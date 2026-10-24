"""Customer notification feed."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.infrastructure.database import get_session
from app.infrastructure.models import Customer, Notification

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    channel: str
    event_type: str
    subject: str
    body: str
    status: str
    sent_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[NotificationView]
    next_cursor: str | None = None


@router.get("", response_model=NotificationPage)
async def list_notifications(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> NotificationPage:
    rows = (await session.scalars(
        select(Notification).where(Notification.customer_id == customer.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    )).all()
    return NotificationPage(items=[
        NotificationView(
            id=row.id, channel=row.channel, event_type=row.event_type, subject=row.subject,
            body=row.body, status=row.status, sent_at=row.sent_at, created_at=row.created_at,
        )
        for row in rows
    ])
