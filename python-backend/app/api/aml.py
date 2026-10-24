"""Anti-money-laundering alert feed and review workflow."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, effective_roles, get_current_customer, require_roles
from app.infrastructure.database import get_session
from app.infrastructure.models import AmlAlert
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/aml", tags=["aml"])

# Compliance owns the alert queue; fraud analysts and admins work it day to day.
REVIEW_ROLES = ("admin", "fraud_analyst", "compliance_officer")


class AmlAlertView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    customer_id: UUID
    payment_id: UUID | None
    rule_code: str
    status: str
    details: dict[str, Any] | None
    created_at: datetime


class AmlAlertPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[AmlAlertView]
    next_cursor: str | None = None


@router.get("/alerts", response_model=AmlAlertPage)
async def list_alerts(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> AmlAlertPage:
    roles = effective_roles(customer)
    query = select(AmlAlert)
    if not ({"admin", "fraud_analyst", "compliance_officer"} & set(roles)):
        query = query.where(AmlAlert.customer_id == customer.id)
    rows = (await session.scalars(query.order_by(AmlAlert.created_at.desc()).limit(limit))).all()
    return AmlAlertPage(items=[
        AmlAlertView(
            id=row.id, customer_id=row.customer_id, payment_id=row.payment_id,
            rule_code=row.rule_code, status=row.status, details=row.details,
            created_at=row.created_at,
        )
        for row in rows
    ])


class UpdateAlertStatusRequest(BaseModel):
    status: Literal["open", "investigating", "escalated", "closed", "false_positive"]
    note: str | None = Field(default=None, max_length=300)


class AmlAlertStatusResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    status: str


@router.patch("/alerts/{alert_id}/status", response_model=AmlAlertStatusResponse)
async def update_alert_status(
    alert_id: UUID,
    request: UpdateAlertStatusRequest,
    http_request: Request,
    reviewer: Customer = Depends(require_roles(*REVIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AmlAlertStatusResponse:
    """Progress an alert through its lifecycle: open -> investigating -> escalated -> closed."""
    alert = await session.get(AmlAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="AML alert not found")
    old_status = alert.status
    alert.status = request.status
    details = dict(alert.details or {})
    if request.note:
        history = list(details.get("statusHistory") or [])
        history.append({
            "status": request.status, "note": request.note[:300],
            "by": str(reviewer.id), "at": datetime.now(UTC).isoformat(),
        })
        details["statusHistory"] = history
        alert.details = details
    await record_audit(session, reviewer.id, "aml.alert.status_changed", "aml_alert", alert.id,
                       request=http_request, old_value={"status": old_status},
                       new_value={"status": alert.status})
    await session.commit()
    return AmlAlertStatusResponse(id=alert.id, status=alert.status)
