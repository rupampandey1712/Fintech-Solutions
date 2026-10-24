using System.ComponentModel.DataAnnotations;
using FinCore.Application;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

// Attributes target the constructor parameters (no "property:" prefix); see pitfall 9.1 — with
// [property:] MVC throws on every request.
public sealed record RegisterRequest([Required, EmailAddress] string Email,
    [Required, MinLength(12), MaxLength(128)] string Password);
public sealed record LoginRequest([Required, EmailAddress] string Email,
    [Required, MinLength(1), MaxLength(128)] string Password);
public sealed record RefreshRequest([Required] string RefreshToken);
public sealed record CustomerResponse(Guid Id, string Email, string Status);
public sealed record TokenResponse(string AccessToken, string TokenType, int ExpiresIn, string RefreshToken);

[Route("api/v1/auth")]
[AllowAnonymous]
[Tags("authentication")]
public sealed class AuthController(AuthenticationService authentication, IRefreshTokenService refreshTokens,
    RateLimiter rateLimiter) : FinCoreControllerBase
{
    // [ApiController] validates the DataAnnotations above before the action runs; failures become
    // 422 problems through ProblemResponses.InvalidModelState. The manual Validate() call is gone.
    [HttpPost("register")]
    [ProducesResponseType<CustomerResponse>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Register(RegisterRequest request, CancellationToken cancellationToken)
    {
        var customer = await authentication.RegisterAsync(request.Email, request.Password, cancellationToken);
        return StatusCode(StatusCodes.Status201Created,
            new CustomerResponse(customer.Id, customer.Email, customer.Status));
    }

    [HttpPost("login")]
    [ProducesResponseType<TokenResponse>(StatusCodes.Status200OK)]
    public async Task<ActionResult<TokenResponse>> Login(LoginRequest request, CancellationToken cancellationToken)
    {
        // Login attempts per account, whatever IP they come from, so spreading a password-guessing
        // run across many IPs does not multiply its budget. Checked before the password, and for
        // unknown emails too, so the refusal reveals nothing about whether the account exists.
        var limits = rateLimiter.Options;
        if (limits.Enabled && limits.LoginPerAccountPerMinute > 0 && !await rateLimiter.TryAcquireAsync("login_account",
                RateLimiter.AccountPrincipal(AuthenticationService.NormalizeEmail(request.Email)),
                limits.LoginPerAccountPerMinute))
        {
            Response.Headers.RetryAfter = RateLimiter.WindowSeconds.ToString();
            return ProblemResult(StatusCodes.Status429TooManyRequests, "rate_limited", "Too many requests",
                "Rate limit exceeded. Retry later.");
        }
        var result = await authentication.LoginAsync(request.Email, request.Password, cancellationToken);
        var refreshToken = await refreshTokens.IssueAsync(result.Customer.Id, cancellationToken);
        return new TokenResponse(result.Token.Value, "Bearer", result.Token.ExpiresInSeconds, refreshToken);
    }

    [HttpPost("refresh")]
    [ProducesResponseType<TokenResponse>(StatusCodes.Status200OK)]
    public async Task<ActionResult<TokenResponse>> Refresh(RefreshRequest request, CancellationToken cancellationToken)
    {
        var result = await refreshTokens.RotateAsync(request.RefreshToken, cancellationToken);
        return new TokenResponse(result.AccessToken.Value, "Bearer", result.AccessToken.ExpiresInSeconds,
            result.RefreshToken);
    }

    [HttpPost("logout")]
    [ProducesResponseType(StatusCodes.Status204NoContent)]
    public async Task<IActionResult> Logout(RefreshRequest request, CancellationToken cancellationToken)
    {
        await refreshTokens.RevokeAsync(request.RefreshToken, cancellationToken);
        return NoContent();
    }
}

[Route("api/v1/customers")]
[Authorize]
[Tags("customers")]
public sealed class CustomersController(ICustomerIdentityRepository customers) : FinCoreControllerBase
{
    [HttpGet("me")]
    [ProducesResponseType<CustomerResponse>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Me(CancellationToken cancellationToken)
    {
        var customer = await customers.FindByIdAsync(CustomerId, cancellationToken);
        if (customer is null) return Unauthorized();
        if (customer.Status is "suspended" or "blocked" or "closed") return Forbid();
        return Ok(new CustomerResponse(customer.Id, customer.Email, customer.Status));
    }
}
