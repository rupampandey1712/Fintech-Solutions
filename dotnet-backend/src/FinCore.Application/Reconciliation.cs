namespace FinCore.Application;

public sealed record ReconciliationRecordView(Guid PaymentId, string Status, string InternalStatus,
    string? ExternalStatus, long InternalAmountMinor, long? ExternalAmountMinor, string? ExternalReference);

public sealed record ReconciliationRunView(Guid Id, DateTimeOffset StartedAt, DateTimeOffset? FinishedAt,
    int Matched, int AmountMismatch, int StatusMismatch, int MissingExternal, int MissingInternal);

public sealed record ReconciliationRunResponse(Guid RunId, int Matched, int AmountMismatch, int StatusMismatch,
    int MissingExternal, int MissingInternal, IReadOnlyList<ReconciliationRecordView> Records);

public sealed record ReconciliationRunDetail(Guid Id, DateTimeOffset StartedAt, DateTimeOffset? FinishedAt,
    int Matched, int AmountMismatch, int StatusMismatch, int MissingExternal, int MissingInternal,
    IReadOnlyList<ReconciliationRecordView> Records);
