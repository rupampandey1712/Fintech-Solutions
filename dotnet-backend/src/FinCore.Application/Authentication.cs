using System.ComponentModel.DataAnnotations;

namespace FinCore.Application;

public sealed record CustomerIdentity(Guid Id, string Email, string PasswordHash, string Status,
    IReadOnlyList<string> Roles);
public sealed record AccessToken(string Value, int ExpiresInSeconds);
public sealed record RefreshResult(Guid CustomerId, AccessToken AccessToken, string RefreshToken);

public interface ICustomerIdentityRepository
{
    Task<CustomerIdentity?> FindByEmailAsync(string normalizedEmail, CancellationToken cancellationToken);
    Task<CustomerIdentity?> FindByIdAsync(Guid id, CancellationToken cancellationToken);
    Task<CustomerIdentity> CreateAsync(string normalizedEmail, string passwordHash, string? rolesJson = null,
        CancellationToken cancellationToken = default);
}

public interface IPasswordHasher
{
    string Hash(string normalizedEmail, string password);
    bool Verify(string normalizedEmail, string passwordHash, string password);
}

/// <summary>Screens passwords against a list of common or breached passwords.</summary>
public interface ICommonPasswordCheck
{
    bool IsCommon(string password);
}

public interface IAccessTokenIssuer
{
    AccessToken Issue(Guid customerId, IEnumerable<string>? roles = null);
}

public interface IRefreshTokenService
{
    Task<string> IssueAsync(Guid customerId, CancellationToken cancellationToken);
    Task<RefreshResult> RotateAsync(string refreshToken, CancellationToken cancellationToken);
    Task RevokeAsync(string refreshToken, CancellationToken cancellationToken);
}

public sealed class AuthenticationException(string code, string message, int statusCode, Exception? innerException = null)
    : Exception(message, innerException)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
}

public sealed class AuthenticationService(
    ICustomerIdentityRepository customers,
    IPasswordHasher passwords,
    IAccessTokenIssuer tokens,
    ICommonPasswordCheck? commonPasswords = null)
{
    public async Task<CustomerIdentity> RegisterAsync(string email, string password, CancellationToken cancellationToken)
    {
        var normalized = NormalizeEmail(email);
        ValidatePassword(password);
        if (commonPasswords?.IsCommon(password) == true)
            throw new AuthenticationException("password_too_common",
                "This password is too common. Choose one that is harder to guess.", 422);
        if (await customers.FindByEmailAsync(normalized, cancellationToken) is not null)
            throw new AuthenticationException("email_already_registered", "An account with this email already exists.", 409);
        var hash = passwords.Hash(normalized, password);
        return await customers.CreateAsync(normalized, hash, cancellationToken: cancellationToken);
    }

    public async Task<(CustomerIdentity Customer, AccessToken Token)> LoginAsync(
        string email, string password, CancellationToken cancellationToken)
    {
        var normalized = NormalizeEmail(email);
        var customer = await customers.FindByEmailAsync(normalized, cancellationToken);
        // An unknown email still pays for a full PBKDF2 verification, so response time does not
        // reveal which emails are registered.
        var valid = passwords.Verify(normalized, customer?.PasswordHash ?? DummyPasswordHash(), password);
        if (customer is null || !valid)
            throw new AuthenticationException("invalid_credentials", "Invalid email or password.", 401);
        if (customer.Status is "suspended" or "blocked" or "closed")
            throw new AuthenticationException("customer_not_active", "Customer access is not active.", 403);
        return (customer, tokens.Issue(customer.Id, customer.Roles));
    }

    // A real hash of a random password, made once; concurrent first uses may each make one,
    // which is harmless.
    private static string? dummyPasswordHash;

    private string DummyPasswordHash() =>
        dummyPasswordHash ??= passwords.Hash(string.Empty, Convert.ToBase64String(Guid.NewGuid().ToByteArray()));

    public static string NormalizeEmail(string email)
    {
        var normalized = email.Trim().ToLowerInvariant();
        if (!new EmailAddressAttribute().IsValid(normalized))
            throw new AuthenticationException("invalid_email", "A valid email address is required.", 422);
        return normalized;
    }

    private static void ValidatePassword(string password)
    {
        if (password.Length is < 12 or > 128)
            throw new AuthenticationException("invalid_password", "Password must be between 12 and 128 characters.", 422);
    }
}
