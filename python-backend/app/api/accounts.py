"""Customer-owned account lifecycle and ledger-derived balances."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.config.settings import get_settings
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    AccountLimit,
    Customer,
    CustomerAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    OutboxEvent,
)

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    account_type: Literal["checking", "savings", "wallet", "merchant"] = "wallet"
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class AccountResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    account_type: str
    currency: str
    status: str
    ledger_balance_minor: int
    available_balance_minor: int
    created_at: datetime


class AccountListResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[AccountResponse]
    next_cursor: str | None = None


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    request: CreateAccountRequest,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    settings = get_settings()
    account = CustomerAccount(
        customer_id=customer.id, account_type=request.account_type, currency=request.currency,
    )
    session.add(account)
    await session.flush()
    session.add_all(
        [
            LedgerAccount(
                account_id=account.id,
                code=f"customer:{customer.id}:account:{account.id}",
                currency=account.currency,
            ),
            AccountLimit(
                account_id=account.id,
                per_transaction_minor=settings.default_limit_per_transaction_minor,
                daily_minor=settings.default_limit_daily_minor,
                weekly_minor=settings.default_limit_weekly_minor,
                monthly_minor=settings.default_limit_monthly_minor,
            ),
        ]
    )
    # Spec section 17: AccountCreated, for downstream services (welcome flows, limits
    # provisioning) that must not couple to the accounts endpoint.
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=account.id, event_type="account.created.v1", schema_version=1,
        payload={"customerId": str(customer.id), "accountId": str(account.id),
                 "accountType": account.account_type, "currency": account.currency},
    ))
    await session.commit()
    await session.refresh(account)
    return AccountResponse(
        id=account.id,
        account_type=account.account_type,
        currency=account.currency,
        status=account.status,
        ledger_balance_minor=0,
        available_balance_minor=0,
        created_at=account.created_at,
    )


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> AccountListResponse:
    accounts = list(
        (await session.scalars(
            select(CustomerAccount)
            .where(CustomerAccount.customer_id == customer.id)
            .order_by(CustomerAccount.created_at.desc())
        )).all()
    )
    if not accounts:
        return AccountListResponse(items=[])
    balances = await session.execute(
        select(
            LedgerAccount.account_id,
            func.coalesce(
                func.sum(case((JournalLine.side == "credit", JournalLine.amount_minor),
                             else_=-JournalLine.amount_minor)),
                0,
            ),
        )
        .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(LedgerAccount.account_id.in_([account.id for account in accounts]))
        .group_by(LedgerAccount.account_id)
    )
    balance_by_account = {account_id: int(amount) for account_id, amount in balances.all()}
    items = [
        AccountResponse(
            id=account.id,
            account_type=account.account_type,
            currency=account.currency,
            status=account.status,
            ledger_balance_minor=balance_by_account.get(account.id, 0),
            available_balance_minor=balance_by_account.get(account.id, 0),
            created_at=account.created_at,
        )
        for account in accounts
    ]
    return AccountListResponse(items=items)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    account = await session.scalar(
        select(CustomerAccount).where(
            CustomerAccount.id == account_id,
            CustomerAccount.customer_id == customer.id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    balance = await session.scalar(
        select(
            func.coalesce(
                func.sum(case((JournalLine.side == "credit", JournalLine.amount_minor),
                             else_=-JournalLine.amount_minor)),
                0,
            )
        )
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .where(LedgerAccount.account_id == account.id)
    )
    amount = int(balance or 0)
    return AccountResponse(
        id=account.id,
        account_type=account.account_type,
        currency=account.currency,
        status=account.status,
        ledger_balance_minor=amount,
        available_balance_minor=amount,
        created_at=account.created_at,
    )
