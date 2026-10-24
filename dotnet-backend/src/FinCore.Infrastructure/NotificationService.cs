using FinCore.Application;
using Microsoft.EntityFrameworkCore;
using Serilog;

namespace FinCore.Infrastructure;

/// <summary>Mock email/SMS/push provider. A real implementation would call SendGrid/Twilio/FCM.</summary>
public static class NotificationGateway
{
    public static Task<string> SendAsync(string channel, string subject, string body)
    {
        Log.Information("Mock notification provider accepted {Channel} message '{Subject}'", channel,
            Truncate(subject, 80));
        return Task.FromResult("sent");
    }

    private static string Truncate(string value, int maxLength) =>
        value.Length <= maxLength ? value : value[..maxLength];
}

public static class NotificationService
{
    // in_app is the product's own surface, so it is delivered by writing the row. The rest model
    // external providers and go through the queued -> sent lifecycle below.
    public static readonly string[] InstantChannels = ["in_app"];
    public static readonly string[] QueuedChannels = ["email", "sms", "push"];

    public static async Task QueueNotificationAsync(FinCoreDbContext db, Guid customerId, string eventType,
        string subject, string body, CancellationToken cancellationToken = default)
    {
        var now = DateTimeOffset.UtcNow;
        foreach (var channel in InstantChannels)
        {
            db.Notifications.Add(new NotificationRecord
            {
                CustomerId = customerId, Channel = channel, EventType = eventType,
                Subject = Truncate(subject, 160), Body = Truncate(body, 1000), Status = "sent",
                SentAt = now, CreatedAt = now
            });
        }
        foreach (var channel in QueuedChannels)
        {
            db.Notifications.Add(new NotificationRecord
            {
                CustomerId = customerId, Channel = channel, EventType = eventType,
                Subject = Truncate(subject, 160), Body = Truncate(body, 1000), Status = "queued",
                CreatedAt = now
            });
        }
        // The transport-visible record of the request (spec section 17: NotificationRequested).
        // Handlers on other services subscribe to it; the in-process handler ignores it because
        // the fan-out already happened here.
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = customerId,
            EventType = "notification.requested.v1",
            SchemaVersion = 1,
            PayloadJson = System.Text.Json.JsonSerializer.Serialize(new
            {
                customerId, eventType, channels = QueuedChannels
            }),
            CreatedAt = now
        });
        Log.Information("Notification {EventType} requested for customer {CustomerId} on channels {Channels}",
            eventType, customerId, string.Join(",", InstantChannels.Concat(QueuedChannels)));
        await db.SaveChangesAsync(cancellationToken);
    }

    /// <summary>Delivers queued notifications for one event through the (mock) gateway.</summary>
    public static async Task<int> DispatchQueuedAsync(FinCoreDbContext db, Guid customerId, string eventType,
        CancellationToken cancellationToken = default)
    {
        var rows = await db.Notifications
            .Where(notice => notice.CustomerId == customerId && notice.EventType == eventType
                && notice.Status == "queued" && QueuedChannels.Contains(notice.Channel))
            .OrderBy(notice => notice.CreatedAt).Take(20).ToListAsync(cancellationToken);
        foreach (var row in rows)
        {
            row.Status = await NotificationGateway.SendAsync(row.Channel, row.Subject, row.Body);
            row.SentAt = DateTimeOffset.UtcNow;
        }
        if (rows.Count > 0) await db.SaveChangesAsync(cancellationToken);
        return rows.Count;
    }

    private static string Truncate(string value, int maxLength) =>
        value.Length <= maxLength ? value : value[..maxLength];
}
