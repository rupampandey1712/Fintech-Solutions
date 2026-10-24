namespace FinCore.Application;

public sealed record CreatePaymentCommand(Guid SourceAccountId, Guid BeneficiaryId, long AmountMinor,
    string Currency, string? Reference);

public sealed record RiskView(int Score, string Level, string Decision, IReadOnlyList<string> Reasons);

public sealed record PaymentView(Guid Id, string Status, long AmountMinor, string Currency,
    DateTimeOffset CreatedAt, string? Reference, string? ProviderReference, string? ProviderStatus,
    RiskView Risk);

public sealed record PaymentPage(IReadOnlyList<PaymentView> Items, string? NextCursor);

public sealed class PaymentException(string code, string message, int statusCode) : Exception(message)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
}

public interface IPaymentService
{
    Task<PaymentView> CreateAsync(Guid customerId, CreatePaymentCommand command, string idempotencyKey,
        CancellationToken cancellationToken);
    Task<PaymentView?> GetAsync(Guid customerId, Guid paymentId, CancellationToken cancellationToken);
    Task<PaymentPage> ListAsync(Guid customerId, string? status, int limit, string? cursor,
        CancellationToken cancellationToken);
}
