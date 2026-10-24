using System.Text.Json;
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

public sealed record AlertStatusRequest(string Status, string? Note);

[Route("api/v1/aml")]
[Authorize]
[Tags("aml")]
public sealed class AmlController(FinCoreDbContext db, IAuthorizationService authorization) : FinCoreControllerBase
{
    [HttpGet("alerts")]
    public async Task<IActionResult> List([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        // Role-dependent results: reviewers see every alert, customers only their own.
        var seeAll = await IsAsync(Policies.Compliance, cancellationToken);
        var query = db.AmlAlerts.AsNoTracking();
        if (!seeAll) query = query.Where(item => item.CustomerId == CustomerId);
        var pageSize = Math.Clamp(limit ?? 50, 1, 200);
        var rows = await query.OrderByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id)
            .Take(pageSize).ToListAsync(cancellationToken);
        var items = rows.Select(item => new AmlAlertView(item.Id, item.CustomerId, item.PaymentId, item.RuleCode,
            item.Status, ParseDetails(item.Details), item.CreatedAt)).ToArray();
        return Ok(new { items, nextCursor = (string?)null });
    }

    [HttpPatch("alerts/{alertId:guid}/status")]
    [Authorize(Policy = Policies.Compliance)]
    public async Task<IActionResult> UpdateStatus(Guid alertId, AlertStatusRequest request,
        CancellationToken cancellationToken)
    {
        if (request.Status is not ("open" or "investigating" or "escalated" or "closed" or "false_positive"))
            return ProblemResult(422, "validation_error", "Request validation failed", "Status is invalid.");
        var alert = await db.AmlAlerts.SingleOrDefaultAsync(item => item.Id == alertId, cancellationToken);
        if (alert is null)
            return ProblemResult(404, "not_found", "Resource not found", "AML alert not found");
        var previous = alert.Status;
        alert.Status = request.Status;
        if (!string.IsNullOrWhiteSpace(request.Note))
        {
            var details = ParseDetails(alert.Details);
            var merged = new Dictionary<string, JsonElement>();
            if (details.ValueKind is JsonValueKind.Object)
            {
                foreach (var property in details.EnumerateObject())
                    if (property.Name != "statusHistory")
                        merged[property.Name] = property.Value.Clone();
            }
            var history = merged.TryGetValue("statusHistory", out var existing)
                && existing.ValueKind == JsonValueKind.Array
                ? existing.EnumerateArray().ToList()
                : [];
            history.Add(JsonSerializer.SerializeToElement(new
            {
                status = request.Status, note = request.Note, by = CustomerId,
                at = DateTimeOffset.UtcNow
            }));
            merged["statusHistory"] = JsonSerializer.SerializeToElement(history);
            alert.Details = JsonSerializer.Serialize(merged);
        }
        await AuditService.RecordAuditAsync(db, CustomerId, "aml.alert.status_changed", "aml_alert", alert.Id,
            oldValue: JsonSerializer.Serialize(new { status = previous }),
            newValue: JsonSerializer.Serialize(new { status = alert.Status }), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        return Ok(new { id = alert.Id, status = alert.Status });
    }

    private async Task<bool> IsAsync(string policy, CancellationToken cancellationToken) =>
        (await authorization.AuthorizeAsync(User, policy)).Succeeded;

    private static JsonElement ParseDetails(string? details)
    {
        if (string.IsNullOrWhiteSpace(details)) return default;
        try
        {
            return JsonDocument.Parse(details).RootElement.Clone();
        }
        catch (JsonException)
        {
            // Stored details are data, not a client error; show them as empty.
            return default;
        }
    }
}
