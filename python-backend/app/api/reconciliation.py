"""External reconciliation against the payment provider simulator (architecture spec, section 20).

Internal payments are compared with the provider's own transaction records as two independent
systems of record. Classifications:

* ``matched``           — both sides agree on status and amount.
* ``status_mismatch``   — e.g. a timed-out payment (still ``processing`` internally) that the
  provider actually completed. The run then settles such a payment from the provider's record
  (``app.services.payment_holds.settle_unconfirmed_payment``).
* ``amount_mismatch``   — same status, different amount.
* ``missing_external``  — an internal payment the provider has no record of (held payments
  settled internally never reach the provider).
* ``missing_internal``  — a provider transaction with no internal payment: money that left the
  provider without the platform knowing, which is exactly what reconciliation must catch.

``unmatched`` remains schema-supported for reference-based pairing, which this payment-id
pairing does not need.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from opentelemetry.trace import SpanKind
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, effective_roles, get_current_customer
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    Payment,
    ProviderTransaction,
    ReconciliationRecord,
    ReconciliationRun,
)
from app.infrastructure.telemetry import reconciliation_mismatch_total, tracer
from app.services.payment_holds import settle_unconfirmed_payment

router = APIRouter(prefix="/api/v1/reconciliation", tags=["reconciliation"])

# Payment statuses that participate in reconciliation: everything money could have moved for,
# plus "processing" (a timed-out payment whose provider outcome is unknown).
RECONCILED_STATUSES = ("completed", "failed", "processing")


class ReconciliationRecordView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    payment_id: UUID
    status: str
    internal_status: str
    external_status: str | None
    internal_amount_minor: int
    external_amount_minor: int | None
    external_reference: str | None


class ReconciliationRunResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    run_id: UUID
    matched: int
    amount_mismatch: int
    status_mismatch: int
    missing_external: int
    missing_internal: int
    records: list[ReconciliationRecordView]


class ReconciliationRunView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    started_at: datetime
    finished_at: datetime | None
    matched: int
    amount_mismatch: int
    status_mismatch: int
    missing_external: int
    missing_internal: int


class ReconciliationRunPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[ReconciliationRunView]


def _require_finance_role(customer: Customer) -> None:
    roles = effective_roles(customer)
    # Operations runs reconciliation as day-to-day work; compliance reviews the results;
    # finance owns them.
    if not ({"admin", "finance_user", "operations_user", "compliance_officer"} & set(roles)):
        raise HTTPException(status_code=403, detail="forbidden")


def _classify(payment: Payment | None, external: dict | None) -> tuple[str, str | None, int | None]:
    """Compare one internal payment with one provider record (either side may be absent)."""
    if payment is None or external is None:
        # missing_internal keeps the provider's numbers (they are the only evidence);
        # missing_external has nothing external to show.
        if external is not None:
            return "missing_internal", external["status"], external["amountMinor"]
        return "missing_external", None, None
    external_status = external["status"]
    external_amount = external["amountMinor"]
    if payment.status != external_status:
        return "status_mismatch", external_status, external_amount
    if external_amount != payment.amount_minor:
        return "amount_mismatch", external_status, external_amount
    return "matched", external_status, external_amount


async def execute_reconciliation_run(session: AsyncSession) -> ReconciliationRunResponse:
    """One reconciliation pass, shared by the API endpoint and the scheduled worker."""
    run = ReconciliationRun()
    session.add(run)
    await session.flush()
    payments = list((await session.scalars(
        select(Payment).where(Payment.status.in_(RECONCILED_STATUSES))
    )).all())
    provider_records = {
        record.payment_id: record
        for record in (await session.scalars(select(ProviderTransaction))).all()
    }
    # A payment that exists internally in ANY state (e.g. reversed or rejected after the
    # provider accepted it) is accounted for; only provider records with no internal payment
    # at all are genuinely "missing internal".
    known_payment_ids = set((await session.scalars(select(Payment.id))).all())

    records: list[ReconciliationRecord] = []
    views: list[ReconciliationRecordView] = []

    async def classify(payment: Payment | None, external: dict | None,
                       payment_id: UUID) -> None:
        # The provider is the "external system" hop of a distributed trace (spec section 30).
        with tracer.start_as_current_span(
            "provider lookup", kind=SpanKind.CLIENT,
            attributes={"fincore.payment.id": str(payment_id)},
        ):
            classification, external_status, external_amount = _classify(payment, external)
        if classification != "matched":
            reconciliation_mismatch_total.add(1, {"type": classification})
        if classification == "matched":
            run.matched += 1
        elif classification == "amount_mismatch":
            run.amount_mismatch += 1
        elif classification == "status_mismatch":
            run.status_mismatch += 1
        elif classification == "missing_external":
            run.missing_external += 1
        else:
            run.missing_internal += 1
        record = ReconciliationRecord(
            run_id=run.id, payment_id=payment_id,
            external_reference=(external or {}).get("reference"),
            internal_status=payment.status if payment else "missing",
            external_status=external_status,
            internal_amount_minor=payment.amount_minor if payment else 0,
            external_amount_minor=external_amount,
            status=classification,
        )
        records.append(record)
        views.append(ReconciliationRecordView(
            payment_id=payment_id, status=classification,
            internal_status=record.internal_status, external_status=external_status,
            internal_amount_minor=record.internal_amount_minor,
            external_amount_minor=external_amount,
            external_reference=record.external_reference,
        ))

    unconfirmed: list[tuple[UUID, str, int]] = []
    for payment in payments:
        record = provider_records.get(payment.id)
        external = (
            {"reference": record.reference, "status": record.status,
             "amountMinor": record.amount_minor}
            if record is not None else None
        )
        await classify(payment, external, payment.id)
        if payment.status == "processing" and record is not None:
            unconfirmed.append((payment.id, record.status, record.amount_minor))
    # Provider transactions with no internal payment at all: the external ledger is the only
    # evidence these transfers ever happened.
    for record in provider_records.values():
        if record.payment_id not in known_payment_ids:
            await classify(None, {
                "reference": record.reference, "status": record.status,
                "amountMinor": record.amount_minor,
            }, record.payment_id)

    # The records above keep what reconciliation found. A timed-out payment is then settled
    # from the provider's answer, so its held funds reach the beneficiary or return home.
    for payment_id, provider_status, provider_amount in unconfirmed:
        await settle_unconfirmed_payment(session, payment_id, provider_status, provider_amount)

    session.add_all(records)
    run.finished_at = datetime.now(UTC)
    await session.commit()
    return ReconciliationRunResponse(
        run_id=run.id, matched=run.matched, amount_mismatch=run.amount_mismatch,
        status_mismatch=run.status_mismatch, missing_external=run.missing_external,
        missing_internal=run.missing_internal, records=views,
    )


@router.post("/run", response_model=ReconciliationRunResponse)
async def run_reconciliation(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationRunResponse:
    _require_finance_role(customer)
    return await execute_reconciliation_run(session)


@router.get("/runs", response_model=ReconciliationRunPage)
async def list_runs(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> ReconciliationRunPage:
    _require_finance_role(customer)
    rows = (await session.scalars(
        select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc()).limit(limit)
    )).all()
    return ReconciliationRunPage(items=[ReconciliationRunView(
        id=row.id, started_at=row.started_at, finished_at=row.finished_at,
        matched=row.matched, amount_mismatch=row.amount_mismatch,
        status_mismatch=row.status_mismatch, missing_external=row.missing_external,
        missing_internal=row.missing_internal,
    ) for row in rows])


@router.get("/runs/{run_id}", response_model=ReconciliationRunResponse)
async def get_run(
    run_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationRunResponse:
    _require_finance_role(customer)
    run = await session.get(ReconciliationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    rows = (await session.scalars(
        select(ReconciliationRecord).where(ReconciliationRecord.run_id == run.id)
        .order_by(ReconciliationRecord.created_at.asc())
    )).all()
    return ReconciliationRunResponse(
        run_id=run.id, matched=run.matched, amount_mismatch=run.amount_mismatch,
        status_mismatch=run.status_mismatch, missing_external=run.missing_external,
        missing_internal=run.missing_internal,
        records=[ReconciliationRecordView(
            payment_id=row.payment_id, status=row.status, internal_status=row.internal_status,
            external_status=row.external_status, internal_amount_minor=row.internal_amount_minor,
            external_amount_minor=row.external_amount_minor,
            external_reference=row.external_reference,
        ) for row in rows],
    )
