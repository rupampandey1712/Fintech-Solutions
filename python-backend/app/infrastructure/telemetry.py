"""OpenTelemetry: traces, metrics and trace-aware logs.

Exporting is configured with the standard variables (OTEL_EXPORTER_OTLP_ENDPOINT,
OTEL_SERVICE_NAME, ...). Without an endpoint, spans and metrics are still created, so code paths and
tests behave the same, but nothing leaves the process.

Traces cross process boundaries with the W3C ``traceparent`` format: the browser sends it with
each request, outbox rows store it (``outbox_events.trace_context``), and Service Bus messages
carry it as an application property. One payment therefore produces one trace through the API,
the database, the outbox dispatcher, Service Bus and the consumer, whichever implementation runs
each step.
"""

import os
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import event

from app.infrastructure.models import OutboxEvent

tracer = trace.get_tracer("fincore")
meter = metrics.get_meter("fincore")
_propagator = TraceContextTextMapPropagator()

# Metric names follow the architecture spec (section 31). HTTP request duration comes from the
# FastAPI instrumentation as the standard http.server.request.duration histogram.
payments_total = meter.create_counter(
    "payments_total", description="Payment creation attempts, by outcome")
payments_success_total = meter.create_counter(
    "payments_success_total", description="Payments accepted (completed, or held for review)")
payments_failed_total = meter.create_counter(
    "payments_failed_total", description="Payments refused, by reason code")
payment_processing_duration = meter.create_histogram(
    "payment_processing_duration", unit="s", description="Time to create a payment")
fraud_detected_total = meter.create_counter(
    "fraud_detected_total", description="Payments flagged by fraud scoring, by decision")
reconciliation_mismatch_total = meter.create_counter(
    "reconciliation_mismatch_total", description="Reconciliation records that did not match")
service_bus_messages_processed = meter.create_counter(
    "service_bus_messages_processed", description="Messages applied by the consumer")
service_bus_messages_failed = meter.create_counter(
    "service_bus_messages_failed", description="Messages that failed and were abandoned")
outbox_events_published = meter.create_counter(
    "outbox_events_published", description="Outbox events delivered to the transport")
outbox_events_dead_lettered = meter.create_counter(
    "outbox_events_dead_lettered", description="Outbox events moved to dead letters")
cache_requests_total = meter.create_counter(
    "cache_requests_total", description="Configuration cache lookups, by result (hit/miss)")


def current_traceparent() -> str | None:
    """The active span's W3C traceparent, or None when nothing is being traced."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def context_from(traceparent: str | None) -> otel_context.Context | None:
    """A parent context for a span continuing a trace from another process (None: new trace)."""
    if not traceparent:
        return None
    return _propagator.extract({"traceparent": traceparent})


@event.listens_for(OutboxEvent, "before_insert")
def _stamp_outbox_trace(_mapper, _connection, target: OutboxEvent) -> None:
    # Every outbox row records the trace of the request that wrote it, with no call-site changes.
    if target.trace_context is None:
        target.trace_context = current_traceparent()


def add_trace_ids(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: put the active trace and span ids on every log line."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


# Extra processors/readers installed with the providers (tests add in-memory ones here).
extra_span_processors: list[Any] = []
extra_metric_readers: list[Any] = []


def configure_telemetry(app) -> None:
    """Install the SDK providers and the automatic instrumentation for HTTP, SQL and Redis."""
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.namespace": "fincore"})  # + OTEL_SERVICE_NAME
    tracer_provider = TracerProvider(resource=resource)
    readers = []
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(),
                                                     export_interval_millis=10_000))
    for processor in extra_span_processors:
        tracer_provider.add_span_processor(processor)
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource,
                                             metric_readers=readers + extra_metric_readers))

    # The per-message ASGI send/receive spans add rows without information; health checks add noise.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready",
                                       exclude_spans=["receive", "send"])
    # PostgreSQL spans come from the asyncpg driver. SQLAlchemy's own instrumentation is pinned
    # to older SQLAlchemy releases and silently does nothing on newer ones.
    AsyncPGInstrumentor().instrument()
    RedisInstrumentor().instrument()
