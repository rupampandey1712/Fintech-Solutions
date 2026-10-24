using FinCore.Application;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using System.Security.Cryptography;
using System.Text;

namespace FinCore.Infrastructure;

public sealed class RefreshTokenService(FinCoreDbContext db, IAccessTokenIssuer tokens, IConfiguration configuration)
    : IRefreshTokenService
{
    private TimeSpan Lifetime =>
        TimeSpan.FromDays(configuration.GetValue<int?>("REFRESH_TOKEN_LIFETIME_DAYS") ?? 30);

    public async Task<string> IssueAsync(Guid customerId, CancellationToken cancellationToken)
    {
        var token = GenerateToken();
        db.RefreshTokens.Add(new RefreshTokenRecord
        {
            CustomerId = customerId,
            TokenHash = Hash(token),
            ExpiresAt = DateTimeOffset.UtcNow + Lifetime
        });
        await db.SaveChangesAsync(cancellationToken);
        return token;
    }

    public async Task<RefreshResult> RotateAsync(string refreshToken, CancellationToken cancellationToken)
    {
        var row = await FindValidAsync(refreshToken, cancellationToken);
        var customer = await db.Customers.AsNoTracking()
            .SingleOrDefaultAsync(item => item.Id == row.CustomerId, cancellationToken)
            ?? throw InvalidToken();
        if (customer.Status is "suspended" or "blocked" or "closed")
            throw new AuthenticationException("customer_not_active", "Customer access is not active.", 403);

        var now = DateTimeOffset.UtcNow;
        row.RevokedAt = now;
        var replacement = GenerateToken();
        db.RefreshTokens.Add(new RefreshTokenRecord
        {
            CustomerId = customer.Id,
            TokenHash = Hash(replacement),
            ExpiresAt = now + Lifetime
        });
        await db.SaveChangesAsync(cancellationToken);
        return new RefreshResult(customer.Id, tokens.Issue(customer.Id, CustomerRoles.Parse(customer.Roles)), replacement);
    }

    public async Task RevokeAsync(string refreshToken, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(refreshToken)) return;
        var row = await db.RefreshTokens.SingleOrDefaultAsync(
            item => item.TokenHash == Hash(refreshToken), cancellationToken);
        if (row is null || row.RevokedAt is not null) return;
        row.RevokedAt = DateTimeOffset.UtcNow;
        await db.SaveChangesAsync(cancellationToken);
    }

    private async Task<RefreshTokenRecord> FindValidAsync(string refreshToken, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(refreshToken)) throw InvalidToken();
        var now = DateTimeOffset.UtcNow;
        var row = await db.RefreshTokens.SingleOrDefaultAsync(
            item => item.TokenHash == Hash(refreshToken), cancellationToken);
        if (row is null || row.RevokedAt is not null || row.ExpiresAt <= now) throw InvalidToken();
        return row;
    }

    private static AuthenticationException InvalidToken() =>
        new("unauthorized", "Refresh token is invalid or expired.", 401);

    private static string GenerateToken() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(48))
        .TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static string Hash(string token) =>
        Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(token)));
}
