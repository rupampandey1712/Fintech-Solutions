namespace FinCore.Application;

public sealed record BeneficiaryView(Guid Id, Guid DestinationAccountId, string Label, string Status,
    DateTimeOffset? VerifiedAt, DateTimeOffset? CoolingUntil, DateTimeOffset CreatedAt);

public interface IBeneficiaryService
{
    Task<BeneficiaryView> AddAsync(Guid customerId, Guid destinationAccountId, string label, CancellationToken cancellationToken);
    Task<IReadOnlyList<BeneficiaryView>> ListAsync(Guid customerId, CancellationToken cancellationToken);
    Task<BeneficiaryView> VerifyAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken);
    Task<BeneficiaryView> ActivateAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken);
    Task<BeneficiaryView> DisableAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken);
}

public sealed class BeneficiaryException(string code, string message, int statusCode) : Exception(message)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
}
