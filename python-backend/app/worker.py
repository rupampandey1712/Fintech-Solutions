"""Outbox dispatcher: drains committed outbox rows to the message transport.

With MESSAGE_PROVIDER=servicebus each event is published to the Service Bus topic and the
subscription consumer (app.services.messaging) applies the handlers below. With
MESSAGE_PROVIDER=inline the handlers run here directly, each inside its own savepoint so one
failing event cannot roll back the rest of the batch.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.infrastructure.database import SessionFactory, engine
from app.infrastructure.models import OutboxDeadLetter, OutboxEvent
from app.infrastructure.telemetry import (
    context_from,
    outbox_events_dead_lettered,
    outbox_events_published,
    tracer,
)
from app.services.messaging import Handler, ServiceBusPublisher, run_consumer
from app.services.notifications import dispatch_queued, queue_notification

logger = structlog.get_logger()

Deliver = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]

MONEY = "amountMinor"


def _major(amount_minor: Any) -> str:
    """Render minor units the way the UI does ("38900" -> "389.00")."""
    try:
        return f"{int(amount_minor) / 100:,.2f}"
    except (TypeError, ValueError):
        return str(amount_minor)


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _notify(session: AsyncSession, payload: dict[str, Any], event_type: str, subject: str,
                  body: str) -> None:
    customer_id = _uuid(payload.get("customerId"))
    if customer_id is None:
        raise ValueError(f"{event_type} payload has no valid customerId")
    await queue_notification(session, customer_id, event_type, subject, body)
    # The mock gateway accepts immediately; with a real email/SMS/push provider this is the
    # point where rows would stay queued until the provider acknowledges.
    await dispatch_queued(session, customer_id, event_type)


async def _handle_payment_completed(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.completed.v1", "Payment completed",
        f"Your payment {payload.get('paymentId')} for {_major(payload.get(MONEY))} "
        f"{payload.get('currency')} completed successfully.",
    )


async def _handle_kyc_approved(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "kyc.approved.v1", "KYC approved",
        "Your identity verification has been approved. Your account is now active.",
    )


async def _handle_payment_flagged(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.flagged.v1", "Additional review required",
        f"Your payment {payload.get('paymentId')} requires additional review "
        f"(risk score {payload.get('score')}, level {payload.get('level')}).",
    )


async def _handle_fraud_case_opened(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "fraud.case.opened.v1", "Fraud case opened",
        f"A fraud review case was opened for payment {payload.get('paymentId')}.",
    )


async def _handle_aml_alert_opened(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "aml.alert.opened.v1", "AML review",
        f"An anti-money-laundering review was started for payment {payload.get('paymentId')}.",
    )


async def _handle_payment_rejected(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.rejected.v1", "Payment rejected",
        f"Your payment {payload.get('paymentId')} was rejected after review. "
        f"{_major(payload.get(MONEY))} {payload.get('currency')} has been returned to your "
        "account.",
    )


async def _handle_account_funded(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "account.funded.v1", "Funds received",
        f"{_major(payload.get(MONEY))} {payload.get('currency')} was deposited into your account.",
    )


async def _handle_payment_refunded(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.refunded.v1", "Refund received",
        f"{_major(payload.get(MONEY))} {payload.get('currency')} from payment "
        f"{payload.get('paymentId')} was refunded to your account.",
    )


async def _handle_payment_reversed(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.reversed.v1", "Payment reversed",
        f"Payment {payload.get('paymentId')} was reversed as a correction. "
        f"{_major(payload.get(MONEY))} {payload.get('currency')} was returned to your account.",
    )


async def _handle_payment_failed(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.failed.v1", "Payment failed",
        f"Your payment {payload.get('paymentId')} for {_major(payload.get(MONEY))} "
        f"{payload.get('currency')} was not completed. No funds have left your account.",
    )


async def _handle_kyc_submitted(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "kyc.submitted.v1", "KYC submitted",
        "Your identity verification was submitted and is being reviewed.",
    )


async def _handle_customer_blocked(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "customer.blocked.v1", "Account access changed",
        f"Your account status changed to {payload.get('status')}. "
        "Contact support if you believe this is a mistake.",
    )


async def _handle_dispute_opened(session: AsyncSession, payload: dict[str, Any]) -> None:
    await _notify(
        session, payload, "payment.dispute.opened.v1", "Dispute opened",
        f"A dispute was opened for payment {payload.get('paymentId')}. "
        "Our team will investigate and contact you.",
    )


async def _handle_dispute_decided(session: AsyncSession, payload: dict[str, Any]) -> None:
    outcome = payload.get("outcome")
    detail = (
        "The chargeback was upheld and the funds were returned to your account."
        if outcome == "lost" else "The chargeback was rejected; the original transfer stands."
    )
    await _notify(
        session, payload, "payment.dispute.decided.v1", "Dispute decided",
        f"Your dispute for payment {payload.get('paymentId')} was decided. {detail}",
    )


async def _handle_settlement_finished(session: AsyncSession, payload: dict[str, Any]) -> None:
    status = payload.get("status")
    subject = "Settlement completed" if status == "completed" else "Settlement failed"
    body = (
        f"Settlement batch {payload.get('settlementBatchId')} for "
        f"{payload.get('businessDate')} completed: net "
        f"{_major(payload.get('netMinor'))} {payload.get('currency')}."
        if status == "completed" else
        f"Settlement batch {payload.get('settlementBatchId')} failed and needs attention."
    )
    await _notify(session, payload, f"settlement.{status}.v1", subject, body)


def default_handlers() -> dict[str, Handler]:
    return {
        "payment.completed.v1": _handle_payment_completed,
        "kyc.approved.v1": _handle_kyc_approved,
        "payment.flagged.v1": _handle_payment_flagged,
        "fraud.case.opened.v1": _handle_fraud_case_opened,
        "aml.alert.opened.v1": _handle_aml_alert_opened,
        "payment.rejected.v1": _handle_payment_rejected,
        "account.funded.v1": _handle_account_funded,
        "payment.refunded.v1": _handle_payment_refunded,
        "payment.reversed.v1": _handle_payment_reversed,
        "payment.failed.v1": _handle_payment_failed,
        "kyc.submitted.v1": _handle_kyc_submitted,
        "customer.blocked.v1": _handle_customer_blocked,
        "payment.dispute.opened.v1": _handle_dispute_opened,
        "payment.dispute.decided.v1": _handle_dispute_decided,
        "settlement.completed.v1": _handle_settlement_finished,
        "settlement.failed.v1": _handle_settlement_finished,
        # notification.requested.v1 is deliberately absent: queue_notification already fanned
        # the request out to the channels, and a handler here would double every notification.
    }


def inline_delivery(handlers: dict[str, Handler]) -> Deliver:
    async def deliver(session: AsyncSession, event: OutboxEvent) -> None:
        handler = handlers.get(event.event_type)
        if handler is None:
            logger.info("outbox_event_ignored", event_type=event.event_type,
                        event_id=str(event.id))
            return
        # The savepoint flushes on exit, so constraint violations surface here and only
        # this event's writes are discarded.
        async with session.begin_nested():
            await handler(session, event.payload)

    return deliver


def servicebus_delivery(publisher: ServiceBusPublisher) -> Deliver:
    async def deliver(_: AsyncSession, event: OutboxEvent) -> None:
        await publisher.publish(event)

    return deliver


async def dispatch_batch(session: AsyncSession, deliver: Deliver, batch_size: int,
                         max_attempts: int) -> int:
    now = datetime.now(UTC)
    events = list((await session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.published_at.is_(None),
            (OutboxEvent.next_attempt_at.is_(None)) | (OutboxEvent.next_attempt_at <= now),
        )
        .order_by(OutboxEvent.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )).all())
    for event in events:
        # Continue the trace of the request that wrote the row (see outbox_events.trace_context).
        with tracer.start_as_current_span(
            f"outbox publish {event.event_type}", context=context_from(event.trace_context),
            kind=SpanKind.PRODUCER,
            attributes={"messaging.message.id": str(event.id),
                        "fincore.event_type": event.event_type,
                        "fincore.outbox.attempt": event.attempts + 1},
        ) as span:
            await _deliver_one(session, deliver, event, max_attempts, span)
    await session.commit()
    return len(events)


async def _deliver_one(session: AsyncSession, deliver: Deliver, event: OutboxEvent,
                       max_attempts: int, span) -> None:
    try:
        await deliver(session, event)
        event.published_at = datetime.now(UTC)
        outbox_events_published.add(1, {"event_type": event.event_type})
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(StatusCode.ERROR, str(exc)[:120])
        event.attempts += 1
        if event.attempts >= max_attempts:
            outbox_events_dead_lettered.add(1, {"event_type": event.event_type})
            session.add(OutboxDeadLetter(
                outbox_event_id=event.id, event_type=event.event_type,
                payload=event.payload, failure_reason=str(exc)[:300],
            ))
            event.published_at = datetime.now(UTC)
            logger.error("outbox_event_dead_lettered", event_type=event.event_type,
                         event_id=str(event.id), error=str(exc))
        else:
            event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2 ** event.attempts)
            logger.warning("outbox_delivery_failed", event_type=event.event_type,
                           event_id=str(event.id), attempts=event.attempts, error=str(exc))


async def run_forever(deliver: Deliver | None = None) -> None:
    settings = get_settings()
    deliver = deliver if deliver is not None else inline_delivery(default_handlers())
    logger.info("outbox_worker_started", poll_seconds=settings.outbox_poll_seconds)
    while True:
        try:
            async with SessionFactory() as session:
                processed = await dispatch_batch(
                    session, deliver, settings.outbox_batch_size, settings.outbox_max_attempts,
                )
            if processed:
                logger.debug("outbox_batch_processed", events=processed)
        except Exception as exc:
            logger.error("outbox_worker_tick_failed", error_type=type(exc).__name__, error=str(exc))
        await asyncio.sleep(settings.outbox_poll_seconds)


async def run_scheduled_reconciliation() -> None:
    """Periodic reconciliation pass (spec section 49: background reconciliation worker).

    Off by default; RECONCILIATION_SCHEDULE_ENABLED=true turns it on for deployments that want
    the operations dashboard to fill itself in without an analyst pressing "Run".
    """
    settings = get_settings()
    if not settings.reconciliation_schedule_enabled:
        return
    # Imported here so the API-only test path does not need the reconciliation module.
    from app.api.reconciliation import execute_reconciliation_run

    logger.info("reconciliation_worker_started",
                interval_seconds=settings.reconciliation_interval_seconds)
    while True:
        try:
            async with SessionFactory() as session:
                run = await execute_reconciliation_run(session)
            logger.info("reconciliation_run_finished", run_id=str(run.run_id),
                        matched=run.matched, amount_mismatch=run.amount_mismatch,
                        status_mismatch=run.status_mismatch,
                        missing_external=run.missing_external,
                        missing_internal=run.missing_internal)
        except Exception as exc:
            logger.error("reconciliation_worker_tick_failed",
                         error_type=type(exc).__name__, error=str(exc))
        await asyncio.sleep(settings.reconciliation_interval_seconds)


async def run_messaging() -> None:
    """Run the outbox worker plus, for Service Bus, the subscription consumer."""
    settings = get_settings()
    if settings.message_provider == "inline":
        tasks = [run_forever(inline_delivery(default_handlers()))]
        if settings.reconciliation_schedule_enabled:
            tasks.append(run_scheduled_reconciliation())
        await asyncio.gather(*tasks)
        return
    publisher = ServiceBusPublisher(settings)
    tasks = [run_forever(servicebus_delivery(publisher)),
             run_consumer(settings, default_handlers())]
    if settings.reconciliation_schedule_enabled:
        tasks.append(run_scheduled_reconciliation())
    try:
        await asyncio.gather(*tasks)
    finally:
        await publisher.close()


async def main() -> None:
    try:
        await run_messaging()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
