using FinCore.Application;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public static class AuditService
{
    public static async Task RecordAuditAsync(FinCoreDbContext db, Guid? actorId, string action, string entityType,
        Guid? entityId, string? correlationId = null, string? ipAddress = null, string? oldValue = null,
        string? newValue = null, CancellationToken cancellationToken = default)
    {
        db.AuditLogs.Add(new AuditLogRecord
        {
            ActorId = actorId,
            Action = action,
            EntityType = entityType,
            EntityId = entityId,
            IpAddress = Truncate(ipAddress, 64),
            CorrelationId = Truncate(correlationId, 128),
            OldValue = oldValue,
            NewValue = newValue,
            CreatedAt = DateTimeOffset.UtcNow
        });
        await db.SaveChangesAsync(cancellationToken);
    }

    public static Task RecordAuditAsync(FinCoreDbContext db, Guid? actorId, string action, string entityType,
        Guid? entityId, HttpContext httpContext, string? oldValue = null, string? newValue = null,
        CancellationToken cancellationToken = default)
    {
        var correlationId = httpContext.Items.TryGetValue("CorrelationId", out var value) && value is string text
            ? text
            : httpContext.TraceIdentifier;
        return RecordAuditAsync(db, actorId, action, entityType, entityId, correlationId,
            httpContext.Connection.RemoteIpAddress?.ToString(), oldValue, newValue, cancellationToken);
    }

    private static string? Truncate(string? value, int maxLength) =>
        value is null ? null : value.Length <= maxLength ? value : value[..maxLength];
}
