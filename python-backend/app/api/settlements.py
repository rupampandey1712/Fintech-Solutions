"""Settlement batches (architecture spec, section 58).

    Transactions during day
            ↓
    Settlement Batch
            ↓
    Calculate Net Amount
            ↓
    Generate Settlement File
            ↓
    Settlement Status

A batch groups the provider-settled completed payments of one business date and currency.
``process`` moves it pending -> processing -> completed (or failed), computes the net payout
(gross minus a deterministic per-payment fee), renders the payout file, and marks every
included payment so it can never be settled twice (unique index on the batch item's payment).
"""

import csv
import io
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, require_roles
from app.config.settings import get_settings
from app.infrastructure.database import get_session
from app.infrastructure.models import OutboxEvent, Payment, SettlementBatch, SettlementBatchItem
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/admin/settlement-batches", tags=["settlement"])
# Finance owns settlement; operations may create the daily batch.
STAFF_ROLES = ("admin", "finance_user", "operations_user")


class CreateBatchRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class SettlementItemView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    payment_id: UUID
    amount_minor: int
    fee_minor: int


class SettlementBatchView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    business_date: str
    currency: str
    status: str
    total_minor: int
    fee_minor: int
    net_minor: int
    payment_count: int
    failure_reason: str | None
    processed_at: datetime | None
    created_at: datetime


class SettlementBatchDetail(SettlementBatchView):
    items: list[SettlementItemView]


class SettlementBatchPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[SettlementBatchView]


def _fee_for(amount_minor: int) -> int:
    """Deterministic settlement fee in minor units (configurable basis points)."""
    return amount_minor * get_settings().settlement_fee_bps // 10_000


def _view(batch: SettlementBatch) -> SettlementBatchView:
    return SettlementBatchView(
        id=batch.id,
        business_date=batch.business_date.strftime("%Y-%m-%d"),
        currency=batch.currency, status=batch.status,
        total_minor=batch.total_minor, fee_minor=batch.fee_minor,
        net_minor=batch.total_minor - batch.fee_minor,
        payment_count=batch.payment_count, failure_reason=batch.failure_reason,
        processed_at=batch.processed_at, created_at=batch.created_at,
    )


def _settlement_file(batch: SettlementBatch, items: list[SettlementBatchItem]) -> str:
    """Render the payout file: a header line plus one row per payment."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["batch_id", "business_date", "currency", "payment_id",
                     "amount_minor", "fee_minor", "net_amount_minor"])
    for item in items:
        writer.writerow([
            str(batch.id), batch.business_date.strftime("%Y-%m-%d"), batch.currency,
            str(item.payment_id), item.amount_minor, item.fee_minor,
            item.amount_minor - item.fee_minor,
        ])
    return buffer.getvalue()


def _payment_event(event_type: str, batch: SettlementBatch, **extra) -> OutboxEvent:
    payload = {
        "settlementBatchId": str(batch.id), "businessDate":
            batch.business_date.strftime("%Y-%m-%d"),
        "currency": batch.currency, "status": batch.status,
        "totalMinor": batch.total_minor, "netMinor": batch.total_minor - batch.fee_minor,
        "paymentCount": batch.payment_count,
        "customerId": str(batch.created_by),
    }
    payload.update(extra)
    return OutboxEvent(
        id=uuid4(), aggregate_id=batch.id, event_type=event_type, schema_version=1,
        payload=payload,
    )


@router.post("", response_model=SettlementBatchDetail, status_code=201)
async def create_batch(
    request: CreateBatchRequest,
    http_request: Request,
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> SettlementBatchDetail:
    """Collect one business day's provider-settled, not-yet-settled payments into a batch."""
    try:
        business_date = datetime.strptime(request.business_date, "%Y-%m-%d").replace(tzinfo=UTC)
        day_end = business_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        # Only payments that actually went through the provider are settleable; a payment
        # already in a batch is excluded by the unique index on settlement_batch_items.
        settled_ids = select(SettlementBatchItem.payment_id).subquery()
        payments = list((await session.scalars(
            select(Payment)
            .where(
                Payment.status == "completed",
                Payment.currency == request.currency,
                Payment.provider_reference.is_not(None),
                Payment.created_at >= business_date,
                Payment.created_at <= day_end,
                Payment.id.not_in(select(settled_ids.c.payment_id)),
            )
            .order_by(Payment.created_at)
        )).all())
        if not payments:
            raise HTTPException(
                status_code=422, detail="no_settleable_payments_for_date",
            )

        batch = SettlementBatch(
            id=uuid4(), business_date=business_date, currency=request.currency,
            status="pending", created_by=actor.id,
        )
        session.add(batch)
        await session.flush()
        items = []
        total = 0
        fees = 0
        for payment in payments:
            fee = _fee_for(payment.amount_minor)
            items.append(SettlementBatchItem(
                id=uuid4(), batch_id=batch.id, payment_id=payment.id,
                amount_minor=payment.amount_minor, fee_minor=fee,
            ))
            total += payment.amount_minor
            fees += fee
        session.add_all(items)
        batch.total_minor = total
        batch.fee_minor = fees
        batch.payment_count = len(items)
        await record_audit(session, actor.id, "settlement.batch_created", "settlement_batch",
                           batch.id, request=http_request,
                           new_value={"businessDate": request.business_date,
                                      "currency": request.currency,
                                      "paymentCount": len(items), "totalMinor": total})
        await session.commit()
        await session.refresh(batch)
        return SettlementBatchDetail(
            **_view(batch).model_dump(),
            items=[SettlementItemView(payment_id=item.payment_id, amount_minor=item.amount_minor,
                                      fee_minor=item.fee_minor) for item in items],
        )
    except Exception:
        await session.rollback()
        raise


