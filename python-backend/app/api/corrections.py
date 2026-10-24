"""Refunds and reversals: the two ways money already moved is corrected.

* A **refund** is a new business transaction: the recipient returns some or all of a completed
  payment. It can be partial and repeated up to the original amount, needs the recipient to have
  the funds, and is idempotent per Idempotency-Key.
* A **reversal** corrects a posting that should never have happened. It mirrors the original
  journal entries exactly (never editing or deleting them), is all-or-nothing, and is an
  operational correction, so it does not require the recipient to have the funds.

Both are staff operations (admin or finance_user) and both are audited and announced as events.
"""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import effective_roles, get_current_customer, require_roles
from app.api.payments import create_payment_request_hash
from app.domain.payment_state import PaymentStatus, transition
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Beneficiary,
    Customer,
    CustomerAccount,
    IdempotencyRecord,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    OutboxEvent,
    Payment,
    PaymentRefund,
)
from app.services.audit import record_audit
from app.services.ledger_postings import (
    ledger_balance,
    lock_accounts,
    post_transfer,
    reverse_journal,
)

router = APIRouter(tags=["corrections"])
STAFF_ROLES = ("admin", "finance_user")
REFUND_OPERATION = "refund:create"
PAYMENT_REVERSAL_OPERATION = "payment_reversal:create"
JOURNAL_REVERSAL_OPERATION = "journal_reversal:create"


async def _claim_idempotency(
    session: AsyncSession, actor_id: UUID, operation: str, key: str, request_hash: str,
) -> tuple[UUID | None, IdempotencyRecord | None]:
    """Claim an idempotency key inside the caller's transaction.

    Returns ``(claimed_id, existing)``: exactly one is set. ``claimed_id`` is None when the key
    was already claimed — either by a finished request (``existing.response_json`` holds the
    stored replay) or by one still in flight (response_json is None -> 409).
    """
    claim = await session.execute(
        insert(IdempotencyRecord)
        .values(id=uuid4(), customer_id=actor_id, operation=operation,
                key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=["customer_id", "operation", "key"])
        .returning(IdempotencyRecord.id)
    )
    claimed_id = claim.scalar_one_or_none()
    if claimed_id is not None:
        return claimed_id, None
    existing = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.customer_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.key == key,
    ))
    if existing is None:
        raise HTTPException(status_code=409, detail="request_in_progress")
    if existing.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="idempotency_key_reused")
    if existing.response_json is None:
        raise HTTPException(status_code=409, detail="request_in_progress")
    return None, existing


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RefundRequest(_Camel):
    amount_minor: int = Field(gt=0, le=9_223_372_036_854_775_807)
    reason: str | None = Field(default=None, max_length=200)


class ReversalRequest(_Camel):
    reason: str = Field(min_length=3, max_length=200)


class RefundView(_Camel):
    id: UUID
    payment_id: UUID
    amount_minor: int
    currency: str
    reason: str | None
    journal_id: UUID
    created_at: datetime


class RefundResponse(RefundView):
    refunded_total_minor: int
    remaining_minor: int


class RefundPage(_Camel):
    items: list[RefundView]
    refunded_total_minor: int
    remaining_minor: int


class PaymentReversalResponse(_Camel):
    payment_id: UUID
    status: str
    reversal_journal_ids: list[UUID]


class JournalReversalResponse(_Camel):
    journal_id: UUID
    reversal_journal_id: UUID


class AdminPaymentView(_Camel):
    id: UUID
    status: str
    amount_minor: int
    currency: str
    reference: str | None
    refunded_minor: int
    created_at: datetime


class AdminDepositView(_Camel):
    journal_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    posted_at: datetime
    reversed: bool


def _refund_view(refund: PaymentRefund) -> RefundView:
    return RefundView(id=refund.id, payment_id=refund.payment_id, amount_minor=refund.amount_minor,
                      currency=refund.currency, reason=refund.reason, journal_id=refund.journal_id,
                      created_at=refund.created_at)


