# Observability

Both APIs emit **traces, metrics and structured logs** with OpenTelemetry and export them over OTLP. Locally they go to the **observability dashboard** (the .NET Aspire dashboard) at <http://localhost:18888>: one UI for traces, metrics and .NET logs from both implementations.

Export is controlled by the standard variables `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_SERVICE_NAME`. Compose sets them to `http://observability:18889` and `fincore-python-api` / `fincore-dotnet-api`. With no endpoint (tests, Docker-free runs) spans and metrics are still produced in-process, but nothing is exported.

## One trace, end to end

A payment made from the React app produces **one trace** across every hop, whichever backend handles each step:

```text
Browser (traceparent header)
 └─ POST /api/v1/payments                 HTTP server span (FastAPI / ASP.NET Core)
     └─ payment.create                     fincore span: payment id, status, risk decision
         └─ SELECT / INSERT ...             PostgreSQL spans (SQLAlchemy / Npgsql)
            (outbox row stores the traceparent)
 ...seconds later, in the outbox worker...
 └─ outbox publish payment.completed.v1   PRODUCER span, continues the stored trace
     └─ Service Bus message                traceparent travels as an application property
 └─ servicebus process payment.completed.v1   CONSUMER span, continues it again
     └─ INSERT notifications ...           PostgreSQL spans
```

Three mechanisms carry the context across process and time boundaries, all in the W3C `traceparent` format:

1. **Browser → API.** The frontend sends a fresh `traceparent` with every request (`frontend/src/api/client.ts`), and uses the same trace id as `X-Correlation-ID`. A problem response's `traceId` is therefore the id to search for in the dashboard.
2. **Request → outbox worker.** Every outbox row records the traceparent of the request that wrote it (`outbox_events.trace_context`, migration `0006`). Nothing at the call sites changes: Python stamps rows in a SQLAlchemy `before_insert` hook, .NET in `FinCoreDbContext.SaveChanges`. The dispatcher starts its span with that stored context as parent.
3. **Publisher → consumer.** The publish span's traceparent rides on the Service Bus message (`traceparent` application property). The consumer continues it, so the notification's database writes join the same trace.

Reconciliation adds the "external provider" hop: each lookup against the simulated payment provider is a `provider lookup` CLIENT span.

Business refusals (insufficient funds, limits, fraud block) are recorded as an **outcome** (`fincore.refusal_reason`), not as span errors, because a 4xx is the system working correctly. Span errors mark real failures: delivery exceptions, consumer failures, unhandled errors.

## Metrics

Names follow the architecture spec (section 31) and are identical in both APIs, so a chart doesn't care which backend served the traffic.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `payments_total` | counter | `outcome` (completed, held, refused, error) | Every payment creation attempt |
| `payments_success_total` | counter | `outcome` (completed, held) | Payments accepted |
| `payments_failed_total` | counter | `reason`, `status_code` | Payments refused, by reason |
| `payment_processing_duration` | histogram (s) | `outcome` | Time to create a payment |
| `fraud_detected_total` | counter | `decision` (review, block) | Payments flagged by fraud scoring |
| `reconciliation_mismatch_total` | counter | `type` (status_mismatch, amount_mismatch, ...) | Reconciliation records that did not match |
| `service_bus_messages_processed` | counter | `event_type`, `duplicate` | Messages applied by the consumer (duplicates are redeliveries skipped by the inbox) |
| `service_bus_messages_failed` | counter | `event_type`, `reason` | Messages abandoned or dead-lettered |
| `outbox_events_published` | counter | `event_type` | Outbox rows delivered to the transport |
| `outbox_events_dead_lettered` | counter | `event_type` | Outbox rows given up on after the retry limit |
| `cache_requests_total` | counter | `result` (hit, miss), `cache` | Configuration cache lookups |
| `http.server.request.duration` | histogram (s) | route, method, status | API request duration (the spec's `api_request_duration`), from the HTTP instrumentation |

The .NET API also exports runtime metrics (GC, thread pool, allocations).

Labels have bounded values on purpose: reason codes and event types, never ids or amounts, which would create a new time series per payment.

## Logs

* **Python:** structured JSON on stdout (structlog), with `trace_id` and `span_id` on every line written inside a span. Copy a `trace_id` into the dashboard's trace search.
* **.NET:** Serilog to the console (with the trace id) **and** to the dashboard over OTLP. In the dashboard's *Structured logs* view, each log line links to its trace.

## Using the dashboard

* **Traces:** filter by resource (`fincore-python-api`, `fincore-dotnet-api`) or open a trace id from an error response. A payment trace usually shows both services, because the two APIs compete for Service Bus messages.
* **Metrics:** pick a resource, then the `fincore` meter (Python) or `FinCore` meter (.NET).
* **Reading a trace:** the dashboard labels spans by type rather than name. Messaging spans (the outbox publish's Service Bus send and the consumer's `servicebus process ...`) appear as **MSG servicebus**, database spans as **DATA postgresql** and Redis calls as **DATA redis**; click a row to see the real span name and attributes. In a payment trace, Redis rows show the cache-aside pattern at work (a GET, a SET on a miss, then GETs that hit).
* **Structured logs:** .NET logs, searchable, each linked to its trace.

The dashboard keeps everything in memory and has no authentication. That is fine for local development and never meant for production.

## In Azure

The instrumentation doesn't change for production; only the destination does. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at an OpenTelemetry Collector (or use the Azure Monitor OpenTelemetry distro) that exports to **Azure Monitor / Application Insights**:
* traces become end-to-end transactions in Application Insights;
* metrics feed Azure Monitor alerts, for example on `payments_failed_total` rate, `service_bus_messages_failed` or `outbox_events_dead_lettered` above zero;
* logs land in Log Analytics, joined to traces by trace id.

Sampling (for example 10% of successful traces, 100% of errors) belongs in the collector, not the services.

## Not covered (yet)

* The browser *starts* each trace but doesn't export its own spans (page load, clicks). That would need the OpenTelemetry web SDK plus a CORS-enabled OTLP endpoint.
* Python logs aren't exported over OTLP; they're correlated through the `trace_id` field instead.
* There are no prebuilt alert rules or Grafana dashboards. The metric set above is what they would be built on.
