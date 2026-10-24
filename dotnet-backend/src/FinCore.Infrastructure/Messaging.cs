using System.Diagnostics;
using System.Text.Json;
using Azure.Messaging.ServiceBus;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;

namespace FinCore.Infrastructure;

public sealed record MessagingOptions(string Provider, string? ConnectionString, string Topic, string Subscription)
{
    public bool UsesServiceBus => Provider.Equals("servicebus", StringComparison.OrdinalIgnoreCase);

    public static MessagingOptions From(IConfiguration configuration)
    {
        var options = new MessagingOptions(
            configuration["MESSAGE_PROVIDER"] ?? "inline",
            configuration["SERVICEBUS_CONNECTION_STRING"],
            configuration["SERVICEBUS_TOPIC"] ?? "fincore-events",
            configuration["SERVICEBUS_SUBSCRIPTION"] ?? "notifications");
        if (options.UsesServiceBus && string.IsNullOrWhiteSpace(options.ConnectionString))
            throw new InvalidOperationException("SERVICEBUS_CONNECTION_STRING is required when MESSAGE_PROVIDER=servicebus.");
        return options;
    }
}

/// <summary>Turns integration events into customer notifications. Used by both transports.</summary>
public static class NotificationEventHandlers
{
    public static async Task ApplyAsync(FinCoreDbContext db, string eventType, string payloadJson,
        CancellationToken cancellationToken)
    {
        using var payload = ParsePayload(payloadJson);
        var description = Describe(eventType, payload?.RootElement);
        if (description is null)
        {
            Log.Information("Event {EventType} has no notification handler", eventType);
            return;
        }
        var customerId = ExtractCustomerId(payloadJson)
            ?? throw new InvalidOperationException($"{eventType} payload has no valid customerId.");
        await NotificationService.QueueNotificationAsync(db, customerId, eventType,
            description.Value.Subject, description.Value.Body, cancellationToken);
        // The mock gateway accepts immediately; with a real email/SMS/push provider this is the
        // point where rows would stay queued until the provider acknowledges.
        await NotificationService.DispatchQueuedAsync(db, customerId, eventType, cancellationToken);
    }

    private static (string Subject, string Body)? Describe(string eventType, JsonElement? payload) => eventType switch
    {
        "payment.completed.v1" => ("Payment completed", "Your payment has been completed."),
        "kyc.approved.v1" => ("KYC approved", "Your identity verification has been approved."),
        "payment.flagged.v1" => ("Additional review required", "Your payment requires additional review."),
        "fraud.case.opened.v1" => ("Fraud case opened", "A fraud case was opened for one of your payments."),
        "aml.alert.opened.v1" => ("AML alert raised", "An anti-money-laundering alert was raised on your account."),
        "payment.rejected.v1" => ("Payment rejected",
            $"Your payment was rejected after review. {Amount(payload)} has been returned to your account."),
        "account.funded.v1" => ("Funds received", $"{Amount(payload)} was deposited into your account."),
        "payment.refunded.v1" => ("Refund received", $"{Amount(payload)} from one of your payments was refunded to your account."),
        "payment.reversed.v1" => ("Payment reversed",
            $"One of your payments was reversed as a correction. {Amount(payload)} was returned to your account."),
        "payment.failed.v1" => ("Payment failed",
            $"Your payment was not completed. {Amount(payload)} has not left your account."),
        "kyc.submitted.v1" => ("KYC submitted", "Your identity verification was submitted and is being reviewed."),
        "customer.blocked.v1" => ("Account access changed",
            $"Your account status changed to {Text(payload, "status")}. Contact support if you believe this is a mistake."),
        "payment.dispute.opened.v1" => ("Dispute opened",
            $"A dispute was opened for payment {Text(payload, "paymentId")}. Our team will investigate and contact you."),
        "payment.dispute.decided.v1" => ("Dispute decided",
            $"Your dispute for payment {Text(payload, "paymentId")} was decided. "
            + (Text(payload, "outcome") == "lost"
                ? "The chargeback was upheld and the funds were returned to your account."
                : "The chargeback was rejected; the original transfer stands.")),
        "settlement.completed.v1" => ("Settlement completed",
            $"Settlement batch {Text(payload, "settlementBatchId")} for {Text(payload, "businessDate")} completed."),
        "settlement.failed.v1" => ("Settlement failed",
            $"Settlement batch {Text(payload, "settlementBatchId")} failed and needs attention."),
        _ => null
    };