async def _refunded_total(session: AsyncSession, payment_id: UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PaymentRefund.amount_minor), 0))
        .where(PaymentRefund.payment_id == payment_id)
    )
    return int(total or 0)


async def _ledger_id(session: AsyncSession, account_id: UUID) -> UUID:
    ledger_id = await session.scalar(
        select(LedgerAccount.id).where(LedgerAccount.account_id == account_id),
    )
    if ledger_id is None:
        raise HTTPException(status_code=409, detail="Account ledger is not configured")
    return ledger_id


async def _destination_account_id(session: AsyncSession, payment: Payment) -> UUID:
    beneficiary = await session.get(Beneficiary, payment.beneficiary_id)
    if beneficiary is None:
        raise HTTPException(status_code=409, detail="Beneficiary no longer exists")
    return beneficiary.destination_account_id


# --------------------------------------------------------------------------------------------
# Refunds


@router.post("/api/v1/payments/{payment_id}/refund", response_model=RefundResponse,
             status_code=status.HTTP_201_CREATED)
async def refund_payment(
    payment_id: UUID,
    request: RefundRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> RefundResponse:
    request_hash = create_payment_request_hash({
        "payment_id": str(payment_id), "amount_minor": request.amount_minor,
        "reason": request.reason,
    })
    claimed_id, existing = await _claim_idempotency(
        session, actor.id, REFUND_OPERATION, idempotency_key, request_hash,
    )
    if claimed_id is None:
        # Replay a committed refund byte-identically instead of refunding twice.
        stored = existing.response_json if existing else None
        if existing is not None and existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="idempotency_key_reused")
        if stored is None:
            raise HTTPException(status_code=409, detail="request_in_progress")
        return RefundResponse.model_validate(stored)

    try:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        destination_account_id = await _destination_account_id(session, payment)
        # Same account locks, in the same order, as payments: refunds and payments touching
        # these accounts serialise, so the recipient's balance check below cannot go stale.
        await lock_accounts(session, payment.source_account_id, destination_account_id)
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="payment_not_refundable")

        refunded = await _refunded_total(session, payment.id)
        if refunded + request.amount_minor > payment.amount_minor:
            raise HTTPException(status_code=422, detail="refund_exceeds_payment")
        destination_ledger = await _ledger_id(session, destination_account_id)
        source_ledger = await _ledger_id(session, payment.source_account_id)
        if await ledger_balance(session, destination_ledger) < request.amount_minor:
            raise HTTPException(status_code=422, detail="recipient_insufficient_funds")

        refund_id = uuid4()
        journal = await post_transfer(session, f"refund:{refund_id}", payment.currency,
                                      destination_ledger, source_ledger, request.amount_minor)
        refund = PaymentRefund(id=refund_id, payment_id=payment.id,
                               amount_minor=request.amount_minor, currency=payment.currency,
                               reason=request.reason, journal_id=journal.id,
                               requested_by=actor.id)
        session.add(refund)
        await session.flush()
        await session.refresh(refund)
        total = refunded + request.amount_minor
        session.add(OutboxEvent(
            id=uuid4(), aggregate_id=payment.id, event_type="payment.refunded.v1",
            schema_version=1,
            payload={
                "customerId": str(payment.customer_id), "paymentId": str(payment.id),
                "refundId": str(refund.id), "amountMinor": request.amount_minor,
                "currency": payment.currency, "refundedTotalMinor": total,
            },
        ))
        response = RefundResponse(**_refund_view(refund).model_dump(),
                                  refunded_total_minor=total,
                                  remaining_minor=payment.amount_minor - total)
        record = await session.get(IdempotencyRecord, claimed_id)
        record.response_json = response.model_dump(mode="json", by_alias=True)
        await record_audit(session, actor.id, "payment.refunded", "payment", payment.id,
                           request=http_request,
                           new_value={"amountMinor": request.amount_minor,
                                      "reason": request.reason, "refundId": str(refund.id)})
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        raise


