using FinCore.Application;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public sealed class AccountService(FinCoreDbContext db, AccountLimitDefaults defaults) : IAccountService
{
    public async Task<AccountView> CreateAsync(Guid customerId, string accountType, string currency,
        CancellationToken cancellationToken)
    {
        var account = new CustomerAccountRecord
        {
            CustomerId = customerId,
            AccountType = accountType,
            Currency = currency
        };
        var ledgerAccount = new LedgerAccountRecord
        {
            AccountId = account.Id,
            Code = $"customer:{customerId}:account:{account.Id}",
            Currency = currency
        };
        var limit = new AccountLimitRecord
        {
            AccountId = account.Id,
            PerTransactionMinor = defaults.PerTransactionMinor,
            DailyMinor = defaults.DailyMinor,
            WeeklyMinor = defaults.WeeklyMinor,
            MonthlyMinor = defaults.MonthlyMinor
        };

        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        db.Accounts.Add(account);
        db.LedgerAccounts.Add(ledgerAccount);
        db.AccountLimits.Add(limit);
        // Spec section 17: AccountCreated, for downstream services (welcome flows, limits
        // provisioning) that must not couple to the accounts endpoint.
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = account.Id,
            EventType = "account.created.v1",
            SchemaVersion = 1,
            PayloadJson = System.Text.Json.JsonSerializer.Serialize(new
            {
                customerId, accountId = account.Id, accountType = account.AccountType, currency
            }),
            CreatedAt = DateTimeOffset.UtcNow
        });
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return ToView(account, 0);
    }

    public async Task<AccountPage> ListAsync(Guid customerId, CancellationToken cancellationToken)
    {
        var accounts = await db.Accounts.AsNoTracking()
            .Where(account => account.CustomerId == customerId)
            .OrderByDescending(account => account.CreatedAt)
            .ToListAsync(cancellationToken);
        var balances = await GetBalancesAsync(accounts.Select(account => account.Id).ToArray(), cancellationToken);
        return new AccountPage(accounts.Select(account => ToView(account, balances.GetValueOrDefault(account.Id))).ToArray(), null);
    }

    public async Task<AccountView?> GetAsync(Guid customerId, Guid accountId, CancellationToken cancellationToken)
    {
        var account = await db.Accounts.AsNoTracking().SingleOrDefaultAsync(
            item => item.Id == accountId && item.CustomerId == customerId, cancellationToken);
        if (account is null) return null;
        var balances = await GetBalancesAsync([accountId], cancellationToken);
        return ToView(account, balances.GetValueOrDefault(accountId));
    }

    private async Task<Dictionary<Guid, long>> GetBalancesAsync(Guid[] accountIds, CancellationToken cancellationToken)
    {
        if (accountIds.Length == 0) return new Dictionary<Guid, long>();
        return await (from ledger in db.LedgerAccounts.AsNoTracking()
                      join line in db.JournalLines.AsNoTracking() on ledger.Id equals line.LedgerAccountId
                      where ledger.AccountId != null && accountIds.Contains(ledger.AccountId.Value)
                      group line by ledger.AccountId!.Value into entries
                      select new
                      {
                          AccountId = entries.Key,
                          Balance = entries.Sum(line => line.Side == "credit" ? line.AmountMinor : -line.AmountMinor)
                      })
            .ToDictionaryAsync(row => row.AccountId, row => row.Balance, cancellationToken);
    }

    private static AccountView ToView(CustomerAccountRecord account, long balance) =>
        new(account.Id, account.AccountType, account.Currency, account.Status, balance, balance, account.CreatedAt);
}
