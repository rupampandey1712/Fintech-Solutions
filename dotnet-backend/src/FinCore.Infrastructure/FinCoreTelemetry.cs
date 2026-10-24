using System.Diagnostics;
using System.Diagnostics.Metrics;

namespace FinCore.Infrastructure;

/// <summary>
/// FinCore's own spans and metrics. Instrument names match the Python API (architecture spec,
/// section 31), so a dashboard reads the same series whichever implementation served a request.
/// Traces cross process boundaries as W3C traceparent strings: outbox rows store the writer's
/// (outbox_events.trace_context) and Service Bus messages carry it as an application property.
/// HTTP request duration comes from the ASP.NET Core instrumentation (http.server.request.duration).
/// </summary>
public static class FinCoreTelemetry
{
    public const string Name = "FinCore";

    public static readonly ActivitySource Source = new(Name);
    private static readonly Meter Meter = new(Name);

    public static readonly Counter<long> PaymentsTotal =
        Meter.CreateCounter<long>("payments_total", description: "Payment creation attempts, by outcome");
    public static readonly Counter<long> PaymentsSuccessTotal =
        Meter.CreateCounter<long>("payments_success_total", description: "Payments accepted (completed, or held for review)");
    public static readonly Counter<long> PaymentsFailedTotal =
        Meter.CreateCounter<long>("payments_failed_total", description: "Payments refused, by reason code");
    public static readonly Histogram<double> PaymentProcessingDuration =
        Meter.CreateHistogram<double>("payment_processing_duration", unit: "s", description: "Time to create a payment");
    public static readonly Counter<long> FraudDetectedTotal =
        Meter.CreateCounter<long>("fraud_detected_total", description: "Payments flagged by fraud scoring, by decision");
    public static readonly Counter<long> ReconciliationMismatchTotal =
        Meter.CreateCounter<long>("reconciliation_mismatch_total", description: "Reconciliation records that did not match");
    public static readonly Counter<long> ServiceBusMessagesProcessed =
        Meter.CreateCounter<long>("service_bus_messages_processed", description: "Messages applied by the consumer");
    public static readonly Counter<long> ServiceBusMessagesFailed =
        Meter.CreateCounter<long>("service_bus_messages_failed", description: "Messages that failed and were abandoned");
    public static readonly Counter<long> OutboxEventsPublished =
        Meter.CreateCounter<long>("outbox_events_published", description: "Outbox events delivered to the transport");
    public static readonly Counter<long> OutboxEventsDeadLettered =
        Meter.CreateCounter<long>("outbox_events_dead_lettered", description: "Outbox events moved to dead letters");
    public static readonly Counter<long> CacheRequestsTotal =
        Meter.CreateCounter<long>("cache_requests_total", description: "Configuration cache lookups, by result (hit/miss)");

    /// <summary>The current activity's W3C traceparent, or null when nothing is being traced.</summary>
    public static string? CurrentTraceparent() =>
        Activity.Current is { IdFormat: ActivityIdFormat.W3C } activity ? activity.Id : null;

    /// <summary>
    /// Starts an activity continuing a trace from another process. Without a traceparent it nests under the
    /// ambient activity if there is one (e.g. the Service Bus SDK's own span), otherwise it starts a new trace.
    /// </summary>
    public static Activity? StartContinuing(string name, ActivityKind kind, string? traceparent) =>
        traceparent is not null && ActivityContext.TryParse(traceparent, null, out var parent)
            ? Source.StartActivity(name, kind, parent)
            : Source.StartActivity(name, kind, parentContext: default);
}
