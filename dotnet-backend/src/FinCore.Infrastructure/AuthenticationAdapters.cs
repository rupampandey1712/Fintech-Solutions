using FinCore.Application;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.IdentityModel.Tokens;
using Npgsql;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Cryptography;
using System.Security.Claims;
using System.Text;
using System.Text.Json;

namespace FinCore.Infrastructure;

public static class CustomerRoles
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static IReadOnlyList<string> Parse(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return ["customer"];
        try
        {
            var roles = JsonSerializer.Deserialize<string[]>(json, JsonOptions);
            return roles is { Length: > 0 } ? roles : ["customer"];
        }
        catch (JsonException)
        {
            return ["customer"];
        }
    }

    public static string Serialize(IEnumerable<string> roles) => JsonSerializer.Serialize(roles, JsonOptions);
}

public sealed class CustomerIdentityRepository(FinCoreDbContext db) : ICustomerIdentityRepository
{
    public async Task<CustomerIdentity?> FindByEmailAsync(string normalizedEmail, CancellationToken cancellationToken)
    {
        var row = await db.Customers.AsNoTracking()
            .SingleOrDefaultAsync(customer => customer.EmailNormalized == normalizedEmail, cancellationToken);
        return row is null ? null : Map(row);
    }

    public async Task<CustomerIdentity?> FindByIdAsync(Guid id, CancellationToken cancellationToken)
    {
        var row = await db.Customers.AsNoTracking().SingleOrDefaultAsync(customer => customer.Id == id, cancellationToken);
        return row is null ? null : Map(row);
    }

    public async Task<CustomerIdentity> CreateAsync(string normalizedEmail, string passwordHash, string? rolesJson = null,
        CancellationToken cancellationToken = default)
    {
        var row = new CustomerRecord { EmailNormalized = normalizedEmail, PasswordHash = passwordHash };
        if (rolesJson is not null) row.Roles = rolesJson;
        db.Customers.Add(row);
        // Spec section 17: CustomerCreated is announced on the event bus like every other
        // lifecycle event, so downstream services can react without coupling to registration.
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = row.Id,
            EventType = "customer.created.v1",
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(new
            {
                customerId = row.Id, email = normalizedEmail, roles = CustomerRoles.Parse(row.Roles)
            }),
            CreatedAt = DateTimeOffset.UtcNow
        });
        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception) when (exception.InnerException is PostgresException
            { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            throw new AuthenticationException("email_already_registered", "An account with this email already exists.", 409, exception);
        }
        return Map(row);
    }

    private static CustomerIdentity Map(CustomerRecord row) =>
        new(row.Id, row.EmailNormalized, row.PasswordHash, row.Status, CustomerRoles.Parse(row.Roles));
}

public sealed class CustomerPasswordHasher : IPasswordHasher
{
    private const int Iterations = 600_000;
    private const int SaltBytes = 16;
    private const int KeyBytes = 32;

    public string Hash(string normalizedEmail, string password)
    {
        var salt = RandomNumberGenerator.GetBytes(SaltBytes);
        var derived = Rfc2898DeriveBytes.Pbkdf2(password, salt, Iterations, HashAlgorithmName.SHA256, KeyBytes);
        return $"pbkdf2_sha256${Iterations}${Base64UrlEncode(salt)}${Base64UrlEncode(derived)}";
    }

    public bool Verify(string normalizedEmail, string passwordHash, string password)
    {
        var components = passwordHash.Split('$');
        if (components.Length != 4 || components[0] != "pbkdf2_sha256"
            || !int.TryParse(components[1], out var iterations) || iterations is < 100_000 or > 2_000_000)
            return false;
        try
        {
            var salt = Base64UrlDecode(components[2]);
            var expected = Base64UrlDecode(components[3]);
            if (expected.Length != KeyBytes) return false;
            var actual = Rfc2898DeriveBytes.Pbkdf2(password, salt, iterations, HashAlgorithmName.SHA256, expected.Length);
            return CryptographicOperations.FixedTimeEquals(actual, expected);
        }
        catch (FormatException)
        {
            return false;
        }
    }

    private static string Base64UrlEncode(byte[] value) => Convert.ToBase64String(value)
        .TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static byte[] Base64UrlDecode(string value)
    {
        var base64 = value.Replace('-', '+').Replace('_', '/');
        return Convert.FromBase64String(base64 + new string('=', (4 - base64.Length % 4) % 4));
    }
}

public sealed class JwtAccessTokenIssuer(IConfiguration configuration) : IAccessTokenIssuer
{
    private const int LifetimeSeconds = 900;

    public AccessToken Issue(Guid customerId, IEnumerable<string>? roles = null)
    {
        var keyText = configuration["JWT_SIGNING_KEY"] ?? "development-only-change-this-key";
        var keyBytes = Encoding.UTF8.GetBytes(keyText);
        if (keyBytes.Length < 32)
            throw new InvalidOperationException("JWT_SIGNING_KEY must contain at least 32 bytes.");

        var issuer = configuration["JWT_ISSUER"] ?? "fincore";
        var audience = configuration["JWT_AUDIENCE"] ?? "fincore-client";
        var now = DateTime.UtcNow;
        var credentials = new SigningCredentials(new SymmetricSecurityKey(keyBytes), SecurityAlgorithms.HmacSha256);
        var claims = new List<Claim>
        {
            new(JwtRegisteredClaimNames.Sub, customerId.ToString()),
            new(JwtRegisteredClaimNames.Jti, Guid.NewGuid().ToString()),
            new(JwtRegisteredClaimNames.Iat, EpochTime.GetIntDate(now).ToString(), ClaimValueTypes.Integer64)
        };
        if (roles is not null)
            claims.AddRange(roles.Select(role => new Claim(ClaimTypes.Role, role)));
        var token = new JwtSecurityToken(
            issuer,
            audience,
            claims,
            now,
            now.AddSeconds(LifetimeSeconds),
            credentials);
        return new AccessToken(new JwtSecurityTokenHandler().WriteToken(token), LifetimeSeconds);
    }
}
