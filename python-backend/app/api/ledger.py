"""Ledger account detail with derived balance and recent entries."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Customer,
    CustomerAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)

router = APIRouter(prefix="/api/v1/ledger", tags=["ledger"])


class LedgerEntryView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    journal_id: UUID
    posted_at: datetime
    business_reference: str
    side: str
    amount_minor: int


class LedgerAccountResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    account_id: UUID
    currency: str
    balance_minor: int
    entries: list[LedgerEntryView]


@router.get("/accounts/{account_id}", response_model=LedgerAccountResponse)
async def get_ledger_account(
    account_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> LedgerAccountResponse:
    account = await session.scalar(
        select(CustomerAccount).where(
            CustomerAccount.id == account_id,
            CustomerAccount.customer_id == customer.id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    ledger_account = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.account_id == account.id)
    )
    if ledger_account is None:
        raise HTTPException(status_code=404, detail="Account ledger is not configured")
    balance = int(await session.scalar(
        select(func.coalesce(func.sum(case(
            (JournalLine.side == "credit", JournalLine.amount_minor),
            else_=-JournalLine.amount_minor,
        )), 0))
        .where(JournalLine.ledger_account_id == ledger_account.id)
    ) or 0)
    rows = (await session.execute(
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalLine.ledger_account_id == ledger_account.id)
        .order_by(JournalEntry.posted_at.desc())
        .limit(200)
    )).all()
    return LedgerAccountResponse(
        account_id=account.id,
        currency=account.currency,
        balance_minor=balance,
        entries=[
            LedgerEntryView(
                journal_id=entry.id, posted_at=entry.posted_at,
                business_reference=entry.business_reference, side=line.side,
                amount_minor=line.amount_minor,
            )
            for line, entry in rows
        ],
    )
