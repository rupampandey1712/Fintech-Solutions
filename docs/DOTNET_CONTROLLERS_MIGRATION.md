# Migrating the .NET API from Minimal APIs to Controllers

> **Status: completed.** Every HTTP route now lives in `[ApiController]` classes under
> `src/FinCore.Api/Controllers/`; the minimal-API `*Endpoints.cs` files were deleted and
> `Program.cs` calls `app.MapControllers()`. The shared infrastructure of section 4 is
> `Controllers/ControllerInfrastructure.cs` (with the additional policies the newer routes
> needed: `FraudStaff`, `Finance`, `Compliance`, `Operations`). The rest of this document is
> kept as the record of how the migration was designed and verified — the pitfalls in
> section 9 are all real and all handled in the current code.

This guide explains how to replace FinCore's ASP.NET Core **minimal API endpoints** (`app.MapGet(...)`, `app.MapPost(...)`) with **MVC controllers** (`[ApiController]` classes), without changing the HTTP contract in `contracts/openapi.yaml`.

It is written against this repository's real code. Every controller and every piece of shared infrastructure below was compiled and run before it went into this document:

* Payments, authentication, customers/me and treasury were converted in a copy of `dotnet-backend`.
* That build ran against the full Docker stack, side by side with the untouched minimal-API groups.
* The same end-to-end suite that exercises the minimal-API version passed against it: registration, KYC with Azurite, accounts, deposits, payments, idempotent replay, held payments with fraud-review release, Service Bus notifications, refresh/logout and admin access. Nine controller-specific checks passed as well: 422 validation shape, `application/problem+json`, the missing `Idempotency-Key` check, domain errors through the exception filter, 401, policy 403, `customers/me`, and coexistence with minimal APIs.

