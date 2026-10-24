"""Treasury operations: staff-initiated deposits into customer accounts.

Money enters the platform only through here. Each deposit is one balanced journal (debit the
per-currency treasury ledger, credit the customer's ledger), is idempotent per actor and
Idempotency-Key, is audited, and emits ``account.funded.v1`` for the customer notification.
"""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_roles
from app.api.payments import create_payment_request_hash
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Customer,
    CustomerAccount,
    IdempotencyRecord,
    JournalLine,
    LedgerAccount,
    OutboxEvent,
)
from app.services.audit import record_audit
from app.services.ledger_postings import TREASURY, post_transfer, system_ledger

router = APIRouter(prefix="/api/v1/admin", tags=["treasury"])
TREASURY_ROLES = ("admin", "finance_user")
OPERATION = "deposit:create"


class DepositRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    amount_minor: int = Field(gt=0, le=1_000_000_000_00)
    reference: str | None = Field(default=None, max_length=140)


class DepositResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    journal_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    reference: str | None
    balance_minor: int
    posted_at: datetime


class AdminAccountView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    account_type: str
    currency: str
    status: str
    balance_minor: int
    created_at: datetime


class AdminAccountPage(BaseModel):
    items: list[AdminAccountView]


def _balance_expression():
    return func.coalesce(func.sum(case(
        (JournalLine.side == "credit", JournalLine.amount_minor), else_=-JournalLine.amount_minor,
    )), 0)


@router.get("/customers/{customer_id}/accounts", response_model=AdminAccountPage)
async def list_customer_accounts(
    customer_id: UUID,
    _: Customer = Depends(require_roles(*TREASURY_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountPage:
    rows = (await session.execute(
        select(CustomerAccount, _balance_expression())
        .join(LedgerAccount, LedgerAccount.account_id == CustomerAccount.id, isouter=True)
        .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id, isouter=True)
        .where(CustomerAccount.customer_id == customer_id)
        .group_by(CustomerAccount.id)
        .order_by(CustomerAccount.created_at)
    )).all()
    return AdminAccountPage(items=[AdminAccountView(
        id=account.id, account_type=account.account_type, currency=account.currency,
        status=account.status, balance_minor=int(balance), created_at=account.created_at,
    ) for account, balance in rows])


@router.post("/accounts/{account_id}/deposits", response_model=DepositResponse,
             status_code=status.HTTP_201_CREATED)
async def create_deposit(
    account_id: UUID,
    request: DepositRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: Customer = Depends(require_roles(*TREASURY_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DepositResponse:
    request_hash = create_payment_request_hash({
        "account_id": str(account_id), "amount_minor": request.amount_minor,
        "reference": request.reference,
    })
    claim = await session.execute(
        insert(IdempotencyRecord)
        .values(id=uuid4(), customer_id=actor.id, operation=OPERATION, key=idempotency_key,
                request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=["customer_id", "operation", "key"])
        .returning(IdempotencyRecord.id)
    )
    claimed_id = claim.scalar_one_or_none()
    if claimed_id is None:
        existing = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.customer_id == actor.id, IdempotencyRecord.operation == OPERATION,
            IdempotencyRecord.key == idempotency_key,
        ))
        # Read before rollback: rollback expires the instance and async sessions cannot reload it.
        stored_hash = existing.request_hash if existing else None
        stored_response = existing.response_json if existing else None
        await session.rollback()
        if stored_hash is not None and stored_hash != request_hash:
            raise HTTPException(status_code=409, detail="idempotency_key_reused")
        if stored_response is None:
            raise HTTPException(status_code=409, detail="request_in_progress")
        return DepositResponse.model_validate(stored_response)

    try:
        account = await session.scalar(
            select(CustomerAccount).where(CustomerAccount.id == account_id).with_for_update(),
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if account.status != "active":
            raise HTTPException(status_code=409, detail="account_not_active")
        ledger = await session.scalar(
            select(LedgerAccount).where(LedgerAccount.account_id == account.id),
        )
        if ledger is None:
            raise HTTPException(status_code=409, detail="Account ledger is not configured")
        treasury = await system_ledger(session, TREASURY, account.currency)
        journal = await post_transfer(session, f"deposit:{actor.id}:{idempotency_key}",
                                      account.currency, treasury.id, ledger.id,
                                      request.amount_minor)
        await session.flush()
        balance = int(await session.scalar(
            select(_balance_expression()).where(JournalLine.ledger_account_id == ledger.id),
        ))
        await session.refresh(journal)
        response = DepositResponse(
            journal_id=journal.id, account_id=account.id, amount_minor=request.amount_minor,
            currency=account.currency, reference=request.reference, balance_minor=balance,
            posted_at=journal.posted_at,
        )
        session.add(OutboxEvent(
            id=uuid4(), aggregate_id=account.id, event_type="account.funded.v1", schema_version=1,
            payload={
                "customerId": str(account.customer_id), "accountId": str(account.id),
                "journalId": str(journal.id), "amountMinor": request.amount_minor,
                "currency": account.currency,
            },
        ))
        record = await session.get(IdempotencyRecord, claimed_id)
        record.response_json = response.model_dump(mode="json", by_alias=True)
        await record_audit(session, actor.id, "treasury.deposit", "account", account.id,
                           request=http_request,
                           new_value={"amountMinor": request.amount_minor,
                                      "reference": request.reference})
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        raise
