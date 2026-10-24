"""Chargeback simulation (architecture spec, section 61).

Lifecycle: transaction -> dispute -> investigation (evidence) -> decision -> won/lost.

* A **customer** opens a dispute on their own completed payment; one dispute per payment.
* **Staff** add evidence entries (each moves an open dispute to ``under_review``) and decide.
* ``won`` closes the dispute in the customer's favour of the original transfer (no money
  movement); ``lost`` upholds the chargeback: the payment's journals are mirrored exactly like
  an operational reversal (never edited or deleted), so the funds return to the payer.

The workflow is staff-gated for evidence and decisions (admin, fraud analyst, finance), audited
end to end, and announced as events.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, effective_roles, get_current_customer, require_roles
from app.domain.payment_state import PaymentStatus, transition
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Beneficiary,
    JournalEntry,
    OutboxEvent,
    Payment,
    PaymentDispute,
)
from app.services.audit import record_audit
from app.services.ledger_postings import lock_accounts, reverse_journal

router = APIRouter(tags=["disputes"])
STAFF_ROLES = ("admin", "fraud_analyst", "finance_user")
DECISION_ROLES = ("admin", "fraud_analyst")
EVIDENCE_LIMIT = 20


class DisputeCreateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class EvidenceRequest(BaseModel):
    note: str = Field(min_length=3, max_length=300)


class DecisionRequest(BaseModel):
    outcome: str = Field(pattern=r"^(won|lost)$")
    resolution: str | None = Field(default=None, max_length=200)


class EvidenceView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    note: str
    added_by: str
    added_at: str


class DisputeView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    payment_id: UUID
    customer_id: UUID
    status: str
    reason: str
    evidence: list[EvidenceView]
    decided_by: UUID | None
    decided_at: datetime | None
    resolution: str | None
    created_at: datetime


class DisputePage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[DisputeView]
    next_cursor: str | None = None


def _view(dispute: PaymentDispute) -> DisputeView:
    return DisputeView(
        id=dispute.id, payment_id=dispute.payment_id, customer_id=dispute.customer_id,
        status=dispute.status, reason=dispute.reason,
        evidence=[EvidenceView(**entry) for entry in (dispute.evidence or [])],
        decided_by=dispute.decided_by, decided_at=dispute.decided_at,
        resolution=dispute.resolution, created_at=dispute.created_at,
    )


def _event(event_type: str, dispute: PaymentDispute, payment: Payment, **extra) -> OutboxEvent:
    payload = {
        "disputeId": str(dispute.id), "paymentId": str(payment.id),
        "customerId": str(dispute.customer_id), "status": dispute.status,
        "amountMinor": payment.amount_minor, "currency": payment.currency,
    }
    payload.update(extra)
    return OutboxEvent(
        id=uuid4(), aggregate_id=dispute.id, event_type=event_type, schema_version=1,
        payload=payload,
    )


async def _destination_account_id(session: AsyncSession, payment: Payment) -> UUID:
    beneficiary = await session.get(Beneficiary, payment.beneficiary_id)
    if beneficiary is None:
        raise HTTPException(status_code=409, detail="Beneficiary no longer exists")
    return beneficiary.destination_account_id


@router.post("/api/v1/payments/{payment_id}/dispute", response_model=DisputeView,
             status_code=status.HTTP_201_CREATED)
async def open_dispute(
    payment_id: UUID,
    request: DisputeCreateRequest,
    http_request: Request,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> DisputeView:
    """The payer disputes a completed payment; a reviewer investigates with staff."""
    try:
        payment = await session.scalar(select(Payment).where(
            Payment.id == payment_id, Payment.customer_id == customer.id,
        ))
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="payment_not_disputable")
        existing = await session.scalar(
            select(PaymentDispute).where(PaymentDispute.payment_id == payment.id)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="payment_already_disputed")

        dispute = PaymentDispute(
            id=uuid4(), payment_id=payment.id, customer_id=customer.id,
            status="open", reason=request.reason, evidence=[],
        )
        session.add(dispute)
        await session.flush()
        await session.refresh(dispute)
        session.add(_event("payment.dispute.opened.v1", dispute, payment, reason=request.reason))
        await record_audit(session, customer.id, "payment.dispute.opened", "dispute", dispute.id,
                           request=http_request,
                           new_value={"paymentId": str(payment.id), "reason": request.reason})
        await session.commit()
        await session.refresh(dispute)
        return _view(dispute)
    except Exception:
        await session.rollback()
        raise


@router.get("/api/v1/payments/{payment_id}/dispute", response_model=DisputeView)
async def get_dispute(
    payment_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> DisputeView:
    payment = await session.get(Payment, payment_id)
    is_staff = bool(set(effective_roles(customer)) & set(STAFF_ROLES))
    # Same visibility as GET /payments/{id}: the payer, plus staff.
    if payment is None or (payment.customer_id != customer.id and not is_staff):
        raise HTTPException(status_code=404, detail="Payment not found")
    dispute = await session.scalar(
        select(PaymentDispute).where(PaymentDispute.payment_id == payment.id)
    )
    if dispute is None:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return _view(dispute)


@router.get("/api/v1/admin/disputes", response_model=DisputePage)
async def list_disputes(
    customer: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> DisputePage:
    query = select(PaymentDispute)
    if status_filter is not None:
        query = query.where(PaymentDispute.status == status_filter)
    rows = (await session.scalars(
        query.order_by(PaymentDispute.created_at.desc()).limit(limit)
    )).all()
    return DisputePage(items=[_view(row) for row in rows])


@router.post("/api/v1/admin/disputes/{dispute_id}/evidence", response_model=DisputeView)
async def add_evidence(
    dispute_id: UUID,
    request: EvidenceRequest,
    http_request: Request,
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DisputeView:
    """Investigation step: append an evidence note; the first one starts the review."""
    try:
        dispute = await session.scalar(
            select(PaymentDispute).where(PaymentDispute.id == dispute_id).with_for_update()
        )
        if dispute is None:
            raise HTTPException(status_code=404, detail="Dispute not found")
        if dispute.status in {"won", "lost"}:
            raise HTTPException(status_code=409, detail="dispute_already_decided")
        entries = list(dispute.evidence or [])
        if len(entries) >= EVIDENCE_LIMIT:
            raise HTTPException(status_code=422, detail="evidence_limit_reached")
        entries.append({
            "note": request.note, "addedBy": str(actor.id),
            "addedAt": datetime.now(UTC).isoformat(),
        })
        dispute.evidence = entries
        old_status = dispute.status
        if dispute.status == "open":
            dispute.status = "under_review"
        await record_audit(session, actor.id, "dispute.evidence_added", "dispute", dispute.id,
                           request=http_request, old_value={"status": old_status},
                           new_value={"status": dispute.status, "evidenceCount": len(entries)})
        await session.commit()
        await session.refresh(dispute)
        return _view(dispute)
    except Exception:
        await session.rollback()
        raise


@router.post("/api/v1/admin/disputes/{dispute_id}/decision", response_model=DisputeView)
async def decide_dispute(
    dispute_id: UUID,
    request: DecisionRequest,
    http_request: Request,
    actor: Customer = Depends(require_roles(*DECISION_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DisputeView:
    """Decide the chargeback. A lost dispute mirrors the payment's journals (funds return)."""
    try:
        dispute = await session.scalar(
            select(PaymentDispute).where(PaymentDispute.id == dispute_id).with_for_update()
        )
        if dispute is None:
            raise HTTPException(status_code=404, detail="Dispute not found")
        if dispute.status in {"won", "lost"}:
            raise HTTPException(status_code=409, detail="dispute_already_decided")
        payment = await session.get(Payment, dispute.payment_id)
        if payment is None:
            raise HTTPException(status_code=409, detail="Payment no longer exists")

        dispute.status = request.outcome
        dispute.decided_by = actor.id
        dispute.decided_at = datetime.now(UTC)
        dispute.resolution = request.resolution or (
            "Chargeback upheld: funds returned to the payer" if request.outcome == "lost"
            else "Chargeback rejected: original transfer stands"
        )

        reversal_ids: list[UUID] = []
        if request.outcome == "lost":
            destination_account_id = await _destination_account_id(session, payment)
            await lock_accounts(session, payment.source_account_id, destination_account_id)
            payment = await session.scalar(
                select(Payment).where(Payment.id == payment.id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if payment.status != PaymentStatus.COMPLETED:
                # Already reversed (or still held): there is nothing left to charge back.
                raise HTTPException(status_code=409, detail="payment_not_reversible")
            journals = (await session.scalars(
                select(JournalEntry).where(
                    or_(JournalEntry.business_reference == f"payment:{payment.id}",
                        JournalEntry.business_reference.like(f"payment:{payment.id}:%")),
                    JournalEntry.reversal_of_id.is_(None),
                ).order_by(JournalEntry.posted_at.desc())
            )).all()
            reversal_ids = [(await reverse_journal(session, journal.id)).id
                            for journal in journals]
            payment.status = transition(PaymentStatus.COMPLETED, PaymentStatus.REVERSED).value

        session.add(_event(
            "payment.dispute.decided.v1", dispute, payment,
            outcome=request.outcome,
            reversalJournalIds=[str(i) for i in reversal_ids],
        ))
        await record_audit(session, actor.id, "payment.dispute.decided", "dispute", dispute.id,
                           request=http_request,
                           new_value={"outcome": request.outcome,
                                      "resolution": dispute.resolution})
        await session.commit()
        await session.refresh(dispute)
        return _view(dispute)
    except Exception:
        await session.rollback()
        raise