Three of the pitfalls in [section 9](#9-pitfalls-found-while-verifying-this-guide) were found by those runs, not by reading documentation.

---

## Contents

1. [Should you migrate?](#1-should-you-migrate)
2. [How the API is wired today](#2-how-the-api-is-wired-today)
3. [Concept map: minimal API → controller](#3-concept-map-minimal-api--controller)
4. [Step 1 — Shared controller infrastructure](#4-step-1--shared-controller-infrastructure)
5. [Step 2 — Program.cs](#5-step-2--programcs)
6. [Step 3 — Convert a group: Payments](#6-step-3--convert-a-group-payments)
7. [Step 4 — Anonymous routes and validation: Auth](#7-step-4--anonymous-routes-and-validation-auth)
8. [Step 5 — Role-protected routes: Treasury](#8-step-5--role-protected-routes-treasury)
9. [Pitfalls found while verifying this guide](#9-pitfalls-found-while-verifying-this-guide)
10. [Migration plan for the remaining groups](#10-migration-plan-for-the-remaining-groups)
11. [Testing for contract parity](#11-testing-for-contract-parity)
12. [Per-group checklist and rollback](#12-per-group-checklist-and-rollback)

---

## 1. Should you migrate?

Both models are first-class in ASP.NET Core and run on the same routing, middleware, authentication and DI. The decision is about how the code is organised, not about capability.

| | Minimal APIs (today) | Controllers |
|---|---|---|
| Shape | Lambdas registered in `Map*Endpoints` extension methods | Classes with one method per action |
| Cross-cutting behaviour | Endpoint filters, or repeated inline code | MVC filters (exception, action, authorization, result) applied globally or by attribute |
| Validation | Manual (`Validator.TryValidateObject`), or the newer built-in `AddValidation()` | Automatic with `[ApiController]`; one response factory for every action |
| Authorization | `.RequireAuthorization(...)` per route or group, or inline checks | `[Authorize(Policy = ...)]` on the class or action |
| Overhead | Lower; Native AOT-friendly | Slightly higher; no Native AOT |
| Discoverability | Routes spread across extension methods | Route, verb, policy and response types visible on the class |
| Testing | Integration tests (`WebApplicationFactory`) | Integration tests, and controllers can also be unit-tested as classes |

**What this codebase gains.** The current endpoints repeat the same four pieces of plumbing by hand. Controllers centralise all four:

| Repeated today | Count across the 13 endpoint files | Controller replacement |
|---|---|---|
| `try { … } catch (XException e) { return Problem(…) }` | 14 `try` blocks | One `DomainExceptionFilter` |
| `TryCustomerId(principal, out var id)` then `Results.Unauthorized()` | In almost every authenticated route | A `CustomerId` property on a base class |
| `CurrentRolesAsync` / `CheckRolesAsync` then `HasAnyRole` then `Forbidden` | 18 role checks | `[Authorize(Policy = Policies.Treasury)]` |
| `Validate(request, context)` returning a 422 | Auth routes | `[ApiController]` plus one `InvalidModelStateResponseFactory` |

**What it costs.**
* 42 routes to move.
* A small runtime overhead, which is irrelevant next to the database work each request does.
* Native AOT is no longer possible, but this service doesn't use it.

**Recommendation.** Migrate if the team prefers controller conventions or the API keeps growing; the reduction in repeated code is real. If the main goal is only removing the duplication, the lighter alternative is to keep minimal APIs and add endpoint filters plus `.RequireAuthorization(policy)`. Section 8's policies work unchanged with minimal APIs. Either way, migrate **one endpoint group at a time** (section 10). Both models run in the same app, so there is never a big-bang cut-over.

---

## 2. How the API is wired today

`Program.cs` registers services, builds the middleware pipeline and then maps every group:

```csharp
app.UseMiddleware<CorrelationIdMiddleware>();
app.UseCors();
app.UseExceptionHandler();               // GlobalExceptionHandler → 500 problem
app.UseSerilogRequestLogging();
app.UseAuthentication();
app.UseMiddleware<RateLimitMiddleware>(rateLimitOptions);   // after auth: limits per customer
app.UseAuthorization();

app.MapAuthEndpoints();
app.MapAccountEndpoints();
app.MapPaymentEndpoints();
// … 10 more groups
```

| File | Routes | Notes |
|---|---|---|
| `AuthEndpoints.cs` | 5 | Anonymous auth routes plus `GET /api/v1/customers/me`; manual DataAnnotations validation |
| `AccountEndpoints.cs` | 3 | |
| `BeneficiaryEndpoints.cs` | 5 | |
| `PaymentEndpoints.cs` | 3 | `Idempotency-Key` header; `PaymentException` → problem |
| `KycEndpoints.cs` | 6 | `KycException` → problem; one reviewer-only route |
| `TransactionsEndpoints.cs`, `LedgerEndpoints.cs`, `NotificationsEndpoints.cs` | 2, 1, 1 | Read-only |
| `FraudEndpoints.cs` | 2 | Review is admin/fraud_analyst; it settles held payments |
| `AmlEndpoints.cs` | 1 | admin/fraud_analyst |
| `ReconciliationEndpoints.cs` | 3 | admin/finance_user |
| `ReportsEndpoints.cs` | 2 | Admins see everything, customers see their own |
| `AdminEndpoints.cs` | 8 | admin, plus the two treasury routes (admin/finance_user) |

**None of the middleware changes.** Controllers are endpoints too: `MapControllers()` adds them to the same endpoint routing table. Correlation ids, CORS, the global exception handler, Serilog request logging, JWT authentication and the Redis-backed rate limiter run exactly as before.

---

## 3. Concept map: minimal API → controller

| Minimal API | Controller | Notes |
|---|---|---|
| `endpoints.MapGroup("/api/v1/payments")` | `[Route("api/v1/payments")]` on the class | No leading slash needed |
| `.WithTags("payments")` | `[Tags("payments")]` | Used by OpenAPI |
| `.RequireAuthorization()` on the group | `[Authorize]` on the class | `[AllowAnonymous]` opts an action out |
| `group.MapPost("", …)` | `[HttpPost]` | |
| `group.MapGet("/{paymentId:guid}", …)` | `[HttpGet("{paymentId:guid}")]` | Route constraints are identical |
| Lambda parameter `CreatePaymentRequest request` (inferred body) | Action parameter `CreatePaymentRequest request` | `[ApiController]` infers `[FromBody]` for complex types |
| `string? status` (inferred query) | `[FromQuery] string? status` | Inferred too, but explicit reads better |
| `context.Request.Headers.TryGetValue("Idempotency-Key", …)` | `[FromHeader(Name = "Idempotency-Key")] string? idempotencyKey` | |
| `ClaimsPrincipal principal` parameter | `User` property | |
| Injected services in the lambda | Primary-constructor injection | `PaymentsController(IPaymentService payments)` |
| `HttpContext context` parameter | `HttpContext` property | |
| `CancellationToken cancellationToken` | Same | Bound to `HttpContext.RequestAborted` |
| `Results.Ok(x)` | `Ok(x)` or return `x` from `ActionResult<T>` | |
| `Results.Json(x, statusCode: 201)` | `StatusCode(201, x)` | Not `CreatedAtAction` — see pitfall 9.6 |
| `Results.NoContent()` | `NoContent()` | |
| `Results.Unauthorized()` / `Results.Forbid()` | `Unauthorized()` / `Forbid()` | `Forbid()` still goes through the JWT `OnForbidden` problem writer |
| `Results.Problem(…)` with `code`/`traceId` extensions | `ProblemResult(…)` on the base class | Keeps the contract's problem shape |
| `try/catch (PaymentException)` in every lambda | `DomainExceptionFilter` | |
| `EndpointSupport.CurrentRolesAsync` + `HasAnyRole` + `Forbidden` | `[Authorize(Policy = Policies.X)]` | Database-backed handler; see section 8 |

---

## 4. Step 1 — Shared controller infrastructure

Create `src/FinCore.Api/Controllers/ControllerInfrastructure.cs`. It holds everything the minimal API endpoints currently repeat by hand.

```csharp
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace FinCore.Api.Controllers;

/// <summary>Base for every FinCore controller: the signed-in customer and contract-shaped problems.</summary>
// No [Produces("application/json")] here: it would override the problem+json content type that
// ProblemDetails results need. MVC already negotiates JSON for everything else.
[ApiController]
public abstract class FinCoreControllerBase : ControllerBase
{
    /// <summary>The authenticated customer's id. [Authorize] guarantees a principal; a token without a
    /// usable subject is answered with 401 by <see cref="DomainExceptionFilter"/>.</summary>
    protected Guid CustomerId => EndpointSupport.TryCustomerId(User, out var id)
        ? id
        : throw new UnauthorizedAccessException("Token has no customer subject.");

    protected ObjectResult ProblemResult(int status, string code, string title, string? detail = null) =>
        ProblemResponses.Create(HttpContext, status, code, title, detail);
}

public static class ProblemResponses
{
    /// <summary>The problem+json body the OpenAPI contract defines: type, title, status, code, detail, traceId.</summary>
    public static ObjectResult Create(HttpContext context, int status, string code, string title, string? detail = null,
        object? errors = null)
    {
        var problem = new ProblemDetails
        {
            Type = $"https://fincore.local/problems/{code}",
            Title = title,
            Status = status,
            Detail = detail ?? title
        };
        problem.Extensions["code"] = code;
        problem.Extensions["traceId"] = context.TraceIdentifier;
        if (errors is not null) problem.Extensions["errors"] = errors;
        var result = new ObjectResult(problem) { StatusCode = status };
        result.ContentTypes.Add("application/problem+json");
        return result;
    }

    /// <summary>
    /// [ApiController] answers invalid models with 400 by default; the contract (and the Python API)
    /// use 422 with field-level errors, so model-state failures are reshaped here.
    /// </summary>
    public static IActionResult InvalidModelState(ActionContext context)
    {
        var errors = context.ModelState
            .Where(entry => entry.Value?.Errors.Count > 0)
            .Select(entry => new { field = ToCamelCase(entry.Key), code = "invalid_value" })
            .ToArray();
        return Create(context.HttpContext, 422, "validation_error", "Request validation failed",
            "One or more fields are invalid.", errors);
    }

    private static string ToCamelCase(string key) =>
        string.IsNullOrEmpty(key) ? "request" : char.ToLowerInvariant(key[0]) + key[1..];
}

/// <summary>
/// Turns the application layer's typed exceptions into problem responses, so actions contain no
/// try/catch. Anything else falls through to GlobalExceptionHandler (500).
/// </summary>
public sealed class DomainExceptionFilter : IExceptionFilter
{
    public void OnException(ExceptionContext context)
    {
        var (status, code, message) = context.Exception switch
        {
            PaymentException e => (e.StatusCode, e.Code, e.Message),
            KycException e => (e.StatusCode, e.Code, e.Message),
            BeneficiaryException e => (e.StatusCode, e.Code, e.Message),
            AuthenticationException e => (e.StatusCode, e.Code, e.Message),
            FraudException e => (e.StatusCode, e.Code, e.Message),
            UnauthorizedAccessException => (401, "unauthorized", "Authentication required"),
            _ => (0, string.Empty, string.Empty)
        };
        if (status == 0) return;
        var title = status switch
        {
            401 => "Authentication failed",
            403 => "Access denied",
            404 => "Resource not found",
            409 => "Conflict",
            422 => "Request validation failed",
            _ => "Request could not be completed"
        };
        context.Result = ProblemResponses.Create(context.HttpContext, status, code, title, message);
        context.ExceptionHandled = true;
    }
}

/// <summary>Named authorization policies. Roles come from the database on every request, as in the
/// minimal API version, because an access token can outlive a role change.</summary>
public static class Policies
{
    public const string Admin = "admin";
    public const string FraudReview = "fraud-review";
    public const string Treasury = "treasury";

    public static void Configure(AuthorizationOptions options)
    {
        options.AddPolicy(Admin, policy => policy.AddRequirements(new DatabaseRoleRequirement("admin")));
        options.AddPolicy(FraudReview, policy => policy.AddRequirements(new DatabaseRoleRequirement("admin", "fraud_analyst")));
        options.AddPolicy(Treasury, policy => policy.AddRequirements(new DatabaseRoleRequirement("admin", "finance_user")));
    }
}

public sealed class DatabaseRoleRequirement(params string[] roles) : IAuthorizationRequirement
{
    public IReadOnlyList<string> Roles { get; } = roles;
}

/// <summary>Scoped (it uses the request's DbContext). Not calling Succeed means 403 via the JWT
/// handler's OnForbidden event, which already writes the contract's problem body.</summary>
public sealed class DatabaseRoleHandler(FinCoreDbContext db) : AuthorizationHandler<DatabaseRoleRequirement>
{
    protected override async Task HandleRequirementAsync(AuthorizationHandlerContext context,
        DatabaseRoleRequirement requirement)
    {
        if (!EndpointSupport.TryCustomerId(context.User, out var customerId)) return;
        var roles = await EndpointSupport.CurrentRolesAsync(db, customerId, CancellationToken.None);
        if (EndpointSupport.HasAnyRole(roles, [.. requirement.Roles])) context.Succeed(requirement);
    }
}
```

What each part replaces, and why it is shaped this way:

* **`FinCoreControllerBase.CustomerId`** replaces the `if (!TryCustomerId(principal, out var customerId)) return Results.Unauthorized();` line at the top of nearly every lambda. `[Authorize]` has already rejected anonymous callers. The only remaining case is a valid token without a usable `sub`, and throwing lets the filter answer it with the same 401 problem.
* **`ProblemResponses.Create`** produces exactly the body the minimal APIs build with `Results.Problem(... extensions: { code, traceId })`. Clients branch on `code`, so it has to stay identical.
* **`InvalidModelState`** is the most important compatibility piece. `[ApiController]` validates bodies automatically, but its default response is a **400** `ValidationProblemDetails`. The contract and the Python implementation both use **422** with `errors: [{ field, code }]`.
* **`DomainExceptionFilter`** removes all 14 `try/catch` blocks. It covers the five typed exceptions the application layer throws (`PaymentException`, `KycException`, `BeneficiaryException`, `AuthenticationException`, `FraudException`), each carrying `Code` and `StatusCode`. It handles only the application's typed exceptions; everything else still reaches `GlobalExceptionHandler`, so unexpected failures remain opaque 500s.
* **Policies and the handler** keep the current security semantics. Roles are read **from the database**, not from the token's role claims, so an admin who removes a role takes effect immediately rather than when the 15-minute access token expires. A claims-based `[Authorize(Roles = "admin")]` would quietly weaken that. Don't swap it in.

---

## 5. Step 2 — Program.cs

Three additions. First add `using FinCore.Api.Controllers;` and `using Microsoft.AspNetCore.Authorization;` at the top, then replace the plain `builder.Services.AddAuthorization();` with:

```csharp
builder.Services.AddControllers(options => options.Filters.Add<DomainExceptionFilter>())
    .ConfigureApiBehaviorOptions(options =>
        options.InvalidModelStateResponseFactory = ProblemResponses.InvalidModelState);
builder.Services.AddAuthorization(Policies.Configure);
builder.Services.AddScoped<IAuthorizationHandler, DatabaseRoleHandler>();
```

Map the controllers after the remaining minimal API groups:

```csharp
app.MapAccountEndpoints();
// … groups not migrated yet …
app.MapAdminEndpoints();
app.MapControllers();
```

And remove each `app.MapXEndpoints();` line in the same commit that adds its controller (section 10 explains why).

Things that deliberately **don't** change:

* **Middleware order.** `MapControllers()` only adds endpoints. `UseAuthentication` → `RateLimitMiddleware` → `UseAuthorization` still applies, so rate limits are still per customer.
* **JWT events.** `OnChallenge`/`OnForbidden` keep writing the 401/403 problem bodies. Controllers' `Unauthorized()`, `Forbid()` and failed policies all flow through them.
* **`AddOpenApi()` / `MapOpenApi()`.** They describe controllers and minimal APIs together, and `[ProducesResponseType]` feeds the document the way `.Produces<T>()` did.
* **Hosted services** (outbox dispatcher, Service Bus consumer) are unrelated to how HTTP endpoints are declared.

---

## 6. Step 3 — Convert a group: Payments

### Before (`PaymentEndpoints.cs`, abridged)

```csharp
var group = endpoints.MapGroup("/api/v1/payments").WithTags("payments").RequireAuthorization();
group.MapPost("", async (CreatePaymentRequest request, ClaimsPrincipal principal,
    IPaymentService service, HttpContext context, CancellationToken cancellationToken) =>
{
    if (!TryCustomerId(principal, out var customerId)) return Results.Unauthorized();
    if (!context.Request.Headers.TryGetValue("Idempotency-Key", out var keyValues)
        || keyValues.Count != 1 || string.IsNullOrWhiteSpace(keyValues[0]))
        return Problem("invalid_idempotency_key", "Idempotency-Key is required.", 422, context);
    try
    {
        var command = new CreatePaymentCommand(request.SourceAccountId, request.BeneficiaryId,
            request.AmountMinor, request.Currency, request.Reference);
        var payment = await service.CreateAsync(customerId, command, keyValues[0]!, cancellationToken);
        return Results.Json(payment, statusCode: 201);
    }
    catch (PaymentException exception)
    {
        return Problem(exception.Code, exception.Message, exception.StatusCode, context);
    }
});
// GET "" and GET "/{paymentId:guid}" follow the same pattern, each with its own try/catch.
```

### After (`Controllers/PaymentsController.cs`)

```csharp
using FinCore.Application;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreatePaymentRequest(Guid SourceAccountId, Guid BeneficiaryId, long AmountMinor,
    string Currency, string? Reference);

[Route("api/v1/payments")]
[Authorize]
[Tags("payments")]
public sealed class PaymentsController(IPaymentService payments) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<PaymentView>(StatusCodes.Status201Created)]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status404NotFound, "application/problem+json")]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status409Conflict, "application/problem+json")]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status422UnprocessableEntity, "application/problem+json")]
    public async Task<IActionResult> Create(CreatePaymentRequest request,
        [FromHeader(Name = "Idempotency-Key")] string? idempotencyKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed", "Idempotency-Key is required.");
        var command = new CreatePaymentCommand(request.SourceAccountId, request.BeneficiaryId, request.AmountMinor,
            request.Currency, request.Reference);
        // PaymentException (limits, funds, idempotency conflicts...) becomes a problem in DomainExceptionFilter.
        var payment = await payments.CreateAsync(CustomerId, command, idempotencyKey, cancellationToken);
        // The contract defines 201 without a Location header, so StatusCode rather than CreatedAtAction.
        return StatusCode(StatusCodes.Status201Created, payment);
    }

    [HttpGet]
    [ProducesResponseType<PaymentPage>(StatusCodes.Status200OK)]
    public async Task<ActionResult<PaymentPage>> List([FromQuery] string? status, [FromQuery] int? limit,
        [FromQuery] string? cursor, CancellationToken cancellationToken) =>
        await payments.ListAsync(CustomerId, status, Math.Clamp(limit ?? 20, 1, 100), cursor, cancellationToken);

    [HttpGet("{paymentId:guid}")]
    [ProducesResponseType<PaymentView>(StatusCodes.Status200OK)]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status404NotFound, "application/problem+json")]
    public async Task<IActionResult> Get(Guid paymentId, CancellationToken cancellationToken)
    {
        var payment = await payments.GetAsync(CustomerId, paymentId, cancellationToken);
        return payment is null
            ? ProblemResult(404, "not_found", "Resource not found", "Payment not found.")
            : Ok(payment);
    }
}
```

### What changed, line by line

1. **The group became the class.** `MapGroup` + `WithTags` + `RequireAuthorization` are now `[Route]`, `[Tags]` and `[Authorize]` on the class, so they apply to every action.
2. **Dependencies moved to the constructor.** `IPaymentService` is injected once (scoped per request, as before) instead of per lambda.
3. **The header is bound declaratively.** `[FromHeader(Name = "Idempotency-Key")]` replaces the manual `TryGetValue`. The "required" check stays in the action because the contract demands the `invalid_idempotency_key` code. Making the parameter `[Required]` would produce a generic `validation_error` instead.
4. **The `try/catch` is gone.** `PaymentException` propagates to `DomainExceptionFilter`, which returns the same problem body (`code`, `traceId`, status from the exception).
5. **`Results.Json(payment, statusCode: 201)` became `StatusCode(201, payment)`.** Status and body are identical. The service's idempotent replay returns the original `PaymentView` and the action answers 201 again, as before.
6. **`ActionResult<PaymentPage>`** lets `List` return the page directly while still allowing problem results; OpenAPI picks up the type.
7. **`CreatePaymentRequest` moved** next to the controller. It stays a plain positional record because it has no validation attributes; `PaymentService` keeps validating amount and currency itself.

Delete `PaymentEndpoints.cs` and the `app.MapPaymentEndpoints();` line in the same change.

---

## 7. Step 4 — Anonymous routes and validation: Auth

```csharp
using System.ComponentModel.DataAnnotations;
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

// Attributes target the constructor parameters (no "property:" prefix); see pitfall 9.1.
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
public sealed class AuthController(AuthenticationService authentication, IRefreshTokenService refreshTokens)
    : FinCoreControllerBase
{
    // [ApiController] validates the DataAnnotations above before the action runs; failures become
    // 422 problems through ProblemResponses.InvalidModelState. No manual Validate() call needed.
    [HttpPost("register")]
    [ProducesResponseType<CustomerResponse>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Register(RegisterRequest request, CancellationToken cancellationToken)
    {
        var customer = await authentication.RegisterAsync(request.Email, request.Password, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, new CustomerResponse(customer.Id, customer.Email, customer.Status));
    }

    [HttpPost("login")]
    public async Task<ActionResult<TokenResponse>> Login(LoginRequest request, CancellationToken cancellationToken)
    {
        var result = await authentication.LoginAsync(request.Email, request.Password, cancellationToken);
        var refreshToken = await refreshTokens.IssueAsync(result.Customer.Id, cancellationToken);
        return new TokenResponse(result.Token.Value, "Bearer", result.Token.ExpiresInSeconds, refreshToken);
    }

    [HttpPost("refresh")]
    public async Task<ActionResult<TokenResponse>> Refresh(RefreshRequest request, CancellationToken cancellationToken)
    {
        var result = await refreshTokens.RotateAsync(request.RefreshToken, cancellationToken);
        return new TokenResponse(result.AccessToken.Value, "Bearer", result.AccessToken.ExpiresInSeconds, result.RefreshToken);
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
    public async Task<ActionResult<CustomerResponse>> Me(CancellationToken cancellationToken)
    {
        var customer = await customers.FindByIdAsync(CustomerId, cancellationToken);
        if (customer is null) return Unauthorized();
        if (customer.Status is "suspended" or "blocked" or "closed") return Forbid();
        return new CustomerResponse(customer.Id, customer.Email, customer.Status);
    }
}
```

Notes:

* **`[AllowAnonymous]` on the class** mirrors the auth group, which never called `RequireAuthorization()`.
* **`Validate(request, context)` disappears.** With `[ApiController]`, model validation runs before the action, and the response factory from section 4 returns what `Validate` returned: 422 `validation_error` with `errors: [{ field, code: "invalid_value" }]`. The verified response for an invalid registration was:

  ```json
  { "type": "https://fincore.local/problems/validation_error", "title": "Request validation failed",
    "status": 422, "detail": "One or more fields are invalid.", "code": "validation_error",
    "traceId": "…", "errors": [ { "field": "email", "code": "invalid_value" },
                               { "field": "password", "code": "invalid_value" } ] }
  ```

  One small difference: the old helper's `type` was `…/validation-error` (hyphen) while this is `…/validation_error`, matching every other problem type. Clients key on `code`, which is unchanged.
* **`AuthenticationException`** (duplicate email, bad credentials, inactive customer) is handled by the filter, replacing the auth file's private `Problem(exception, context)` helper.
* **`GET /api/v1/customers/me`** lived inside `AuthEndpoints.cs` but is not an auth route, so it gets its own small controller with `[Authorize]`.

---

## 8. Step 5 — Role-protected routes: Treasury

The treasury routes show the biggest simplification. Today each route starts like this:

```csharp
var (actorId, roles) = await CheckRolesAsync(principal, db, cancellationToken);
if (actorId is null) return Results.Unauthorized();
if (!EndpointSupport.HasAnyRole(roles, TreasuryRoles)) return EndpointSupport.Forbidden(context);
```

As a controller, the policy carries that logic:

```csharp
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
        [FromHeader(Name = "Idempotency-Key")] string? idempotencyKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed", "Idempotency-Key is required.");
        var deposit = await treasury.DepositAsync(CustomerId, accountId, request.AmountMinor, request.Reference,
            idempotencyKey, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, deposit);
    }
}
```

How a request is authorized:

1. `UseAuthentication` validates the JWT and sets `User`.
2. `UseAuthorization` sees `[Authorize(Policy = "treasury")]` and runs `DatabaseRoleHandler` for the `DatabaseRoleRequirement("admin", "finance_user")`.
3. The handler loads the customer's **current** roles from PostgreSQL. If neither role is present it does not call `Succeed`. The policy then fails, the JWT handler's `OnForbidden` writes the 403 problem (`code: "forbidden"`), and the action never runs. Anonymous callers get the 401 problem from `OnChallenge`.

**Moving routes out of a shared group.** Both treasury routes live inside `MapAdminEndpoints`, alongside admin-only routes, under the same `/api/v1/admin` prefix. When you add `TreasuryController`, **delete those two `group.MapGet`/`group.MapPost` registrations from `AdminEndpoints.cs` in the same change**. If one route is registered both as a minimal API and as a controller action, routing throws `AmbiguousMatchException` at request time, not at startup. The rest of `AdminEndpoints.cs` keeps working until its own turn. A controller can share a URL prefix with a minimal API group without conflict, as long as no individual route is duplicated.

The same pattern covers every role check in the codebase:

| Routes | Policy |
|---|---|
| `admin/customers` PATCH, `admin/fraud-rules`, `admin/audit-logs`, `admin/accounts/{id}/status` | `Policies.Admin` |
| `fraud/cases/{id}/review`, `aml/alerts` | `Policies.FraudReview` |
| `reconciliation/*`, `admin/customers` GET, treasury | `Policies.Treasury` (admin + finance_user) |

Routes whose *results* depend on the role, rather than access to them, keep an inline check. `GET /fraud/cases` and the reports return everything to staff but only the caller's own data to customers. Use `IAuthorizationService.AuthorizeAsync(User, Policies.FraudReview)` inside the action, so the rule stays defined in one place.

---

## 9. Pitfalls found while verifying this guide

**9.1 Validation attributes on positional records must target the parameter. (Found at runtime.)**
The existing request records use `[property: Required]`, which works with the manual `Validator.TryValidateObject` call. MVC's validator **throws on every request** for such records:

```
InvalidOperationException: Record type 'FinCore.Api.Controllers.RegisterRequest' has validation metadata
defined on property 'Password' that will be ignored. 'Password' is a parameter in the record primary
constructor and validation metadata must be associated with the constructor parameter.
```

The `GlobalExceptionHandler` turned this into a 500 for every registration. The fix is to write `[Required, EmailAddress] string Email` without the `property:` target.

**9.2 `[Produces("application/json")]` overrides problem responses. (Found by the parity checks.)**
With `[Produces]` on the base controller, 401/404/422 responses went out as `application/json` instead of `application/problem+json`. MVC already serialises `ProblemDetails` as problem+json and everything else as JSON, so leave `[Produces]` off.

**9.3 400 vs 422.** `[ApiController]`'s automatic model validation returns **400** unless you replace `InvalidModelStateResponseFactory` (section 4). Without it, every validation failure silently changes status code and body shape against the contract and the Python implementation.

**9.4 Malformed JSON also becomes 422.** With the factory in place, unparseable JSON (`{"amountMinor": "abc"}`) reaches the same path and returns 422 `validation_error`; the field key is the JSON path (`$.amountMinor`). The minimal APIs answer some of these with a framework 400. 422 is the consistent choice, but call it out to API consumers.

**9.5 Non-nullable reference types are implicitly required.** With `<Nullable>enable</Nullable>`, MVC treats a non-nullable `string Currency` as `[Required]`. A missing `currency` is now rejected by model validation (422 `validation_error`) before `PaymentService` runs. The status code is the same, but the message is generic. Mark optional properties `string?`, as `Reference` already is.

**9.6 Don't reach for `CreatedAtAction`.** It adds a `Location` header and needs a resolvable "get" action. The contract defines `201` with the resource in the body and no `Location`. `StatusCode(201, value)` reproduces the current response exactly.

**9.7 JSON options are configured separately.** Minimal APIs use `ConfigureHttpJsonOptions(...)`; controllers use `AddControllers().AddJsonOptions(...)`. FinCore currently relies on the defaults (camelCase, web settings) in both, so responses match. If you ever add a converter, such as `JsonStringEnumConverter`, add it in **both** places during the migration, or the same type will serialise differently depending on which model served the route.

**9.8 `[FromHeader]` joins repeated headers.** The minimal API rejected a request carrying two `Idempotency-Key` headers (`keyValues.Count != 1`). `[FromHeader] string` binds them as one comma-joined string, which then becomes a single (odd) key. If you want to keep the strict rejection, bind `[FromHeader(Name = "Idempotency-Key")] string[]? keys` and require `keys is [var key]`.

**9.9 The authorization handler must be scoped.** `DatabaseRoleHandler` takes `FinCoreDbContext`, so register it with `AddScoped<IAuthorizationHandler, DatabaseRoleHandler>()`. As a singleton it would capture a disposed context.

**9.10 One route, one owner.** Register a route as either a minimal API *or* a controller action, never both. A duplicate is not detected at startup; the first request to it fails with `AmbiguousMatchException` (500).

---

## 10. Migration plan for the remaining groups

Migrate in small, independently deployable steps. Each step moves one file's routes, deletes that file and its `Map…Endpoints()` call, and passes the parity suite (section 11).

| Order | Group | Routes | Why this order |
|---|---|---|---|
| 1 | Infrastructure + `Program.cs` (sections 4–5) | 0 | No behaviour change; controllers and minimal APIs now coexist |
| 2 | Payments | 3 | Done above; exercises headers, idempotency and the exception filter |
| 3 | Auth + customers/me | 5 | Done above; exercises anonymous routes and validation |
| 4 | Accounts, Transactions, Ledger, Notifications | 7 | Read-mostly, customer-scoped, no role checks |
| 5 | Beneficiaries | 5 | `BeneficiaryException` is already in the filter; drop the file's private `Problem` helper |
| 6 | KYC | 6 | Base64 uploads; the reviewer route uses `Policies.FraudReview` or an admin policy |
| 7 | Treasury (out of Admin) | 2 | Done above; first policy-protected controller |
| 8 | Fraud, AML | 3 | Review settles held payments inside a transaction; keep the transaction in the action or move it into a service. AML catches `JsonException` while parsing stored alert details; keep that local `try` (it is not an HTTP error) |
| 9 | Reconciliation, Reports | 5 | Role-dependent results (inline `IAuthorizationService`) |
| 10 | Admin | 6 | Last: most role checks, smallest blast radius once policies are proven |
| 11 | Clean-up | — | Delete `EndpointSupport.Problem`/`Forbidden` once nothing uses them; keep `TryCustomerId`/`CurrentRolesAsync` (the base class and handler use them) |

**Moving logic into services while you're there.** Several lambdas contain business logic, not just HTTP plumbing. The fraud review endpoint opens a transaction, locks the case and settles the held payment, and some admin routes query `FinCoreDbContext` directly. The cleanest conversion moves that logic into an application or infrastructure service (as `TreasuryService` and `PaymentHoldService` already are), leaving the controller action as binding, a service call and a result. That is also what makes controllers worth unit-testing.

---

## 11. Testing for contract parity

The migration is correct when nothing observable changes. Three layers check that:

**1. The existing suites.** `dotnet test` must stay green. The services the controllers call are already covered by the integration tests in `tests/FinCore.Tests`, and they don't change.

**2. An end-to-end parity run against both builds.** Run the same HTTP scenario against the current API and against the build with controllers, and compare the results. This guide was verified that way. The scenario covered:
* register/login, KYC upload to Azurite and approval
* opening accounts and a treasury deposit, with a replay that doesn't double-credit and a customer who gets 403
* a payment, its idempotent replay and a key reused with a different body (409)
* a payment held by fraud review and released by a reviewer
* notifications arriving through Service Bus
* refresh, logout, and refresh after logout (401)
* admin access allowed and denied

**3. Controller-specific checks.** These are the behaviours that change *mechanism*, so test them directly:

| Check | Expected |
|---|---|
| Invalid register body | 422, `code: validation_error`, `errors` with camelCase fields |
| Any problem response | `Content-Type: application/problem+json` |
| Payment without `Idempotency-Key` | 422, `code: invalid_idempotency_key` |
| Payment with unknown source account | 404, `code: not_found` (via `DomainExceptionFilter`) |
| No token | 401, `code: unauthorized` |
| Customer calling a treasury route | 403, `code: forbidden` (via policy) |
| `GET /customers/me` | 200 with the caller's email |
| A route still on minimal APIs | Unaffected (coexistence) |

For in-process tests, `WebApplicationFactory<Program>` works unchanged; `Program` already ends with `public partial class Program { }`. Add `Microsoft.AspNetCore.Mvc.Testing` to the test project, then:

```csharp
public sealed class PaymentsContractTests(WebApplicationFactory<Program> factory)
    : IClassFixture<WebApplicationFactory<Program>>
{
    [Fact]
    public async Task Missing_idempotency_key_is_a_422_problem()
    {
        var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", await TestTokens.CustomerAsync(factory));

        var response = await client.PostAsJsonAsync("/api/v1/payments",
            new { sourceAccountId = Guid.NewGuid(), beneficiaryId = Guid.NewGuid(), amountMinor = 5, currency = "USD" });

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        response.Content.Headers.ContentType!.MediaType.Should().Be("application/problem+json");
        (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("code").GetString()
            .Should().Be("invalid_idempotency_key");
    }
}
```

(`TestTokens.CustomerAsync` stands for a helper that registers and logs in a customer against the factory's test database; build it on the existing `RequiresPostgresFact` setup.)

---

## 12. Per-group checklist and rollback

For every group you migrate:

- [ ] Controller class with `[Route]` equal to the old group prefix, plus `[Tags]`
- [ ] `[Authorize]`, `[AllowAnonymous]` or `[Authorize(Policy = …)]` matching the old group, with role semantics unchanged
- [ ] Every action's verb and route template matches the old `Map*` call, including constraints like `:guid`
- [ ] Request records: validation attributes without `property:` (9.1); optional fields nullable (9.5)
- [ ] Success status codes unchanged (`StatusCode(201, …)`, `NoContent()`, `Ok(…)`), with no `CreatedAtAction` (9.6)
- [ ] Inline problem results use `ProblemResult` with the **same `code`** as before
- [ ] Any new exception type the group's services throw is added to `DomainExceptionFilter`
- [ ] Old `Map…Endpoints()` call and file deleted **in the same change** (9.10)
- [ ] `dotnet test` green; parity suite green against the new build
- [ ] OpenAPI document reviewed: same paths and verbs, response types present

**Rollback** is per group. Because each step deletes one minimal-API file and adds one controller, reverting that commit restores the previous wiring exactly. No data, schema or contract changes are involved, so the Python implementation and the frontend are unaffected at every step.