@router.post("/{batch_id}/process", response_model=SettlementBatchDetail)
async def process_batch(
    batch_id: UUID,
    http_request: Request,
    actor: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> SettlementBatchDetail:
    """Process a pending batch: calculate the net amount, generate the file, set the status."""
    try:
        batch = await session.scalar(
            select(SettlementBatch).where(SettlementBatch.id == batch_id).with_for_update()
        )
        if batch is None:
            raise HTTPException(status_code=404, detail="Settlement batch not found")
        if batch.status != "pending":
            raise HTTPException(status_code=409, detail="settlement_batch_not_pending")
        batch.status = "processing"
        items = list((await session.scalars(
            select(SettlementBatchItem).where(SettlementBatchItem.batch_id == batch.id)
            .order_by(SettlementBatchItem.created_at)
        )).all())
        if not items:
            # Defensive: an empty batch must fail loudly rather than settle nothing silently.
            batch.status = "failed"
            batch.failure_reason = "Settlement batch has no payments"
        else:
            batch.file_content = _settlement_file(batch, items)
            batch.status = "completed"
            batch.processed_at = datetime.now(UTC)
        session.add(_payment_event(
            f"settlement.{batch.status}.v1", batch,
        ))
        await record_audit(session, actor.id, f"settlement.batch_{batch.status}",
                           "settlement_batch", batch.id, request=http_request,
                           old_value={"status": "pending"},
                           new_value={"status": batch.status,
                                      "netMinor": batch.total_minor - batch.fee_minor})
        await session.commit()
        await session.refresh(batch)
        return SettlementBatchDetail(
            **_view(batch).model_dump(),
            items=[SettlementItemView(payment_id=item.payment_id, amount_minor=item.amount_minor,
                                      fee_minor=item.fee_minor) for item in items],
        )
    except Exception:
        await session.rollback()
        raise


@router.get("", response_model=SettlementBatchPage)
async def list_batches(
    customer: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> SettlementBatchPage:
    rows = (await session.scalars(
        select(SettlementBatch).order_by(SettlementBatch.created_at.desc()).limit(limit)
    )).all()
    return SettlementBatchPage(items=[_view(row) for row in rows])


@router.get("/{batch_id}", response_model=SettlementBatchDetail)
async def get_batch(
    batch_id: UUID,
    customer: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> SettlementBatchDetail:
    batch = await session.get(SettlementBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Settlement batch not found")
    items = list((await session.scalars(
        select(SettlementBatchItem).where(SettlementBatchItem.batch_id == batch.id)
        .order_by(SettlementBatchItem.created_at)
    )).all())
    return SettlementBatchDetail(
        **_view(batch).model_dump(),
        items=[SettlementItemView(payment_id=item.payment_id, amount_minor=item.amount_minor,
                                  fee_minor=item.fee_minor) for item in items],
    )


@router.get("/{batch_id}/file")
async def get_batch_file(
    batch_id: UUID,
    customer: Customer = Depends(require_roles(*STAFF_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    """Download the generated settlement file (CSV)."""
    batch = await session.get(SettlementBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Settlement batch not found")
    if batch.status != "completed" or batch.file_content is None:
        raise HTTPException(status_code=409, detail="settlement_file_not_available")
    filename = f"settlement-{batch.business_date.strftime('%Y%m%d')}-{batch.id}.csv"
    return Response(
        content=batch.file_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
