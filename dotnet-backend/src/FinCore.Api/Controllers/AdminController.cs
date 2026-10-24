using System.ComponentModel.DataAnnotations;
using System.Text.Json;
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

public sealed record CustomerStatusRequest([Required] string Status);
public sealed record FraudRulePatchRequest(bool? Enabled, int? Weight, long? ThresholdMinor);
public sealed record AccountStatusRequest([Required] string Status);
public sealed record UpdateRolesRequest(string[] Roles);
public sealed record DeadLetterView(Guid Id, Guid OutboxEventId, string EventType, string FailureReason,
    bool Replayed, DateTimeOffset CreatedAt);

/// <summary>Admin-only management endpoints (spec section 25). Treasury lookups are separate.</summary>
[Route("api/v1/admin")]
[Authorize(Policy = Policies.Admin)]
[Tags("admin")]
public sealed class AdminController(FinCoreDbContext db, FinCoreCache cache) : FinCoreControllerBase
{
    public static readonly string[] CustomerStatuses = ["pending", "active", "suspended", "blocked", "closed"];
    public static readonly string[] AccountStatuses = ["active", "frozen", "suspended", "closed"];
    public static readonly string[] KnownRoles =
        ["customer", "admin", "operations_user", "fraud_analyst", "compliance_officer", "finance_user"];

    [HttpGet("customers")]
    public async Task<IActionResult> ListCustomers([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 50, 1, 500);
        var rows = await db.Customers.AsNoTracking()
            .OrderByDescending(customer => customer.CreatedAt).ThenByDescending(customer => customer.Id)
            .Take(pageSize)
            .Select(customer => new { customer.Id, customer.EmailNormalized, customer.Status, customer.Roles, customer.CreatedAt })
            .ToListAsync(cancellationToken);
        var items = rows.Select(row => new AdminCustomerView(row.Id, row.EmailNormalized, row.Status,
            CustomerRoles.Parse(row.Roles), row.CreatedAt)).ToArray();
        return Ok(new { items });
    }