    private static string Text(JsonElement? payload, string property) =>
        payload is { ValueKind: JsonValueKind.Object } root
            && root.TryGetProperty(property, out var value)
            && value.ValueKind is JsonValueKind.String or JsonValueKind.Number
            ? value.ToString()
            : "unknown";

    /// <summary>"38900" + "USD" → "389.00 USD", matching how the UI renders minor units.</summary>
    private static string Amount(JsonElement? payload)
    {
        if (payload is not { ValueKind: JsonValueKind.Object } root
            || !root.TryGetProperty("amountMinor", out var minor) || !minor.TryGetInt64(out var amount))
            return "The amount";
        var currency = root.TryGetProperty("currency", out var code) ? code.GetString() : null;
        return $"{(amount / 100m).ToString("N2", System.Globalization.CultureInfo.InvariantCulture)} {currency}".Trim();
    }

    private static JsonDocument? ParsePayload(string payloadJson)
    {
        try
        {
            return JsonDocument.Parse(payloadJson);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static Guid? ExtractCustomerId(string payloadJson)
    {
        try
        {
            using var payload = JsonDocument.Parse(payloadJson);
            return payload.RootElement.TryGetProperty("customerId", out var value) && value.TryGetGuid(out var customerId)
                ? customerId
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }
}

public interface IOutboxPublisher
{
    Task PublishAsync(OutboxEventRecord item, CancellationToken cancellationToken);
}

public sealed class ServiceBusOutboxPublisher(ServiceBusClient client, MessagingOptions options) : IOutboxPublisher, IAsyncDisposable
{
    private readonly ServiceBusSender _sender = client.CreateSender(options.Topic);

    public Task PublishAsync(OutboxEventRecord item, CancellationToken cancellationToken)
    {
        // MessageId = outbox id lets the topic's duplicate detection drop a re-publish that happens
        // when the process dies between sending and marking the row published.
        var message = new ServiceBusMessage(BinaryData.FromString(item.PayloadJson))
        {
            MessageId = item.Id.ToString(),
            Subject = item.EventType,
            ContentType = "application/json"
        };
        message.ApplicationProperties["eventType"] = item.EventType;
        message.ApplicationProperties["schemaVersion"] = item.SchemaVersion;
        message.ApplicationProperties["aggregateId"] = item.AggregateId.ToString();
        // The dispatcher's activity, so the consumer continues the same trace.
        message.ApplicationProperties["traceparent"] = FinCoreTelemetry.CurrentTraceparent() ?? string.Empty;
        return _sender.SendMessageAsync(message, cancellationToken);
    }

    public ValueTask DisposeAsync() => _sender.DisposeAsync();
}

/// <summary>
/// Consumes the notifications subscription. Delivery is at-least-once, so each message id is
/// recorded in inbox_messages in the same transaction as the handler's writes; a redelivery is
/// completed without repeating its effects. Messages that keep failing are dead-lettered by
/// Service Bus after the subscription's MaxDeliveryCount.
/// </summary>
public sealed class ServiceBusConsumerWorker(ServiceBusClient client, MessagingOptions options,
    IServiceScopeFactory scopeFactory) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await using var processor = client.CreateProcessor(options.Topic, options.Subscription, new ServiceBusProcessorOptions
        {
            AutoCompleteMessages = false,
            MaxConcurrentCalls = 4,
            ReceiveMode = ServiceBusReceiveMode.PeekLock
        });
        processor.ProcessMessageAsync += HandleAsync;
        processor.ProcessErrorAsync += args =>
        {
            Log.Warning(args.Exception, "Service Bus consumer error from {Source}", args.ErrorSource);
            return Task.CompletedTask;
        };
        await processor.StartProcessingAsync(stoppingToken);
        Log.Information("Service Bus consumer started on {Topic}/{Subscription}", options.Topic, options.Subscription);
        try
        {
            await Task.Delay(Timeout.Infinite, stoppingToken);
        }
        catch (OperationCanceledException)
        {
        }
        await processor.StopProcessingAsync(CancellationToken.None);
    }

    private async Task HandleAsync(ProcessMessageEventArgs args)
    {
        var message = args.Message;
        var eventType = message.Subject ?? string.Empty;
        var traceparent = message.ApplicationProperties.TryGetValue("traceparent", out var value)
            && value is string text && text.Length > 0 ? text : null;
        using var activity = FinCoreTelemetry.StartContinuing($"servicebus process {eventType}", ActivityKind.Consumer, traceparent);
        activity?.SetTag("messaging.system", "servicebus");
        activity?.SetTag("messaging.message.id", message.MessageId);
        activity?.SetTag("messaging.destination.subscription.name", options.Subscription);
        activity?.SetTag("fincore.delivery_count", message.DeliveryCount);
        var eventTag = new KeyValuePair<string, object?>("event_type", eventType);
        var payload = message.Body.ToString();
        try
        {
            using var _ = JsonDocument.Parse(payload);
        }
        catch (JsonException)
        {
            // Retrying cannot fix a malformed body, so dead-letter it immediately.
            await args.DeadLetterMessageAsync(message, "malformed_payload", cancellationToken: args.CancellationToken);
            FinCoreTelemetry.ServiceBusMessagesFailed.Add(1, eventTag, new("reason", "malformed"));
            return;
        }

        try
        {
            using var scope = scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<FinCoreDbContext>();
            var applied = await ApplyOnceAsync(db, options.Subscription, message.MessageId, eventType, payload,
                args.CancellationToken);
            await args.CompleteMessageAsync(message, args.CancellationToken);
            FinCoreTelemetry.ServiceBusMessagesProcessed.Add(1, eventTag, new("duplicate", !applied));
            Log.Information("Processed {EventType} message {MessageId} (duplicate: {Duplicate})",
                eventType, message.MessageId, !applied);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            activity?.AddException(exception);
            activity?.SetStatus(ActivityStatusCode.Error, exception.Message);
            FinCoreTelemetry.ServiceBusMessagesFailed.Add(1, eventTag);
            Log.Error(exception, "Message {MessageId} of type {EventType} failed on delivery {DeliveryCount}",
                message.MessageId, eventType, message.DeliveryCount);
            await args.AbandonMessageAsync(message, cancellationToken: args.CancellationToken);
        }
    }

    /// <summary>Runs the handler once per consumer and message id. Returns false for duplicates.</summary>
    public static async Task<bool> ApplyOnceAsync(FinCoreDbContext db, string consumer, string messageId,
        string eventType, string payloadJson, CancellationToken cancellationToken)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var claimed = await db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO inbox_messages (consumer, message_id, event_type, processed_at)
            VALUES ({consumer}, {messageId}, {eventType}, now())
            ON CONFLICT (consumer, message_id) DO NOTHING
            """, cancellationToken);
        if (claimed == 0)
        {
            await transaction.RollbackAsync(cancellationToken);
            return false;
        }
        await NotificationEventHandlers.ApplyAsync(db, eventType, payloadJson, cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return true;
    }
}

public static class MessagingRegistration
{
    public static IServiceCollection AddFinCoreMessaging(this IServiceCollection services, IConfiguration configuration)
    {
        var options = MessagingOptions.From(configuration);
        services.AddSingleton(options);
        if (configuration.GetValue<bool?>("OUTBOX_WORKER_ENABLED") ?? true)
            services.AddHostedService<OutboxDispatcherWorker>();
        // Scheduled reconciliation (spec section 49); off unless RECONCILIATION_SCHEDULE_ENABLED.
        services.AddHostedService<ReconciliationWorker>();
        if (!options.UsesServiceBus)
            return services;
        services.AddSingleton(_ => new ServiceBusClient(options.ConnectionString));
        services.AddSingleton<IOutboxPublisher, ServiceBusOutboxPublisher>();
        services.AddHostedService<ServiceBusConsumerWorker>();
        return services;
    }
}
