namespace FinCore.Application;

public sealed record TransactionView(Guid Id, Guid PaymentId, Guid AccountId, string Direction,
    Guid? CounterpartyAccountId, long AmountMinor, string Currency, DateTimeOffset OccurredAt, string Status,
    string? Reference);