    [HttpPatch("customers/{customerId:guid}/status")]
    [ProducesResponseType<object>(StatusCodes.Status200OK)]
    public async Task<IActionResult> UpdateCustomerStatus(Guid customerId, CustomerStatusRequest request,
        CancellationToken cancellationToken)
    {
        if (!CustomerStatuses.Contains(request.Status))
            return ProblemResult(422, "validation_error", "Request validation failed", "Status is invalid.");
        var customer = await db.Customers.SingleOrDefaultAsync(item => item.Id == customerId, cancellationToken);
        if (customer is null)
            return ProblemResult(404, "not_found", "Resource not found", "Customer not found");
        var previous = customer.Status;
        customer.Status = request.Status;
        await AuditService.RecordAuditAsync(db, CustomerId, "admin.customer_status_changed", "customer",
            customer.Id, oldValue: JsonSerializer.Serialize(new { status = previous }),
            newValue: JsonSerializer.Serialize(new { status = customer.Status }), cancellationToken: cancellationToken);
        if (request.Status is "blocked" or "suspended" or "closed"
            && previous is not ("blocked" or "suspended" or "closed"))
        {
            // Spec section 22: an AccountBlocked notification. Emitted as an event so the
            // notification service, not the admin endpoint, owns channel fan-out.
            db.OutboxEvents.Add(new OutboxEventRecord
            {
                AggregateId = customer.Id,
                EventType = "customer.blocked.v1",
                SchemaVersion = 1,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    customerId = customer.Id, status = request.Status, previousStatus = previous
                }),
                CreatedAt = DateTimeOffset.UtcNow
            });
        }
        await db.SaveChangesAsync(cancellationToken);
        return Ok(new { id = customer.Id, email = customer.EmailNormalized, status = customer.Status });
    }

    [HttpGet("fraud-rules")]
    public async Task<IActionResult> ListFraudRules(CancellationToken cancellationToken)
    {
        var items = await db.FraudRules.AsNoTracking().OrderBy(rule => rule.Code)
            .Select(rule => new FraudRuleView(rule.Id, rule.Code, rule.Description, rule.ThresholdMinor,
                rule.Weight, rule.Enabled))
            .ToListAsync(cancellationToken);
        return Ok(new { items });
    }

    [HttpPatch("fraud-rules/{code}")]
    [ProducesResponseType<FraudRuleView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> UpdateFraudRule(string code, FraudRulePatchRequest request,
        CancellationToken cancellationToken)
    {
        var rule = await db.FraudRules.SingleOrDefaultAsync(item => item.Code == code, cancellationToken);
        if (rule is null)
            return ProblemResult(404, "not_found", "Resource not found", "Fraud rule not found");
        var previous = new { rule.Enabled, rule.Weight, rule.ThresholdMinor };
        if (request.Enabled is not null) rule.Enabled = request.Enabled.Value;
        if (request.Weight is not null) rule.Weight = request.Weight.Value;
        if (request.ThresholdMinor is not null) rule.ThresholdMinor = request.ThresholdMinor.Value;
        await AuditService.RecordAuditAsync(db, CustomerId, "admin.fraud_rule_updated",
            "fraud_rule", rule.Id,
            oldValue: JsonSerializer.Serialize(previous),
            newValue: JsonSerializer.Serialize(new { rule.Enabled, rule.Weight, rule.ThresholdMinor }),
            cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        // After the commit, so a concurrent reader cannot re-cache the old rules (see FinCoreCache).
        await cache.InvalidateAsync(FinCoreCache.FraudRulesKey);
        return Ok(new FraudRuleView(rule.Id, rule.Code, rule.Description, rule.ThresholdMinor,
            rule.Weight, rule.Enabled));
    }

    [HttpGet("audit-logs")]
    public async Task<IActionResult> AuditLogs([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 100, 1, 500);
        var items = await db.AuditLogs.AsNoTracking()
            .OrderByDescending(log => log.CreatedAt).ThenByDescending(log => log.Id)
            .Take(pageSize)
            .Select(log => new AuditLogView(log.Id, log.ActorId, log.Action, log.EntityType, log.EntityId,
                log.IpAddress, log.CorrelationId, log.CreatedAt))
            .ToListAsync(cancellationToken);
        return Ok(new { items });
    }

    [HttpPatch("accounts/{accountId:guid}/status")]
    public async Task<IActionResult> UpdateAccountStatus(Guid accountId, AccountStatusRequest request,
        CancellationToken cancellationToken)
    {
        if (!AccountStatuses.Contains(request.Status))
            return ProblemResult(422, "validation_error", "Request validation failed", "Status is invalid.");
        var account = await db.Accounts.SingleOrDefaultAsync(item => item.Id == accountId, cancellationToken);
        if (account is null)
            return ProblemResult(404, "not_found", "Resource not found", "Account not found");
        var previous = account.Status;
        account.Status = request.Status;
        await AuditService.RecordAuditAsync(db, CustomerId, "admin.account_status_changed", "account", account.Id,
            oldValue: JsonSerializer.Serialize(new { status = previous }),
            newValue: JsonSerializer.Serialize(new { status = account.Status }), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        return Ok(new { id = account.Id, status = account.Status });
    }

    // ------------------------------------------------------------------ user management (§25)

    [HttpGet("users")]
    public async Task<IActionResult> ListUsers([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 100, 1, 500);
        var rows = await db.Customers.AsNoTracking()
            .OrderByDescending(customer => customer.CreatedAt).ThenByDescending(customer => customer.Id)
            .Take(pageSize)
            .Select(customer => new { customer.Id, customer.EmailNormalized, customer.Status, customer.Roles, customer.CreatedAt })
            .ToListAsync(cancellationToken);
        var items = rows.Select(row => new AdminCustomerView(row.Id, row.EmailNormalized, row.Status,
            CustomerRoles.Parse(row.Roles), row.CreatedAt)).ToArray();
        return Ok(new { items });
    }

    [HttpPatch("users/{userId:guid}/roles")]
    [ProducesResponseType<AdminCustomerView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> UpdateUserRoles(Guid userId, UpdateRolesRequest request,
        CancellationToken cancellationToken)
    {
        if (request.Roles.Length is < 1 or > 6 || request.Roles.Any(role => !KnownRoles.Contains(role)))
            return ProblemResult(422, "validation_error", "Request validation failed", "Roles are invalid.");
        var target = await db.Customers.SingleOrDefaultAsync(customer => customer.Id == userId, cancellationToken);
        if (target is null)
            return ProblemResult(404, "not_found", "Resource not found", "User not found");
        var requested = request.Roles.Distinct().Order().ToArray();
        var previous = CustomerRoles.Parse(target.Roles).Order().ToArray();
        // An admin cannot drop their own admin role (lockout guard).
        if (target.Id == CustomerId && !requested.Contains("admin"))
            return ProblemResult(422, "cannot_remove_own_admin_role", "Request validation failed",
                "You cannot remove your own admin role.");
        if (!requested.SequenceEqual(previous))
        {
            target.Roles = JsonSerializer.Serialize(requested.Distinct());
            await AuditService.RecordAuditAsync(db, CustomerId, "admin.user_roles_changed", "user",
                target.Id, oldValue: JsonSerializer.Serialize(new { roles = previous }),
                newValue: JsonSerializer.Serialize(new { roles = requested }), cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
        }
        return Ok(new AdminCustomerView(target.Id, target.EmailNormalized, target.Status,
            CustomerRoles.Parse(target.Roles), target.CreatedAt));
    }

    // ------------------------------------------------- outbox dead letters (§18-19, manual replay)

    [HttpGet("outbox/dead-letters")]
    public async Task<IActionResult> DeadLetters([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 100, 1, 500);
        var rows = await db.OutboxDeadLetters.AsNoTracking()
            .OrderByDescending(letter => letter.CreatedAt).ThenByDescending(letter => letter.Id)
            .Take(pageSize).ToListAsync(cancellationToken);
        var items = rows.Select(letter => new DeadLetterView(letter.Id, letter.OutboxEventId, letter.EventType,
            letter.FailureReason, letter.ReplayedAt is not null, letter.CreatedAt)).ToArray();
        return Ok(new { items });
    }

    [HttpPost("outbox/dead-letters/{deadLetterId:guid}/replay")]
    public async Task<IActionResult> ReplayDeadLetter(Guid deadLetterId, CancellationToken cancellationToken)
    {
        // The original dead letter is kept (never deleted) and marked, so an operator can always
        // see what was replayed, when, and why it failed in the first place.
        var deadLetter = await db.OutboxDeadLetters.SingleOrDefaultAsync(letter => letter.Id == deadLetterId,
            cancellationToken);
        if (deadLetter is null)
            return ProblemResult(404, "not_found", "Resource not found", "Dead letter not found");
        if (deadLetter.ReplayedAt is not null)
            return ProblemResult(409, "dead_letter_already_replayed", "Conflict",
                "This dead letter has already been replayed.");
        var replay = new OutboxEventRecord
        {
            AggregateId = deadLetter.OutboxEventId,
            EventType = deadLetter.EventType,
            SchemaVersion = 1,
            PayloadJson = deadLetter.Payload,
            CreatedAt = DateTimeOffset.UtcNow
        };
        db.OutboxEvents.Add(replay);
        deadLetter.ReplayedAt = DateTimeOffset.UtcNow;
        await AuditService.RecordAuditAsync(db, CustomerId, "admin.dead_letter_replayed",
            "outbox_dead_letter", deadLetter.Id,
            newValue: JsonSerializer.Serialize(new { eventType = deadLetter.EventType, newOutboxEventId = replay.Id }),
            cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        return Ok(new { deadLetterId = deadLetter.Id, newOutboxEventId = replay.Id });
    }
}
