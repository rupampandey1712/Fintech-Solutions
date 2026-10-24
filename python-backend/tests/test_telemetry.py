"""Spans, metrics and trace propagation through the outbox and Service Bus."""

import json
import uuid as uuidlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.messaging as messaging
from app.infrastructure.models import OutboxEvent
from app.infrastructure.telemetry import current_traceparent, tracer
from app.worker import default_handlers, dispatch_batch, inline_delivery
from tests.conftest import METRICS, SPANS, requires_database
from tests.test_api_flow import create_account, fund_account, register_and_login
from tests.test_holds_and_treasury import _make_staff
from tests.test_messaging import _verified_beneficiary


def metric_sum(name: str, **attributes) -> float:
    """Sum (counters) or count (histograms) of a metric across matching data points."""
    total = 0.0
    data = METRICS.get_metrics_data()
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if all(point.attributes.get(k) == v for k, v in attributes.items()):
                        total += getattr(point, "value", None) or getattr(point, "count", 0)
    return total


def spans(name: str):
    return [span for span in SPANS.get_finished_spans() if span.name == name]


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


async def _paying_customer(client, session):
    alice = await register_and_login(client, f"t-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"t2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_acc = await create_account(client, alice["headers"])
    bob_acc = await create_account(client, bob["headers"])
    await fund_account(session, alice_acc["id"], 100_000)
    beneficiary = await _verified_beneficiary(client, alice["headers"], bob_acc["id"])
    return alice, alice_acc, beneficiary


async def _pay(client, user, source, beneficiary, amount, traceparent=None, reference=None):
    headers = {**user["headers"], "Idempotency-Key": uuidlib.uuid4().hex}
    if traceparent:
        headers["traceparent"] = traceparent
    return await client.post("/api/v1/payments", headers=headers, json={
        "sourceAccountId": source, "beneficiaryId": beneficiary, "amountMinor": amount,
        "currency": "USD", "reference": reference})


@requires_database
async def test_payments_record_spans_and_outcome_metrics(client, session):
    alice, account, beneficiary = await _paying_customer(client, session)
    completed_before = metric_sum("payments_total", outcome="completed")
    refused_before = metric_sum("payments_failed_total")
    durations_before = metric_sum("payment_processing_duration")

    ok = await _pay(client, alice, account["id"], beneficiary, 1_000)
    assert ok.status_code == 201
    refused = await _pay(client, alice, account["id"], beneficiary, 10_000_000_000)
    assert refused.status_code == 422

    assert metric_sum("payments_total", outcome="completed") == completed_before + 1
    assert metric_sum("payments_failed_total") == refused_before + 1
    assert metric_sum("payment_processing_duration") == durations_before + 2
    span = next(s for s in spans("payment.create")
                if s.attributes.get("fincore.payment.id") == ok.json()["id"])
    assert span.attributes["fincore.risk.decision"] == "allow"


class _Message:
    def __init__(self, traceparent: str, customer_id: str) -> None:
        self.message_id = str(uuidlib.uuid4())
        self.subject = "payment.completed.v1"
        self.delivery_count = 1
        self.application_properties = {b"traceparent": traceparent.encode()}
        self._body = json.dumps({"customerId": customer_id, "amountMinor": 1000, "currency": "USD"})

    def __str__(self) -> str:
        return self._body


class _Receiver:
    def __init__(self) -> None:
        self.completed = False

    async def complete_message(self, _message) -> None:
        self.completed = True

    async def abandon_message(self, _message) -> None:
        raise AssertionError("the message should not fail")

    async def dead_letter_message(self, _message, reason=None) -> None:
        raise AssertionError("the message should not be dead-lettered")


@requires_database
async def test_one_trace_from_the_browser_through_outbox_and_service_bus(
    client, session, monkeypatch,
):
    alice, account, beneficiary = await _paying_customer(client, session)
    browser_trace = uuidlib.uuid4().hex
    traceparent = f"00-{browser_trace}-{uuidlib.uuid4().hex[:16]}-01"

    response = await _pay(client, alice, account["id"], beneficiary, 1_000, traceparent)
    payment_id = uuidlib.UUID(response.json()["id"])

    # 1. The API joined the browser's trace and stamped it on the outbox row.
    event = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == payment_id, OutboxEvent.event_type == "payment.completed.v1"))
    assert trace_id_of(event.trace_context) == browser_trace

    # 2. The dispatcher, running later in another transaction, continues it ...
    captured: dict[str, str] = {}

    async def deliver(_session, row):
        if row.id == event.id:
            captured["traceparent"] = current_traceparent()

    await dispatch_batch(session, deliver, batch_size=500, max_attempts=5)
    assert trace_id_of(captured["traceparent"]) == browser_trace
    assert any(s.context.trace_id == int(browser_trace, 16)
               for s in spans("outbox publish payment.completed.v1"))

    # 3. ... and so does the Service Bus consumer that receives the published message.
    factory = async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(messaging, "SessionFactory", factory)
    receiver = _Receiver()
    processed_before = metric_sum("service_bus_messages_processed")
    await messaging._process(receiver, _Message(captured["traceparent"], alice["customer_id"]),
                             "notifications", default_handlers())
    assert receiver.completed
    consumer = spans("servicebus process payment.completed.v1")[-1]
    assert consumer.context.trace_id == int(browser_trace, 16)
    assert metric_sum("service_bus_messages_processed") == processed_before + 1


@requires_database
async def test_inline_delivery_continues_the_writer_trace(client, session):
    with tracer.start_as_current_span("test request") as span:
        session.add(OutboxEvent(aggregate_id=uuidlib.uuid4(), event_type="unknown.v1",
                                payload={}))
        await session.commit()
        expected = span.get_span_context().trace_id
    published_before = metric_sum("outbox_events_published")
    await dispatch_batch(session, inline_delivery(default_handlers()), 500, 5)
    assert any(s.context.trace_id == expected for s in spans("outbox publish unknown.v1"))
    assert metric_sum("outbox_events_published") >= published_before + 1


@requires_database
async def test_reconciliation_counts_mismatches_and_traces_the_provider(client, session):
    alice, account, beneficiary = await _paying_customer(client, session)
    ops = await register_and_login(client, f"ops-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, ops["customer_id"], "operations_user")
    paid = await _pay(client, alice, account["id"], beneficiary, 500,
                      reference="force-status-mismatch")
    assert paid.status_code == 201
    before = metric_sum("reconciliation_mismatch_total", type="status_mismatch")

    run = await client.post("/api/v1/reconciliation/run", headers=ops["headers"])
    assert run.status_code == 200, run.text  # operations_user may run reconciliation
    assert metric_sum("reconciliation_mismatch_total", type="status_mismatch") == before + 1
    assert spans("provider lookup")
