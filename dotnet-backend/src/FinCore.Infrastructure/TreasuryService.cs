using System.Text.Json;
using FinCore.Application;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace FinCore.Infrastructure;

public sealed record DepositView(Guid JournalId, Guid AccountId, long AmountMinor, string Currency, string? Reference,
    long BalanceMinor, DateTimeOffset PostedAt);

public sealed record AdminAccountView(Guid Id, string AccountType, string Currency, string Status, long BalanceMinor,
    DateTimeOffset CreatedAt);

/// <summary>
/// Staff-initiated deposits: the only way money enters the platform. Each deposit is one balanced
/// journal (debit the per-currency treasury ledger, credit the customer's ledger), idempotent per
/// actor and Idempotency-Key, audited, and announced with account.funded.v1.
/// </summary>
public sealed class TreasuryService(FinCoreDbContext db)
{
    private const string Operation = "deposit:create";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public async Task<IReadOnlyList<AdminAccountView>> ListCustomerAccountsAsync(Guid customerId,
        CancellationToken cancellationToken)
    {
        var accounts = await db.Accounts.AsNoTracking().Where(item => item.CustomerId == customerId)
            .OrderBy(item => item.CreatedAt).ToListAsync(cancellationToken);
        var result = new List<AdminAccountView>(accounts.Count);
        foreach (var account in accounts)
            result.Add(new AdminAccountView(account.Id, account.AccountType, account.Currency, account.Status,
                await BalanceAsync(account.Id, cancellationToken), account.CreatedAt));
        return result;
    }

    public async Task<DepositView> DepositAsync(Guid actorId, Guid accountId, long amountMinor, string? reference,
        string idempotencyKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey) || idempotencyKey.Length is < 8 or > 128
            || idempotencyKey.Any(char.IsWhiteSpace))
            throw new PaymentException("invalid_idempotency_key", "Idempotency-Key must be 8 to 128 non-space characters.", 422);
        if (amountMinor is <= 0 or > 100_000_000_000 || reference?.Length > 140)
            throw new PaymentException("validation_error", "Deposit request is invalid.", 422);
        var requestHash = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes($"{accountId:D}|{amountMinor}|{reference}")));

        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var existing = await db.IdempotencyRecords.AsNoTracking().SingleOrDefaultAsync(record =>
            record.CustomerId == actorId && record.Operation == Operation && record.Key == idempotencyKey, cancellationToken);
        if (existing is not null)
        {
            if (existing.RequestHash != requestHash)
                throw new PaymentException("idempotency_key_reused", "Idempotency key was used with a different request.", 409);
            return string.IsNullOrWhiteSpace(existing.ResponseJson)
                ? throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409)
                : JsonSerializer.Deserialize<DepositView>(existing.ResponseJson, JsonOptions)!;
        }

        var claim = new IdempotencyRecord { CustomerId = actorId, Operation = Operation, Key = idempotencyKey, RequestHash = requestHash };
        db.IdempotencyRecords.Add(claim);
        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception) when (exception.InnerException is PostgresException
            { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409);
        }

        var account = (await db.Accounts.FromSql($"SELECT * FROM customer_accounts WHERE id = {accountId} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault()
            ?? throw new PaymentException("not_found", "Account not found.", 404);
        if (account.Status != "active")
            throw new PaymentException("account_not_active", "Account is not active.", 409);
        var ledger = await LedgerPostings.LedgerForAccountAsync(db, account.Id, cancellationToken);
        var treasury = await LedgerPostings.SystemLedgerAsync(db, LedgerPostings.Treasury, account.Currency, cancellationToken);

        var now = DateTimeOffset.UtcNow;
        var journal = LedgerPostings.AddTransfer(db, $"deposit:{actorId}:{idempotencyKey}", account.Currency,
            treasury.Id, ledger.Id, amountMinor, now);
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = account.Id,
            EventType = "account.funded.v1",
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(new
            {
                customerId = account.CustomerId,
                accountId = account.Id,
                journalId = journal.Id,
                amountMinor,
                currency = account.Currency
            }, JsonOptions),
            CreatedAt = now
        });
        await db.SaveChangesAsync(cancellationToken);

        var response = new DepositView(journal.Id, account.Id, amountMinor, account.Currency, reference,
            await BalanceAsync(account.Id, cancellationToken), now);
        claim.ResponseJson = JsonSerializer.Serialize(response, JsonOptions);
        await AuditService.RecordAuditAsync(db, actorId, "treasury.deposit", "account", account.Id,
            oldValue: null, newValue: JsonSerializer.Serialize(new { amountMinor, reference }, JsonOptions),
            cancellationToken: cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return response;
    }

    private Task<long> BalanceAsync(Guid accountId, CancellationToken cancellationToken) =>
        (from line in db.JournalLines.AsNoTracking()
         join ledger in db.LedgerAccounts.AsNoTracking() on line.LedgerAccountId equals ledger.Id
         where ledger.AccountId == accountId
         select line.Side == "credit" ? line.AmountMinor : -line.AmountMinor).SumAsync(cancellationToken);
}
