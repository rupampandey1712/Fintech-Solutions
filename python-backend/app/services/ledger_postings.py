"""Double-entry postings shared by payments, holds, refunds, reversals and deposits."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models import JournalEntry, JournalLine, LedgerAccount

# System ledgers have no customer account. One per currency, created on first use.
PAYMENT_HOLDS = "system:payment-holds"
TREASURY = "system:treasury"


async def system_ledger(session: AsyncSession, purpose: str, currency: str) -> LedgerAccount:
    code = f"{purpose}:{currency}"
    # ON CONFLICT keeps concurrent first uses from failing on the unique code.
    await session.execute(
        insert(LedgerAccount)
        .values(id=uuid4(), code=code, currency=currency)
        .on_conflict_do_nothing(index_elements=["code"])
    )
    ledger = await session.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    assert ledger is not None
    return ledger


async def post_transfer(session: AsyncSession, business_reference: str, currency: str,
                        debit_ledger_id: UUID, credit_ledger_id: UUID,
                        amount_minor: int) -> JournalEntry:
    """Post one balanced journal: debit one ledger, credit another, same amount."""
    if amount_minor <= 0:
        raise ValueError("journal amount must be positive")
    journal = JournalEntry(id=uuid4(), business_reference=business_reference, currency=currency)
    session.add(journal)
    await session.flush()
    session.add_all([
        JournalLine(journal_id=journal.id, ledger_account_id=debit_ledger_id, side="debit",
                    amount_minor=amount_minor),
        JournalLine(journal_id=journal.id, ledger_account_id=credit_ledger_id, side="credit",
                    amount_minor=amount_minor),
    ])
    return journal


async def lock_accounts(session: AsyncSession, *account_ids: UUID) -> None:
    """Transaction-scoped advisory locks on customer accounts, always in UUID order.

    Every operation that checks or moves an account balance takes these, so balance checks are
    serialised per account and opposite-direction operations cannot deadlock.
    """
    for account_id in sorted({str(item) for item in account_ids}):
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(account_id, 0))),
        )


async def ledger_balance(session: AsyncSession, ledger_id: UUID) -> int:
    balance = await session.scalar(
        select(func.coalesce(func.sum(case(
            (JournalLine.side == "credit", JournalLine.amount_minor),
            else_=-JournalLine.amount_minor,
        )), 0)).where(JournalLine.ledger_account_id == ledger_id)
    )
    return int(balance or 0)


async def reverse_journal(session: AsyncSession, journal_id: UUID) -> JournalEntry:
    """Post the mirror image of a journal entry (every debit becomes a credit and vice versa).

    The original is never changed or deleted; the reversal points at it through reversal_of_id,
    which is unique, so an entry can be reversed only once even under concurrent requests.
    """
    original = await session.scalar(
        select(JournalEntry).where(JournalEntry.id == journal_id).with_for_update(),
    )
    if original is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if original.reversal_of_id is not None:
        raise HTTPException(status_code=409, detail="reversal_not_reversible")
    already = await session.scalar(
        select(JournalEntry.id).where(JournalEntry.reversal_of_id == original.id),
    )
    if already is not None:
        raise HTTPException(status_code=409, detail="already_reversed")
    lines = (await session.scalars(
        select(JournalLine).where(JournalLine.journal_id == original.id),
    )).all()
    reversal = JournalEntry(id=uuid4(), business_reference=f"reversal:{original.id}",
                            currency=original.currency, reversal_of_id=original.id)
    session.add(reversal)
    await session.flush()
    session.add_all([
        JournalLine(journal_id=reversal.id, ledger_account_id=line.ledger_account_id,
                    side="credit" if line.side == "debit" else "debit",
                    amount_minor=line.amount_minor)
        for line in lines
    ])
    return reversal