@router.get("/api/v1/payments/{payment_id}/refunds", response_model=RefundPage)
async def list_refunds(
    payment_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> RefundPage:
    payment = await session.get(Payment, payment_id)
    is_staff = bool(set(effective_roles(customer)) & set(STAFF_ROLES))
    # Same visibility as GET /payments/{id}: the payer, plus staff.
    if payment is None or (payment.customer_id != customer.id and not is_staff):
        raise HTTPException(status_code=404, detail="Payment not found")
    refunds = (await session.scalars(
        select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)
        .order_by(PaymentRefund.created_at)
    )).all()
    total = sum(item.amount_minor for item in refunds)
    return RefundPage(items=[_refund_view(item) for item in refunds], refunded_total_minor=total,
                      remaining_minor=payment.amount_minor - total)


# --------------------------------------------------------------------------------------------
# Reversals


@router.post("/api/v1/payments/{payment_id}/reversal", response_model=PaymentReversalResponse)
async def reverse_payment(
    payment_id: UUID,
    request: ReversalRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> PaymentReversalResponse:
    request_hash = create_payment_request_hash({
        "payment_id": str(payment_id), "reason": request.reason,
    })
    claimed_id, existing = await _claim_idempotency(
        session, actor.id, PAYMENT_REVERSAL_OPERATION, idempotency_key, request_hash,
    )
    if claimed_id is None:
        # Replay a committed reversal byte-identically instead of reversing twice.
        stored = existing.response_json if existing else None
        if existing is not None and existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="idempotency_key_reused")
        if stored is None:
            raise HTTPException(status_code=409, detail="request_in_progress")
        return PaymentReversalResponse.model_validate(stored)

    try:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        destination_account_id = await _destination_account_id(session, payment)
        await lock_accounts(session, payment.source_account_id, destination_account_id)
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if payment.status != PaymentStatus.COMPLETED:
            # Reversing twice, or reversing a held/rejected payment, is not a correction.
            raise HTTPException(status_code=409, detail="payment_not_reversible")
        if await _refunded_total(session, payment.id) > 0:
            raise HTTPException(status_code=409, detail="payment_has_refunds")

        # A direct payment has one journal; a payment released from a fraud hold has two
        # (source -> holds, holds -> beneficiary). Reversing all of them restores every ledger.
        journals = (await session.scalars(
            select(JournalEntry).where(
                or_(JournalEntry.business_reference == f"payment:{payment.id}",
                    JournalEntry.business_reference.like(f"payment:{payment.id}:%")),
                JournalEntry.reversal_of_id.is_(None),
            ).order_by(JournalEntry.posted_at.desc())
        )).all()
        reversal_ids = [(await reverse_journal(session, journal.id)).id for journal in journals]
        payment.status = transition(PaymentStatus.COMPLETED, PaymentStatus.REVERSED).value
        session.add(OutboxEvent(
            id=uuid4(), aggregate_id=payment.id, event_type="payment.reversed.v1",
            schema_version=1,
            payload={
                "customerId": str(payment.customer_id), "paymentId": str(payment.id),
                "amountMinor": payment.amount_minor, "currency": payment.currency,
            },
        ))
        await record_audit(session, actor.id, "payment.reversed", "payment", payment.id,
                           request=http_request, old_value={"status": "completed"},
                           new_value={"status": payment.status, "reason": request.reason,
                                      "reversalJournalIds": [str(i) for i in reversal_ids]})
        response = PaymentReversalResponse(payment_id=payment.id, status=payment.status,
                                           reversal_journal_ids=reversal_ids)
        record = await session.get(IdempotencyRecord, claimed_id)
        record.response_json = response.model_dump(mode="json", by_alias=True)
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        raise


@router.post("/api/v1/admin/journal-entries/{journal_id}/reversal",
             response_model=JournalReversalResponse)
