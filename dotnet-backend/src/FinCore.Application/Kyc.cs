namespace FinCore.Application;

public sealed record KycDocumentView(Guid Id, string DocumentType, string BlobPath, string Status,
    DateTimeOffset UploadedAt);

public sealed record KycApplicationView(Guid Id, string Status, string FullName, string Country,
    string? RejectionReason, DateTimeOffset? SubmittedAt, DateTimeOffset? DecidedAt, DateTimeOffset CreatedAt,
    IReadOnlyList<KycDocumentView> Documents);

public sealed class KycException(string code, string message, int statusCode, Exception? innerException = null)
    : Exception(message, innerException)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
}

public interface IKycService
{
    Task<KycApplicationView> CreateAsync(Guid customerId, string fullName, string country,
        CancellationToken cancellationToken);
    Task<KycDocumentView> AddDocumentAsync(Guid customerId, Guid applicationId, string documentType,
        string contentBase64, CancellationToken cancellationToken);
    Task<KycApplicationView> SubmitAsync(Guid customerId, Guid applicationId, CancellationToken cancellationToken);
    Task<KycApplicationView?> GetAsync(Guid customerId, Guid applicationId, CancellationToken cancellationToken);
    Task<IReadOnlyList<KycApplicationView>> ListAsync(Guid customerId, CancellationToken cancellationToken);
    Task<KycApplicationView?> ReviewAsync(Guid applicationId, Guid reviewerId, bool approve, string? reason,
        CancellationToken cancellationToken);
}
