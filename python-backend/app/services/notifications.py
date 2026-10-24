"""Notification dispatch with mock channel providers (architecture spec, section 22).

Every request fans out to all four channels. ``in_app`` is written directly as ``sent``; the
external channels (``email``, ``sms``, ``push``) are written as ``queued`` and a gateway pass
moves them to ``sent`` once the (mock) provider accepts them. The history, including a failed
send, is kept either way.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models import Notification, OutboxEvent
from app.infrastructure.telemetry import current_traceparent

logger = structlog.get_logger()

# in_app is the product's own surface, so it is delivered by writing the row. The rest model
# external providers and go through the queued -> sent lifecycle below.
INSTANT_CHANNELS = ("in_app",)
QUEUED_CHANNELS = ("email", "sms", "push")


class NotificationGateway:
    """Mock email/SMS/push provider. A real implementation would call SendGrid/Twilio/FCM."""

    async def send(self, channel: str, subject: str, body: str) -> str:
        logger.info("notification_sent_to_provider", channel=channel, subject=subject[:80])
        return "sent"


gateway = NotificationGateway()


async def queue_notification(
    session: AsyncSession, customer_id: UUID, event_type: str, subject: str, body: str
) -> list[Notification]:
    """Fan a notification request out to every channel and record the request as an event."""
    now = datetime.now(UTC)
    rows = [
        Notification(
            customer_id=customer_id, channel=channel, event_type=event_type,
            subject=subject[:160], body=body[:1000],
            status="sent" if channel in INSTANT_CHANNELS else "queued",
            sent_at=now if channel in INSTANT_CHANNELS else None,
        )
        for channel in (*INSTANT_CHANNELS, *QUEUED_CHANNELS)
    ]
    session.add_all(rows)
    # The transport-visible record of the request (spec section 17: NotificationRequested).
    # Handlers on other services subscribe to it; the in-process handler ignores it because
    # the fan-out already happened here.
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=customer_id, event_type="notification.requested.v1",
        schema_version=1,
        payload={
            "customerId": str(customer_id), "eventType": event_type,
            "channels": list(QUEUED_CHANNELS),
            "traceContext": current_traceparent(),
        },
    ))
    logger.info(
        "notification_requested", customer_id=str(customer_id), event_type=event_type,
        channels=list(INSTANT_CHANNELS + QUEUED_CHANNELS),
    )
    return rows


async def dispatch_queued(
    session: AsyncSession, customer_id: UUID, event_type: str, batch_limit: int = 20
) -> int:
    """Deliver queued notifications for one event through the gateway (mock: always accepts)."""
    rows = list((await session.scalars(
        select(Notification)
        .where(
            Notification.customer_id == customer_id,
            Notification.event_type == event_type,
            Notification.status == "queued",
            Notification.channel.in_(QUEUED_CHANNELS),
        )
        .order_by(Notification.created_at)
        .limit(batch_limit)
        .with_for_update(skip_locked=True)
    )).all())
    for row in rows:
        row.status = await gateway.send(row.channel, row.subject, row.body)
        row.sent_at = datetime.now(UTC)
    return len(rows)
