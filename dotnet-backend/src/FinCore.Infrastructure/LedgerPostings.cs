using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

/// <summary>Double-entry postings shared by payments, holds, refunds, reversals and deposits.</summary>
public static class LedgerPostings
{
    // System ledgers have no customer account. One per currency, created on first use.
    public const string PaymentHolds = "system:payment-holds";
    public const string Treasury = "system:treasury";

    public static async Task<LedgerAccountRecord> SystemLedgerAsync(FinCoreDbContext db, string purpose, string currency,
        CancellationToken cancellationToken)
    {
        var code = $"{purpose}:{currency}";
        // ON CONFLICT keeps concurrent first uses from failing on the unique code.
        await db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO ledger_accounts (id, account_id, code, currency, created_at)
            VALUES ({Guid.NewGuid()}, NULL, {code}, {currency}, now())
            ON CONFLICT (code) DO NOTHING
            """, cancellationToken);
        return await db.LedgerAccounts.AsNoTracking().SingleAsync(item => item.Code == code, cancellationToken);
    }

    public static async Task<LedgerAccountRecord> LedgerForAccountAsync(FinCoreDbContext db, Guid accountId,
        CancellationToken cancellationToken) =>
        await db.LedgerAccounts.AsNoTracking().SingleOrDefaultAsync(item => item.AccountId == accountId, cancellationToken)
        ?? throw new PaymentException("ledger_not_configured", "Account ledger is not configured.", 409);

    /// <summary>Adds one balanced journal (debit one ledger, credit another) to the context.</summary>
    public static JournalEntryRecord AddTransfer(FinCoreDbContext db, string businessReference, string currency,
        Guid debitLedgerId, Guid creditLedgerId, long amountMinor, DateTimeOffset postedAt)
    {
        var journal = new JournalEntryRecord { BusinessReference = businessReference, Currency = currency, PostedAt = postedAt };
        var draft = JournalDraft.Create(businessReference, currency,
        [
            new JournalLineDraft(debitLedgerId, EntrySide.Debit, new Money(amountMinor, currency)),
            new JournalLineDraft(creditLedgerId, EntrySide.Credit, new Money(amountMinor, currency))
        ]);
        journal.Lines = draft.Lines.Select(line => new JournalLineRecord
        {
            JournalEntryId = journal.Id,
            LedgerAccountId = line.LedgerAccountId,
            Side = line.Side.ToString().ToLowerInvariant(),
            AmountMinor = line.Amount.AmountMinor
        }).ToList();
        db.JournalEntries.Add(journal);
        return journal;
    }

    /// <summary>
    /// Transaction-scoped advisory locks on customer accounts. Keys are sorted by their string form,
    /// exactly as the Python API sorts them, so operations from either implementation on the same
    /// accounts always lock in the same order and cannot deadlock each other.
    /// </summary>
    public static async Task LockAccountsAsync(FinCoreDbContext db, IEnumerable<Guid> accountIds,
        CancellationToken cancellationToken)
    {
        foreach (var key in accountIds.Select(id => id.ToString()).Distinct().Order(StringComparer.Ordinal))
            await db.Database.ExecuteSqlInterpolatedAsync(
                $"SELECT pg_advisory_xact_lock(hashtextextended({key}, 0))", cancellationToken);
    }

    public static Task<long> BalanceAsync(FinCoreDbContext db, Guid ledgerId, CancellationToken cancellationToken) =>
        db.JournalLines.AsNoTracking().Where(line => line.LedgerAccountId == ledgerId)
            .SumAsync(line => line.Side == "credit" ? line.AmountMinor : -line.AmountMinor, cancellationToken);

    /// <summary>
    /// Adds the mirror image of a journal entry (each debit becomes a credit and vice versa). The
    /// original is never changed; the reversal points at it via ReversalOfId, which is unique, so an
    /// entry is reversed at most once even under concurrent requests.
    /// </summary>
    public static async Task<JournalEntryRecord> ReverseJournalAsync(FinCoreDbContext db, Guid journalId,
        CancellationToken cancellationToken)
    {
        var original = (await db.JournalEntries
            .FromSql($"SELECT * FROM journal_entries WHERE id = {journalId} FOR UPDATE")
            .AsNoTracking().ToListAsync(cancellationToken)).SingleOrDefault()
            ?? throw new PaymentException("not_found", "Journal entry not found.", 404);
        if (original.ReversalOfId is not null)
            throw new PaymentException("reversal_not_reversible", "A reversal cannot itself be reversed.", 409);
        if (await db.JournalEntries.AnyAsync(entry => entry.ReversalOfId == original.Id, cancellationToken))
            throw new PaymentException("already_reversed", "This journal entry has already been reversed.", 409);
        var lines = await db.JournalLines.AsNoTracking().Where(line => line.JournalEntryId == original.Id)
            .ToListAsync(cancellationToken);
        var reversal = new JournalEntryRecord
        {
            BusinessReference = $"reversal:{original.Id}",
            Currency = original.Currency,
            ReversalOfId = original.Id,
            PostedAt = DateTimeOffset.UtcNow
        };
        reversal.Lines = lines.Select(line => new JournalLineRecord
        {
            JournalEntryId = reversal.Id,
            LedgerAccountId = line.LedgerAccountId,
            Side = line.Side == "debit" ? "credit" : "debit",
            AmountMinor = line.AmountMinor
        }).ToList();
        db.JournalEntries.Add(reversal);
        return reversal;
    }
}