async def reverse_journal_entry(
    journal_id: UUID,
    request: ReversalRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> JournalReversalResponse:
    """Correct a non-payment posting, such as a deposit credited to the wrong account."""
    request_hash = create_payment_request_hash({
        "journal_id": str(journal_id), "reason": request.reason,
    })
    claimed_id, existing = await _claim_idempotency(
        session, actor.id, JOURNAL_REVERSAL_OPERATION, idempotency_key, request_hash,
    )
    if claimed_id is None:
        stored = existing.response_json if existing else None
        if existing is not None and existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="idempotency_key_reused")
        if stored is None:
            raise HTTPException(status_code=409, detail="request_in_progress")
        return JournalReversalResponse.model_validate(stored)

    try:
        journal = await session.get(JournalEntry, journal_id)
        if journal is None:
            raise HTTPException(status_code=404, detail="Journal entry not found")
        if journal.business_reference.startswith(("payment:", "refund:")):
            # Payment journals must go through the payment reversal so the payment status,
            # holds and refunds stay consistent with the ledger.
            raise HTTPException(status_code=409, detail="use_payment_reversal")
        accounts = (await session.scalars(
            select(LedgerAccount.account_id)
            .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id)
            .where(JournalLine.journal_id == journal.id, LedgerAccount.account_id.is_not(None))
        )).all()
        await lock_accounts(session, *accounts)
        reversal = await reverse_journal(session, journal.id)
        await record_audit(session, actor.id, "journal.reversed", "journal_entry", journal.id,
                           request=http_request,
                           new_value={"reason": request.reason, "reversalId": str(reversal.id)})
        response = JournalReversalResponse(journal_id=journal.id, reversal_journal_id=reversal.id)
        record = await session.get(IdempotencyRecord, claimed_id)
        record.response_json = response.model_dump(mode="json", by_alias=True)
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        raise


# --------------------------------------------------------------------------------------------
# Staff lookups for the operations UI


@router.get("/api/v1/admin/customers/{customer_id}/payments")
async def list_customer_payments(
    customer_id: UUID,
    _: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[AdminPaymentView]]:
    refunded = (
        select(PaymentRefund.payment_id, func.sum(PaymentRefund.amount_minor).label("total"))
        .group_by(PaymentRefund.payment_id).subquery()
    )
    rows = (await session.execute(
        select(Payment, func.coalesce(refunded.c.total, 0))
        .join(refunded, refunded.c.payment_id == Payment.id, isouter=True)
        .where(Payment.customer_id == customer_id)
        .order_by(Payment.created_at.desc()).limit(100)
    )).all()
    return {"items": [AdminPaymentView(
        id=payment.id, status=payment.status, amount_minor=payment.amount_minor,
        currency=payment.currency, reference=payment.reference, refunded_minor=int(total),
        created_at=payment.created_at,
    ) for payment, total in rows]}


@router.get("/api/v1/admin/customers/{customer_id}/deposits")
async def list_customer_deposits(
    customer_id: UUID,
    _: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[AdminDepositView]]:
    reversal = select(JournalEntry.reversal_of_id).where(
        JournalEntry.reversal_of_id.is_not(None),
    ).subquery()
    rows = (await session.execute(
        select(JournalEntry, LedgerAccount.account_id, JournalLine.amount_minor,
               reversal.c.reversal_of_id)
        .join(JournalLine, JournalLine.journal_id == JournalEntry.id)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(CustomerAccount, CustomerAccount.id == LedgerAccount.account_id)
        .join(reversal, reversal.c.reversal_of_id == JournalEntry.id, isouter=True)
        .where(CustomerAccount.customer_id == customer_id,
               JournalEntry.business_reference.like("deposit:%"),
               JournalLine.side == "credit")
        .order_by(JournalEntry.posted_at.desc()).limit(100)
    )).all()
    return {"items": [AdminDepositView(
        journal_id=journal.id, account_id=account_id, amount_minor=amount,
        currency=journal.currency, posted_at=journal.posted_at, reversed=reversed_of is not None,
    ) for journal, account_id, amount, reversed_of in rows]}
