"""Customer-facing transaction history derived from ledger journal lines."""

from base64 import b64decode, b64encode
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Customer,
    CustomerAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Payment,
)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


class TransactionResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    payment_id: UUID | None
    account_id: UUID | None
    direction: str
    counterparty_account_id: UUID | None
    amount_minor: int
    currency: str
    occurred_at: datetime
    status: str
    reference: str | None


class TransactionPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[TransactionResponse]
    next_cursor: str | None = None


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = b64decode(cursor.encode()).decode()
        occurred_at_text, line_id = raw.split("|", 1)
        return datetime.fromisoformat(occurred_at_text), UUID(line_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from exc


def _encode_cursor(line: JournalLine, occurred_at: datetime) -> str:
    return b64encode(f"{occurred_at.isoformat()}|{line.id}".encode()).decode()


async def _enrich(session: AsyncSession, rows: list[tuple]) -> list[TransactionResponse]:
    payment_ids: set[UUID] = set()
    journal_ids: set[UUID] = set()
    for _line, entry, _ledger, _account_id in rows:
        journal_ids.add(entry.id)
        reference = entry.business_reference
        if reference.startswith("payment:"):
            try:
                payment_ids.add(UUID(reference.split(":", 1)[1]))
            except ValueError:
                continue
    payments: dict[UUID, Payment] = {}
    if payment_ids:
        payments = {
            payment.id: payment for payment in (await session.scalars(
                select(Payment).where(Payment.id.in_(payment_ids))
            )).all()
        }
    counterparty_by_line: dict[UUID, UUID | None] = {}
    if journal_ids:
        siblings = (await session.execute(
            select(JournalLine, LedgerAccount)
            .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
            .where(JournalLine.journal_id.in_(journal_ids))
        )).all()
        lines_by_journal: dict[UUID, list[UUID | None]] = {}
        for sibling_line, sibling_ledger in siblings:
            lines_by_journal.setdefault(
                sibling_line.journal_id, [],
            ).append(sibling_ledger.account_id)
        for line, entry, _ledger, _account_id in rows:
            others = [
                account_id
                for account_id in lines_by_journal.get(entry.id, [])
                if account_id != _ledger.account_id
            ]
            counterparty_by_line[line.id] = others[0] if others else None
    items = []
    for line, entry, _ledger, account_id in rows:
        payment = payments.get(UUID(entry.business_reference.split(":", 1)[1])) \
            if entry.business_reference.startswith("payment:") else None
        items.append(TransactionResponse(
            id=line.id, payment_id=payment.id if payment else None, account_id=account_id,
            direction="debit" if line.side == "debit" else "credit",
            counterparty_account_id=counterparty_by_line.get(line.id),
            amount_minor=line.amount_minor, currency=entry.currency,
            occurred_at=entry.posted_at, status=payment.status if payment else "completed",
            reference=payment.reference if payment else None,
        ))
    return items


def _base_query(customer: Customer, account_id: UUID | None):
    query = (
        select(JournalLine, JournalEntry, LedgerAccount, CustomerAccount.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(CustomerAccount, CustomerAccount.id == LedgerAccount.account_id)
        .where(
            CustomerAccount.customer_id == customer.id,
            JournalEntry.business_reference.like("payment:%"),
        )
    )
    if account_id is not None:
        query = query.where(LedgerAccount.account_id == account_id)
    return query


@router.get("", response_model=TransactionPage)
async def list_transactions(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    account_id: UUID | None = Query(default=None, alias="accountId"),
    limit: int = Query(default=20, ge=1, le=200),
    cursor: str | None = None,
) -> TransactionPage:
    query = _base_query(customer, account_id)
    if cursor is not None:
        occurred_at, line_id = _decode_cursor(cursor)
        query = query.where(
            (JournalEntry.posted_at < occurred_at)
            | ((JournalEntry.posted_at == occurred_at) & (JournalLine.id < line_id))
        )
    rows = list((await session.execute(
        query.order_by(JournalEntry.posted_at.desc(), JournalLine.id.desc()).limit(limit + 1)
    )).all())
    next_cursor = _encode_cursor(rows[-1][0], rows[-1][1].posted_at) if len(rows) > limit else None
    items = await _enrich(session, rows[:limit])
    return TransactionPage(items=items, next_cursor=next_cursor)


@router.get("/{line_id}", response_model=TransactionResponse)
async def get_transaction(
    line_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    row = (await session.execute(
        _base_query(customer, None).where(JournalLine.id == line_id).limit(1)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return (await _enrich(session, [row]))[0]
