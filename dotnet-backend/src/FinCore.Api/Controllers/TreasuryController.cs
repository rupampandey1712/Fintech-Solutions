using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record DepositRequest(long AmountMinor, string? Reference);

/// <summary>Staff deposits. The policy replaces the per-endpoint CheckRolesAsync/HasAnyRole calls.</summary>
[Route("api/v1/admin")]
[Authorize(Policy = Policies.Treasury)]
[Tags("treasury")]
public sealed class TreasuryController(TreasuryService treasury) : FinCoreControllerBase
{
    [HttpGet("customers/{customerId:guid}/accounts")]
    public async Task<IActionResult> CustomerAccounts(Guid customerId, CancellationToken cancellationToken) =>
        Ok(new { items = await treasury.ListCustomerAccountsAsync(customerId, cancellationToken) });

    [HttpPost("accounts/{accountId:guid}/deposits")]
    [ProducesResponseType<DepositView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Deposit(Guid accountId, DepositRequest request,
        [FromHeader(Name = "Idempotency-Key")] string[]? idempotencyKeys, CancellationToken cancellationToken)
    {
        if (idempotencyKeys is not [var key] || string.IsNullOrWhiteSpace(key))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed",
                "Idempotency-Key is required.");
        var deposit = await treasury.DepositAsync(CustomerId, accountId, request.AmountMinor, request.Reference,
            key, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, deposit);
    }
}
