namespace FinCore.Application;

public sealed record AccountLimitDefaults(long PerTransactionMinor, long DailyMinor, long WeeklyMinor, long MonthlyMinor);
public sealed record AccountView(Guid Id, string AccountType, string Currency, string Status,
    long LedgerBalanceMinor, long AvailableBalanceMinor, DateTimeOffset CreatedAt);
public sealed record AccountPage(IReadOnlyList<AccountView> Items, string? NextCursor);

public interface IAccountService
{
    Task<AccountView> CreateAsync(Guid customerId, string accountType, string currency, CancellationToken cancellationToken);
    Task<AccountPage> ListAsync(Guid customerId, CancellationToken cancellationToken);
    Task<AccountView?> GetAsync(Guid customerId, Guid accountId, CancellationToken cancellationToken);
}
