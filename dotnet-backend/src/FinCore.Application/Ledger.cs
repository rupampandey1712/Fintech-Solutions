namespace FinCore.Application;

public sealed record LedgerEntryView(Guid JournalId, DateTimeOffset PostedAt, string BusinessReference,
    string Side, long AmountMinor);

public sealed record LedgerAccountStatement(Guid AccountId, string Currency, long BalanceMinor,
    IReadOnlyList<LedgerEntryView> Entries);
