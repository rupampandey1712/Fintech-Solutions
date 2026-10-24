using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreateAccountRequest(string? AccountType, string? Currency);

[Route("api/v1/accounts")]
[Authorize]
[Tags("accounts")]
public sealed class AccountsController(IAccountService accounts) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<AccountView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Create(CreateAccountRequest request, CancellationToken cancellationToken)
    {
        // The manual check preserves the exact error body the minimal API returned (detail and
        // the "accountType/currency" field), which is narrower than the generic model validation.
        if (request.AccountType is not ("checking" or "savings" or "wallet" or "merchant")
            || string.IsNullOrWhiteSpace(request.Currency)
            || request.Currency.Length != 3
            || request.Currency.Any(c => c is < 'A' or > 'Z'))
        {
            return ProblemResult(422, "validation_error", "Request validation failed",
                "Account type or currency is invalid.",
                new[] { new { field = "accountType/currency", code = "invalid_value" } });
        }
        var account = await accounts.CreateAsync(CustomerId, request.AccountType, request.Currency, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, account);
    }

    [HttpGet]
    [ProducesResponseType<AccountPage>(StatusCodes.Status200OK)]
    public async Task<IActionResult> List(CancellationToken cancellationToken) =>
        Ok(await accounts.ListAsync(CustomerId, cancellationToken));

    [HttpGet("{accountId:guid}")]
    [ProducesResponseType<AccountView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Get(Guid accountId, CancellationToken cancellationToken)
    {
        var account = await accounts.GetAsync(CustomerId, accountId, cancellationToken);
        return account is null
            ? ProblemResult(404, "not_found", "Account not found")
            : Ok(account);
    }
}
