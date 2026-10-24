using System.Text.Json;
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

public sealed record FraudCaseReviewRequest(string Resolution, string? Note);

public sealed record InvestigationStepView(string Tool, string Label);

public sealed record InvestigationView(Guid CaseId, string Recommendation, string Confidence, string Summary,
    IReadOnlyList<string> KeyFindings, string SuggestedNote, IReadOnlyList<InvestigationStepView> Steps,
    string Model, DateTimeOffset GeneratedAt);

public sealed record AgentStatusView(bool Enabled, string Provider, string Model);

/// <summary>Fraud investigator limits: model turns per investigation and the wall-clock budget.</summary>
public sealed record InvestigatorSettings(int MaxSteps, TimeSpan Timeout);

[Route("api/v1/fraud")]
[Authorize]
[Tags("fraud")]
public sealed class FraudController(FinCoreDbContext db, IAuthorizationService authorization) : FinCoreControllerBase
{
    public static readonly string[] CaseStatuses = ["open", "investigating", "escalated", "closed", "false_positive"];

    [HttpGet("cases")]
    public async Task<IActionResult> List([FromQuery] string? status, [FromQuery] int? limit,
        CancellationToken cancellationToken)
    {
        if (status is not null && !CaseStatuses.Contains(status))
            return ProblemResult(422, "validation_error", "Request validation failed", "Status filter is invalid.");
        // Role-dependent results: reviewers see every case, customers only their own — the check
        // stays in the action, so [Authorize] can remain plain authentication.
        var seeAll = await IsAsync(Policies.FraudReview, cancellationToken);
        var query = db.FraudCases.AsNoTracking();
        if (!seeAll) query = query.Where(item => item.CustomerId == CustomerId);
        if (status is not null) query = query.Where(item => item.Status == status);
        var pageSize = Math.Clamp(limit ?? 50, 1, 200);
        var items = await query.OrderByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id)
            .Take(pageSize).ToListAsync(cancellationToken);
        return Ok(new
        {
            items = items.Select(item => new FraudCaseView(item.Id, item.PaymentId, item.CustomerId, item.Score,
                item.Level, item.Status, JsonText.ParseStringArray(item.Reasons), item.Resolution,
                item.ReviewedAt, item.CreatedAt)).ToArray(),
            nextCursor = (string?)null
        });
    }

    [HttpPost("cases/{caseId:guid}/review")]
    [Authorize(Policy = Policies.FraudReview)]
    [ProducesResponseType<FraudCaseView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Review(Guid caseId, FraudCaseReviewRequest request,
        CancellationToken cancellationToken)
    {
        if (request.Resolution is not ("closed" or "false_positive" or "escalated"))
            return ProblemResult(422, "validation_error", "Request validation failed",
                "resolution must be closed, false_positive or escalated.");
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        // Locked so concurrent reviews of one case serialize; the second sees a final status.
        var fraudCase = (await db.FraudCases
            .FromSql($"SELECT * FROM fraud_cases WHERE id = {caseId} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault();
        if (fraudCase is null)
            return ProblemResult(404, "not_found", "Resource not found", "Fraud case not found");
        if (fraudCase.Status is "closed" or "false_positive")
            return ProblemResult(409, "invalid_state", "Conflict", "Fraud case is already resolved.");

        var previous = fraudCase.Status;
        fraudCase.Status = request.Resolution;
        fraudCase.Resolution = request.Note is { Length: <= 200 } ? request.Note : request.Note?[..200];
        fraudCase.ReviewedBy = CustomerId;
        fraudCase.ReviewedAt = DateTimeOffset.UtcNow;
        // Release or refund the held payment in the same transaction as the decision; a
        // PaymentException rolls back via the filter and the disposed transaction.
        var paymentStatus = await PaymentHoldService.SettleAsync(db, fraudCase.PaymentId, request.Resolution,
            cancellationToken);
        await AuditService.RecordAuditAsync(db, CustomerId, "fraud.case.reviewed", "fraud_case", fraudCase.Id,
            oldValue: JsonSerializer.Serialize(new { status = previous }),
            newValue: JsonSerializer.Serialize(new
            {
                status = fraudCase.Status, resolution = fraudCase.Resolution, paymentStatus
            }), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Ok(new FraudCaseView(fraudCase.Id, fraudCase.PaymentId, fraudCase.CustomerId,
            fraudCase.Score, fraudCase.Level, fraudCase.Status, JsonText.ParseStringArray(fraudCase.Reasons),
            fraudCase.Resolution, fraudCase.ReviewedAt, fraudCase.CreatedAt));
    }

    // ----------------------------------------------------------------------------------------
    // Fraud investigator agent: gathers evidence and drafts a recommendation. It never decides;
    // the reviewer acts on its draft through Review above.

    [HttpGet("agent/status")]
    [ProducesResponseType<AgentStatusView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> AgentStatus([FromServices] IInvestigatorChatClientFactory models,
        CancellationToken cancellationToken)
    {
        var configured = models.Create() is not null;
        return Ok(new AgentStatusView(configured && await IsAsync(Policies.FraudReview, cancellationToken),
            models.Provider, models.Model));
    }

    [HttpPost("cases/{caseId:guid}/investigate")]
    [Authorize(Policy = Policies.FraudReview)]
    [ProducesResponseType<InvestigationView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Investigate(Guid caseId, [FromServices] IInvestigatorChatClientFactory models,
        [FromServices] InvestigatorSettings settings, CancellationToken cancellationToken)
    {
        var model = models.Create();
        if (model is null)
            return ProblemResult(503, "agent_not_configured", "Service unavailable",
                "The fraud investigator is not configured.");
        var fraudCase = await db.FraudCases.AsNoTracking().SingleOrDefaultAsync(item => item.Id == caseId, cancellationToken);
        if (fraudCase is null)
            return ProblemResult(404, "not_found", "Resource not found", "Fraud case not found");
        if (fraudCase.Status is "closed" or "false_positive")
            return ProblemResult(409, "invalid_state", "Conflict", "Fraud case is already resolved.");

        InvestigationResult result;
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        budget.CancelAfter(settings.Timeout);
        try
        {
            result = await FraudInvestigator.RunAsync(model, new InvestigationFacts(db, fraudCase), settings.MaxSteps,
                budget.Token);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return ProblemResult(504, "agent_timeout", "Gateway timeout", "The investigation took too long.");
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            // Provider outage, quota, malformed model output: reported, never surfaced raw.
            return ProblemResult(502, "agent_failed", "Bad gateway", "The investigation could not be completed.");
        }
        if (result.Report is not { } report)
            // The model ran out of turns, or stopped without submitting a report.
            return ProblemResult(502, "agent_incomplete", "Bad gateway", "The investigation ended without a report.");

        // Recorded so every AI-assisted review leaves a trace of what the agent saw and suggested.
        await AuditService.RecordAuditAsync(db, CustomerId, "fraud.case.investigated", "fraud_case", fraudCase.Id,
            newValue: JsonSerializer.Serialize(new
            {
                recommendation = report.Recommendation, confidence = report.Confidence,
                tools = result.Steps.Select(step => step.Tool).ToArray(), model = models.Model, turns = result.ModelTurns
            }), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        return Ok(new InvestigationView(fraudCase.Id, report.Recommendation, report.Confidence, report.Summary,
            report.KeyFindings, report.SuggestedNote,
            result.Steps.Select(step => new InvestigationStepView(step.Tool, step.Label)).ToArray(),
            models.Model, DateTimeOffset.UtcNow));
    }

    private async Task<bool> IsAsync(string policy, CancellationToken cancellationToken) =>
        (await authorization.AuthorizeAsync(User, policy)).Succeeded;
}
