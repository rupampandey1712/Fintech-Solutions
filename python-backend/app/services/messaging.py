"""Azure Service Bus transport for outbox events.

The outbox worker publishes each committed outbox row to a topic. A subscription consumer
applies the event handlers. Delivery is at-least-once, so the consumer records every message
id in ``inbox_messages`` inside the same transaction as the handler's writes; a redelivered
message is then acknowledged without repeating its side effects. Messages that keep failing
are moved to the subscription's dead-letter queue by Service Bus after MaxDeliveryCount.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.infrastructure.database import SessionFactory
from app.infrastructure.models import InboxMessage, OutboxEvent
from app.infrastructure.telemetry import (
    context_from,
    current_traceparent,
    service_bus_messages_failed,
    service_bus_messages_processed,
    tracer,
)

logger = structlog.get_logger()

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]


class ServiceBusPublisher:
    def __init__(self, settings: Settings) -> None:
        from azure.servicebus.aio import ServiceBusClient

        self._client = ServiceBusClient.from_connection_string(
            settings.servicebus_connection_string.get_secret_value(),
        )
        self._sender = self._client.get_topic_sender(settings.servicebus_topic)

    async def publish(self, event: OutboxEvent) -> None:
        from azure.servicebus import ServiceBusMessage

        # MessageId = outbox id lets the topic's duplicate detection drop a re-publish that
        # happens when the process dies between sending and marking the row published.
        await self._sender.send_messages(ServiceBusMessage(
            json.dumps(event.payload),
            message_id=str(event.id),
            subject=event.event_type,
            content_type="application/json",
            application_properties={
                "eventType": event.event_type,
                "schemaVersion": event.schema_version,
                "aggregateId": str(event.aggregate_id),
                # The dispatcher's span, so the consumer continues the same trace.
                "traceparent": current_traceparent() or "",
            },
        ))

    async def close(self) -> None:
        await self._sender.close()
        await self._client.close()


async def apply_once(session: AsyncSession, consumer: str, message_id: str, event_type: str,
                     payload: dict[str, Any], handlers: dict[str, Handler]) -> bool:
    """Run the handler for one message exactly once per consumer. Returns False for duplicates."""
    claimed = await session.execute(
        insert(InboxMessage)
        .values(consumer=consumer, message_id=message_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["consumer", "message_id"])
        .returning(InboxMessage.message_id)
    )
    if claimed.scalar_one_or_none() is None:
        await session.rollback()
        return False
    handler = handlers.get(event_type)
    if handler is not None:
        await handler(session, payload)
    else:
        logger.info("message_ignored", event_type=event_type, message_id=message_id)
    await session.commit()
    return True


async def run_consumer(settings: Settings, handlers: dict[str, Handler]) -> None:
    """Receive from the subscription forever, reconnecting with backoff if the broker drops."""
    from azure.servicebus import ServiceBusReceiveMode
    from azure.servicebus.aio import ServiceBusClient

    consumer = settings.servicebus_subscription
    backoff = 1.0
    logger.info("servicebus_consumer_started", topic=settings.servicebus_topic,
                subscription=consumer)
    while True:
        try:
            async with ServiceBusClient.from_connection_string(
                settings.servicebus_connection_string.get_secret_value(),
            ) as client, client.get_subscription_receiver(
                settings.servicebus_topic, consumer,
                receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
            ) as receiver:
                backoff = 1.0
                while True:
                    messages = await receiver.receive_messages(
                        max_message_count=20, max_wait_time=5,
                    )
                    for message in messages:
                        await _process(receiver, message, consumer, handlers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("servicebus_consumer_disconnected", error_type=type(exc).__name__,
                           error=str(exc), retry_in_seconds=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _traceparent(message) -> str | None:
    for key, value in (message.application_properties or {}).items():
        name = key.decode() if isinstance(key, bytes) else key
        if name == "traceparent":
            text = value.decode() if isinstance(value, bytes) else str(value)
            return text or None
    return None


async def _process(receiver, message, consumer: str, handlers: dict[str, Handler]) -> None:
    event_type = message.subject or ""
    with tracer.start_as_current_span(
        f"servicebus process {event_type}", context=context_from(_traceparent(message)),
        kind=SpanKind.CONSUMER,
        attributes={"messaging.system": "servicebus",
                    "messaging.message.id": str(message.message_id),
                    "messaging.destination.subscription.name": consumer,
                    "fincore.delivery_count": message.delivery_count or 0},
    ) as span:
        await _process_in_span(receiver, message, consumer, handlers, event_type, span)


async def _process_in_span(receiver, message, consumer: str, handlers: dict[str, Handler],
                           event_type: str, span) -> None:
    try:
        payload = json.loads(str(message))
    except ValueError:
        # Retrying cannot fix a malformed body, so dead-letter it immediately.
        logger.error("message_malformed", event_type=event_type, message_id=str(message.message_id))
        await receiver.dead_letter_message(message, reason="malformed_payload")
        service_bus_messages_failed.add(1, {"event_type": event_type, "reason": "malformed"})
        return
    try:
        async with SessionFactory() as session:
            applied = await apply_once(session, consumer, str(message.message_id), event_type,
                                       payload, handlers)
        await receiver.complete_message(message)
        service_bus_messages_processed.add(1, {"event_type": event_type, "duplicate": not applied})
        logger.info("message_processed", event_type=event_type,
                    message_id=str(message.message_id), duplicate=not applied)
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(StatusCode.ERROR, str(exc)[:120])
        service_bus_messages_failed.add(1, {"event_type": event_type})
        # Abandon so Service Bus redelivers; after MaxDeliveryCount it dead-letters the message.
        logger.error("message_failed", event_type=event_type, message_id=str(message.message_id),
                     delivery_count=message.delivery_count, error_type=type(exc).__name__,
                     error=str(exc))
        await receiver.abandon_message(message)
