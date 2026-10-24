using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record OpenDisputeRequest(string Reason);
public sealed record EvidenceRequest(string Note);
public sealed record DecisionRequest(string Outcome, string? Resolution);

/// <summary>Chargeback simulation (spec section 61): the payer opens, staff investigate and decide.</summary>
[Route("api/v1/payments/{paymentId:guid}/dispute")]
[Authorize]
[Tags("disputes")]
public sealed class PaymentDisputesController(DisputesService disputes, FinCoreDbContext db) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<PaymentDisputeView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Open(Guid paymentId, OpenDisputeRequest request,
        CancellationToken cancellationToken)
    {
        var dispute = await disputes.OpenAsync(CustomerId, paymentId, request.Reason, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, dispute);
    }

    [HttpGet]
    [ProducesResponseType<PaymentDisputeView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Get(Guid paymentId, CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        var isStaff = EndpointSupport.HasAnyRole(roles, DisputesService.StaffRoles);
        var dispute = await disputes.GetAsync(CustomerId, isStaff, paymentId, cancellationToken);
        return dispute is null
            ? ProblemResult(404, "not_found", "Resource not found", "Dispute not found.")
            : Ok(dispute);
    }
}

[Route("api/v1/admin/disputes")]
[Tags("disputes")]
public sealed class AdminDisputesController(DisputesService disputes) : FinCoreControllerBase
{
    [HttpGet]
    [Authorize(Policy = Policies.FraudStaff)]
    public async Task<IActionResult> List([FromQuery] string? status, [FromQuery] int? limit,
        CancellationToken cancellationToken)
    {
        var items = await disputes.ListAsync(status, Math.Clamp(limit ?? 50, 1, 200), cancellationToken);
        return Ok(new { items, nextCursor = (string?)null });
    }

    [HttpPost("{disputeId:guid}/evidence")]
    [Authorize(Policy = Policies.FraudStaff)]
    [ProducesResponseType<PaymentDisputeView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> AddEvidence(Guid disputeId, EvidenceRequest request,
        CancellationToken cancellationToken) =>
        Ok(await disputes.AddEvidenceAsync(CustomerId, disputeId, request.Note, cancellationToken));

    [HttpPost("{disputeId:guid}/decision")]
    [Authorize(Policy = Policies.FraudReview)]
    [ProducesResponseType<PaymentDisputeView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Decide(Guid disputeId, DecisionRequest request,
        CancellationToken cancellationToken) =>
        Ok(await disputes.DecideAsync(CustomerId, disputeId, request.Outcome, request.Resolution,
            cancellationToken));
}
