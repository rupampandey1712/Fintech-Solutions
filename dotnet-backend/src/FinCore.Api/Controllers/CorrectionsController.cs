using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record RefundRequest(long AmountMinor, string? Reason);
public sealed record ReversalRequest(string? Reason);

/// <summary>Refunds and reversals (see CorrectionsService for the rules). Staff: admin or finance_user.</summary>
[Route("api/v1")]
[Authorize(Policy = Policies.Treasury)]
[Tags("corrections")]
public sealed class CorrectionsController(CorrectionsService corrections) : FinCoreControllerBase
{
    [HttpPost("payments/{paymentId:guid}/refund")]
    [ProducesResponseType<RefundResult>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Refund(Guid paymentId, RefundRequest request,
        [FromHeader(Name = "Idempotency-Key")] string[]? idempotencyKeys, CancellationToken cancellationToken)
    {
        if (idempotencyKeys is not [var key] || string.IsNullOrWhiteSpace(key))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed",
                "Idempotency-Key is required.");
        return StatusCode(StatusCodes.Status201Created,
            await corrections.RefundAsync(CustomerId, paymentId, request.AmountMinor, request.Reason, key,
                cancellationToken));
    }

    [HttpPost("payments/{paymentId:guid}/reversal")]
    [ProducesResponseType<PaymentReversalResult>(StatusCodes.Status200OK)]
    public async Task<IActionResult> ReversePayment(Guid paymentId, ReversalRequest request,
        [FromHeader(Name = "Idempotency-Key")] string[]? idempotencyKeys, CancellationToken cancellationToken)
    {
        if (idempotencyKeys is not [var key] || string.IsNullOrWhiteSpace(key))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed",
                "Idempotency-Key is required.");
        return Ok(await corrections.ReversePaymentAsync(CustomerId, paymentId, request.Reason ?? string.Empty, key,
            cancellationToken));
    }

    [HttpPost("admin/journal-entries/{journalId:guid}/reversal")]
    [ProducesResponseType<JournalReversalResult>(StatusCodes.Status200OK)]
    public async Task<IActionResult> ReverseJournal(Guid journalId, ReversalRequest request,
        [FromHeader(Name = "Idempotency-Key")] string[]? idempotencyKeys, CancellationToken cancellationToken)
    {
        if (idempotencyKeys is not [var key] || string.IsNullOrWhiteSpace(key))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed",
                "Idempotency-Key is required.");
        return Ok(await corrections.ReverseJournalAsync(CustomerId, journalId, request.Reason ?? string.Empty, key,
            cancellationToken));
    }
}

/// <summary>Staff lookups for the operations UI (payments with refunded totals, deposits with reversals).</summary>
[Route("api/v1/admin/customers/{customerId:guid}")]
[Authorize(Policy = Policies.Treasury)]
[Tags("corrections")]
public sealed class CorrectionsAdminController(CorrectionsService corrections) : FinCoreControllerBase
{
    [HttpGet("payments")]
    public async Task<IActionResult> CustomerPayments(Guid customerId, CancellationToken cancellationToken) =>
        Ok(new { items = await corrections.ListCustomerPaymentsAsync(customerId, cancellationToken) });

    [HttpGet("deposits")]
    public async Task<IActionResult> CustomerDeposits(Guid customerId, CancellationToken cancellationToken) =>
        Ok(new { items = await corrections.ListCustomerDepositsAsync(customerId, cancellationToken) });
}

/// <summary>Refund history: visible to the payer and to staff, like the payment itself.</summary>
[Route("api/v1/payments/{paymentId:guid}/refunds")]
[Authorize]
[Tags("corrections")]
public sealed class RefundsController(CorrectionsService corrections, FinCoreDbContext db) : FinCoreControllerBase
{
    [HttpGet]
    [ProducesResponseType<RefundPage>(StatusCodes.Status200OK)]
    public async Task<IActionResult> List(Guid paymentId, CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        var page = await corrections.ListRefundsAsync(CustomerId,
            EndpointSupport.HasAnyRole(roles, "admin", "finance_user"), paymentId, cancellationToken);
        return page is null
            ? ProblemResult(404, "not_found", "Resource not found", "Payment not found.")
            : Ok(page);
    }
}
