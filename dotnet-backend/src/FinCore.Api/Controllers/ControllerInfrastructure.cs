using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace FinCore.Api.Controllers;

/// <summary>Base for every FinCore controller: the signed-in customer and contract-shaped problems.</summary>
// No [Produces("application/json")] here: it would override the problem+json content type that
// ProblemDetails results need (pitfall 9.2 in docs/DOTNET_CONTROLLERS_MIGRATION.md). MVC already
// negotiates JSON for everything else.
[ApiController]
public abstract class FinCoreControllerBase : ControllerBase
{
    /// <summary>The authenticated customer's id. [Authorize] guarantees a principal; a token without a
    /// usable subject is answered with 401 by <see cref="DomainExceptionFilter"/>.</summary>
    protected Guid CustomerId => EndpointSupport.TryCustomerId(User, out var id)
        ? id
        : throw new UnauthorizedAccessException("Token has no customer subject.");

    protected ObjectResult ProblemResult(int status, string code, string title, string? detail = null,
        object? errors = null) =>
        ProblemResponses.Create(HttpContext, status, code, title, detail, errors);
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
    /// use 422 with field-level errors, so model-state failures are reshaped here (pitfall 9.3).
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

/// <summary>
/// Named authorization policies. Roles come from the database on every request, as in the
/// minimal API version, because an access token can outlive a role change (the JWT carries no
/// role claims the policy could trust).
/// </summary>
public static class Policies
{
    public const string Admin = "admin";
    public const string FraudReview = "fraud-review";          // admin, fraud_analyst
    public const string Treasury = "treasury";                 // admin, finance_user
    public const string FraudStaff = "fraud-staff";            // admin, fraud_analyst, finance_user
    public const string Finance = "finance";                   // admin, finance_user, operations_user
    public const string Compliance = "compliance";             // admin, fraud_analyst, compliance_officer
    public const string Operations = "operations";             // admin, operations_user

    public static void Configure(AuthorizationOptions options)
    {
        options.AddPolicy(Admin, policy => policy.AddRequirements(new DatabaseRoleRequirement("admin")));
        options.AddPolicy(FraudReview, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "fraud_analyst")));
        options.AddPolicy(Treasury, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "finance_user")));
        options.AddPolicy(FraudStaff, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "fraud_analyst", "finance_user")));
        options.AddPolicy(Finance, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "finance_user", "operations_user")));
        options.AddPolicy(Compliance, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "fraud_analyst", "compliance_officer")));
        options.AddPolicy(Operations, policy => policy.AddRequirements(
            new DatabaseRoleRequirement("admin", "operations_user")));
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
