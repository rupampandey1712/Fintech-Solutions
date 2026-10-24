using System.Security.Claims;
using FinCore.Infrastructure;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api;

public static class EndpointSupport
{
    public static bool TryCustomerId(ClaimsPrincipal principal, out Guid customerId)
    {
        var value = principal.FindFirstValue(ClaimTypes.NameIdentifier) ?? principal.FindFirstValue("sub");
        return Guid.TryParse(value, out customerId);
    }

    // DB-based role resolution keeps admin/fraud/finance decisions correct after
    // an admin changes a customer's roles: the JWT may outlive the change.
    public static async Task<IReadOnlyList<string>> CurrentRolesAsync(FinCoreDbContext db, Guid customerId,
        CancellationToken cancellationToken)
    {
        var row = await db.Customers.AsNoTracking()
            .SingleOrDefaultAsync(customer => customer.Id == customerId, cancellationToken);
        return CustomerRoles.Parse(row?.Roles);
    }

    public static bool HasAnyRole(IReadOnlyList<string> roles, params string[] allowed) =>
        roles.Any(role => allowed.Contains(role, StringComparer.Ordinal));
}
