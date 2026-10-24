using System.Diagnostics;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Hosting;
using Serilog;

namespace FinCore.Infrastructure;

public delegate Task OutboxDelivery(FinCoreDbContext db, OutboxEventRecord item, CancellationToken cancellationToken);

public sealed class OutboxDispatcherWorker(IServiceScopeFactory scopeFactory, IConfiguration configuration,
    IOutboxPublisher? publisher = null) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var pollSeconds = configuration.GetValue<double?>("OUTBOX_POLL_SECONDS") ?? 2.0;
        var batchSize = configuration.GetValue<int?>("OUTBOX_BATCH_SIZE") ?? 20;
        var maxAttempts = configuration.GetValue<int?>("OUTBOX_MAX_ATTEMPTS") ?? 5;
        // With Service Bus the subscription consumer applies the handlers; otherwise run them here.
        OutboxDelivery deliver = publisher is null
            ? InlineDeliveryAsync
            : (_, item, cancellationToken) => publisher.PublishAsync(item, cancellationToken);
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                using var scope = scopeFactory.CreateScope();
                var db = scope.ServiceProvider.GetRequiredService<FinCoreDbContext>();
                await DispatchBatchAsync(db, deliver, batchSize, maxAttempts, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                Log.Warning(exception, "Outbox dispatch tick failed");
            }
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(pollSeconds), stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    public static async Task InlineDeliveryAsync(FinCoreDbContext db, OutboxEventRecord item, CancellationToken cancellationToken)
    {
        try
        {
            // Inside an open transaction EF Core wraps SaveChanges in a savepoint and rolls back to
            // it on failure, so only this event's writes are lost.
            await NotificationEventHandlers.ApplyAsync(db, item.EventType, item.PayloadJson, cancellationToken);
        }
        catch
        {
            foreach (var entry in db.ChangeTracker.Entries<NotificationRecord>().Where(e => e.State == EntityState.Added).ToList())
                entry.State = EntityState.Detached;
            throw;
        }
    }

    public static async Task<int> DispatchBatchAsync(FinCoreDbContext db, OutboxDelivery deliver, int batchSize,
        int maxAttempts, CancellationToken cancellationToken)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        // FromSql (not Database.SqlQuery) so the rows are tracked and PublishedAt/Attempts are saved.
        var events = await db.OutboxEvents
            .FromSql($"""
                SELECT * FROM outbox_events
                WHERE published_at IS NULL AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at
                LIMIT {batchSize}
                FOR UPDATE SKIP LOCKED
                """)
            .ToListAsync(cancellationToken);

        foreach (var item in events)
        {
            // Continue the trace of the request that wrote the row (see outbox_events.trace_context).
            using var activity = FinCoreTelemetry.StartContinuing($"outbox publish {item.EventType}",
                ActivityKind.Producer, item.TraceContext);
            activity?.SetTag("messaging.message.id", item.Id.ToString());
            activity?.SetTag("fincore.event_type", item.EventType);
            activity?.SetTag("fincore.outbox.attempt", item.Attempts + 1);
            var eventTag = new KeyValuePair<string, object?>("event_type", item.EventType);
            try
            {
                await deliver(db, item, cancellationToken);
                item.PublishedAt = DateTimeOffset.UtcNow;
                FinCoreTelemetry.OutboxEventsPublished.Add(1, eventTag);
            }
            catch (Exception exception) when (exception is not OperationCanceledException)
            {
                activity?.AddException(exception);
                activity?.SetStatus(ActivityStatusCode.Error, exception.Message);
                item.Attempts += 1;
                if (item.Attempts >= maxAttempts)
                {
                    FinCoreTelemetry.OutboxEventsDeadLettered.Add(1, eventTag);
                    db.OutboxDeadLetters.Add(new OutboxDeadLetterRecord
                    {
                        OutboxEventId = item.Id,
                        EventType = item.EventType,
                        Payload = item.PayloadJson,
                        FailureReason = Truncate(exception.Message, 300),
                        CreatedAt = DateTimeOffset.UtcNow
                    });
                    item.PublishedAt = DateTimeOffset.UtcNow;
                    Log.Error(exception, "Outbox event {EventId} of type {EventType} moved to dead letters after {Attempts} attempts",
                        item.Id, item.EventType, item.Attempts);
                }
                else
                {
                    item.NextAttemptAt = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(Math.Pow(2, item.Attempts));
                    Log.Warning(exception, "Outbox event {EventId} delivery failed (attempt {Attempts})", item.Id, item.Attempts);
                }
            }
        }

        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return events.Count;
    }

    private static string Truncate(string value, int maxLength) =>
        value.Length <= maxLength ? value : value[..maxLength];
}
