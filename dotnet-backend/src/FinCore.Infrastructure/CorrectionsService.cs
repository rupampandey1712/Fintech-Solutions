using System.Text.Json;
using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace FinCore.Infrastructure;

public sealed record RefundView(Guid Id, Guid PaymentId, long AmountMinor, string Currency, string? Reason,
    Guid JournalId, DateTimeOffset CreatedAt);

public sealed record RefundResult(Guid Id, Guid PaymentId, long AmountMinor, string Currency, string? Reason,
    Guid JournalId, DateTimeOffset CreatedAt, long RefundedTotalMinor, long RemainingMinor);

public sealed record RefundPage(IReadOnlyList<RefundView> Items, long RefundedTotalMinor, long RemainingMinor);

public sealed record PaymentReversalResult(Guid PaymentId, string Status, IReadOnlyList<Guid> ReversalJournalIds);

public sealed record JournalReversalResult(Guid JournalId, Guid ReversalJournalId);

public sealed record AdminPaymentView(Guid Id, string Status, long AmountMinor, string Currency, string? Reference,
    long RefundedMinor, DateTimeOffset CreatedAt);

public sealed record AdminDepositView(Guid JournalId, Guid AccountId, long AmountMinor, string Currency,
    DateTimeOffset PostedAt, bool Reversed);

/// <summary>
/// Refunds and reversals, the two ways money that already moved is corrected.
/// A refund is a new business transaction: the recipient returns some or all of a completed payment
/// (partial and repeated up to the original amount, recipient must have the funds, idempotent).
/// A reversal corrects a posting that should never have happened: it mirrors the original journal
/// entries exactly, never edits or deletes them, and as an operational correction does not require
/// the recipient to have the funds. Both are staff operations, audited and announced as events.
/// </summary>
public sealed class CorrectionsService(FinCoreDbContext db)
{
    private const string RefundOperation = "refund:create";
    private const string PaymentReversalOperation = "payment_reversal:create";
    private const string JournalReversalOperation = "journal_reversal:create";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>
    /// Claims an idempotency key inside the caller's transaction. Returns the stored replay
    /// when the key was already used with the same request (null otherwise), and the claim row
    /// so the response can be persisted for later replays.
    /// </summary>
    private async Task<(string? Replay, IdempotencyRecord Claim)> ClaimIdempotencyAsync(Guid actorId,
        string operation, string idempotencyKey, string requestHash, CancellationToken cancellationToken)
    {
        var existing = await db.IdempotencyRecords.AsNoTracking().SingleOrDefaultAsync(record =>
            record.CustomerId == actorId && record.Operation == operation && record.Key == idempotencyKey,
            cancellationToken);
        if (existing is not null)
        {
            if (existing.RequestHash != requestHash)
                throw new PaymentException("idempotency_key_reused", "Idempotency key was used with a different request.", 409);
            return string.IsNullOrWhiteSpace(existing.ResponseJson)
                ? throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409)
                : (existing.ResponseJson, null!);
        }
        var claim = new IdempotencyRecord { CustomerId = actorId, Operation = operation, Key = idempotencyKey, RequestHash = requestHash };
        db.IdempotencyRecords.Add(claim);
        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception) when (exception.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409);
        }
        return (null, claim);
    }

    private static string HashRequest(string canonical) =>
        Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(canonical)));

    public async Task<RefundResult> RefundAsync(Guid actorId, Guid paymentId, long amountMinor, string? reason,
        string idempotencyKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey) || idempotencyKey.Length is < 8 or > 128
            || idempotencyKey.Any(char.IsWhiteSpace))
            throw new PaymentException("invalid_idempotency_key", "Idempotency-Key must be 8 to 128 non-space characters.", 422);
        if (amountMinor <= 0 || reason?.Length > 200)
            throw new PaymentException("validation_error", "Refund request is invalid.", 422);
        var requestHash = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes($"{paymentId:D}|{amountMinor}|{reason}")));

        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var existing = await db.IdempotencyRecords.AsNoTracking().SingleOrDefaultAsync(record =>
            record.CustomerId == actorId && record.Operation == RefundOperation && record.Key == idempotencyKey,
            cancellationToken);
        if (existing is not null)
        {
            if (existing.RequestHash != requestHash)
                throw new PaymentException("idempotency_key_reused", "Idempotency key was used with a different request.", 409);
            return string.IsNullOrWhiteSpace(existing.ResponseJson)
                ? throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409)
                : JsonSerializer.Deserialize<RefundResult>(existing.ResponseJson, JsonOptions)!;
        }
        var claim = new IdempotencyRecord { CustomerId = actorId, Operation = RefundOperation, Key = idempotencyKey, RequestHash = requestHash };
        db.IdempotencyRecords.Add(claim);
        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception) when (exception.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409);
        }

        var (payment, destinationAccountId) = await LockPaymentAsync(paymentId, cancellationToken);
        if (payment.Status != PaymentStatus.Completed)
            throw new PaymentException("payment_not_refundable", "Only completed payments can be refunded.", 409);
        var refunded = await RefundedTotalAsync(payment.Id, cancellationToken);
        if (refunded + amountMinor > payment.AmountMinor)
            throw new PaymentException("refund_exceeds_payment", "Refunds cannot exceed the original payment.", 422);
        var destinationLedger = await LedgerPostings.LedgerForAccountAsync(db, destinationAccountId, cancellationToken);
        var sourceLedger = await LedgerPostings.LedgerForAccountAsync(db, payment.SourceAccountId, cancellationToken);
        if (await LedgerPostings.BalanceAsync(db, destinationLedger.Id, cancellationToken) < amountMinor)
            throw new PaymentException("recipient_insufficient_funds", "The recipient does not have enough balance for this refund.", 422);

        var now = DateTimeOffset.UtcNow;
        var refund = new PaymentRefundRecord
        {
            PaymentId = payment.Id, AmountMinor = amountMinor, Currency = payment.Currency, Reason = reason,
            RequestedBy = actorId, CreatedAt = now
        };
        var journal = LedgerPostings.AddTransfer(db, $"refund:{refund.Id}", payment.Currency, destinationLedger.Id,
            sourceLedger.Id, amountMinor, now);
        refund.JournalId = journal.Id;
        db.PaymentRefunds.Add(refund);
        var total = refunded + amountMinor;
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = payment.Id,
            EventType = "payment.refunded.v1",
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(new
            {
                customerId = payment.CustomerId, paymentId = payment.Id, refundId = refund.Id, amountMinor,
                currency = payment.Currency, refundedTotalMinor = total
            }, JsonOptions),
            CreatedAt = now
        });
        var result = new RefundResult(refund.Id, payment.Id, amountMinor, payment.Currency, reason, journal.Id, now,
            total, payment.AmountMinor - total);
        claim.ResponseJson = JsonSerializer.Serialize(result, JsonOptions);
        await AuditService.RecordAuditAsync(db, actorId, "payment.refunded", "payment", payment.Id,
            newValue: JsonSerializer.Serialize(new { amountMinor, reason, refundId = refund.Id }, JsonOptions),
            cancellationToken: cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return result;
    }

    /// <summary>Visible to the payer and to staff, like the payment itself; null means not found.</summary>
    public async Task<RefundPage?> ListRefundsAsync(Guid callerId, bool isStaff, Guid paymentId,
        CancellationToken cancellationToken)
    {
        var payment = await db.Payments.AsNoTracking().SingleOrDefaultAsync(item => item.Id == paymentId, cancellationToken);
        if (payment is null || (payment.CustomerId != callerId && !isStaff)) return null;
        var items = await db.PaymentRefunds.AsNoTracking().Where(item => item.PaymentId == paymentId)
            .OrderBy(item => item.CreatedAt)
            .Select(item => new RefundView(item.Id, item.PaymentId, item.AmountMinor, item.Currency, item.Reason,
                item.JournalId, item.CreatedAt))
            .ToListAsync(cancellationToken);
        var total = items.Sum(item => item.AmountMinor);
        return new RefundPage(items, total, payment.AmountMinor - total);
    }

    public async Task<PaymentReversalResult> ReversePaymentAsync(Guid actorId, Guid paymentId, string reason,
        string idempotencyKey, CancellationToken cancellationToken)
    {
        ValidateReason(reason);
        if (string.IsNullOrWhiteSpace(idempotencyKey) || idempotencyKey.Length is < 8 or > 128
            || idempotencyKey.Any(char.IsWhiteSpace))
            throw new PaymentException("invalid_idempotency_key", "Idempotency-Key must be 8 to 128 non-space characters.", 422);
        var requestHash = HashRequest($"{{\"payment_id\":\"{paymentId:D}\",\"reason\":{JsonSerializer.Serialize(reason ?? "")}}}");
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var (replay, claim) = await ClaimIdempotencyAsync(actorId, PaymentReversalOperation, idempotencyKey,
            requestHash, cancellationToken);
        if (replay is not null)
            return JsonSerializer.Deserialize<PaymentReversalResult>(replay, JsonOptions)!;
        var (payment, _) = await LockPaymentAsync(paymentId, cancellationToken);
        if (payment.Status != PaymentStatus.Completed)
            throw new PaymentException("payment_not_reversible", "Only completed payments can be reversed.", 409);
        if (await RefundedTotalAsync(payment.Id, cancellationToken) > 0)
            throw new PaymentException("payment_has_refunds", "A payment with refunds cannot be reversed.", 409);

        // A direct payment has one journal; one released from a fraud hold has two (source -> holds,
        // holds -> beneficiary). Reversing all of them restores every ledger.
        var prefix = $"payment:{payment.Id}:";
        var journalIds = await db.JournalEntries.AsNoTracking()
            .Where(entry => (entry.BusinessReference == $"payment:{payment.Id}" || entry.BusinessReference.StartsWith(prefix))
                && entry.ReversalOfId == null)
            .OrderByDescending(entry => entry.PostedAt).Select(entry => entry.Id).ToListAsync(cancellationToken);
        var reversalIds = new List<Guid>();
        foreach (var journalId in journalIds)
            reversalIds.Add((await LedgerPostings.ReverseJournalAsync(db, journalId, cancellationToken)).Id);

        payment.Status = PaymentStateMachine.Transition(PaymentStatus.Completed, PaymentStatus.Reversed);
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = payment.Id,
            EventType = "payment.reversed.v1",
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(new
            {
                customerId = payment.CustomerId, paymentId = payment.Id, amountMinor = payment.AmountMinor,
                currency = payment.Currency
            }, JsonOptions),
            CreatedAt = DateTimeOffset.UtcNow
        });
        await AuditService.RecordAuditAsync(db, actorId, "payment.reversed", "payment", payment.Id,
            oldValue: JsonSerializer.Serialize(new { status = "completed" }, JsonOptions),
            newValue: JsonSerializer.Serialize(new { status = "reversed", reason, reversalJournalIds = reversalIds }, JsonOptions),
            cancellationToken: cancellationToken);
        var result = new PaymentReversalResult(payment.Id, "reversed", reversalIds);
        claim.ResponseJson = JsonSerializer.Serialize(result, JsonOptions);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return result;
    }

    /// <summary>Corrects a non-payment posting, such as a deposit credited to the wrong account.</summary>
    public async Task<JournalReversalResult> ReverseJournalAsync(Guid actorId, Guid journalId, string reason,
        string idempotencyKey, CancellationToken cancellationToken)
    {
        ValidateReason(reason);
        if (string.IsNullOrWhiteSpace(idempotencyKey) || idempotencyKey.Length is < 8 or > 128
            || idempotencyKey.Any(char.IsWhiteSpace))
            throw new PaymentException("invalid_idempotency_key", "Idempotency-Key must be 8 to 128 non-space characters.", 422);
        var requestHash = HashRequest($"{{\"journal_id\":\"{journalId:D}\",\"reason\":{JsonSerializer.Serialize(reason ?? "")}}}");
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var (replay, claim) = await ClaimIdempotencyAsync(actorId, JournalReversalOperation, idempotencyKey,
            requestHash, cancellationToken);
        if (replay is not null)
            return JsonSerializer.Deserialize<JournalReversalResult>(replay, JsonOptions)!;
        var journal = await db.JournalEntries.AsNoTracking().SingleOrDefaultAsync(entry => entry.Id == journalId, cancellationToken)
            ?? throw new PaymentException("not_found", "Journal entry not found.", 404);
        // Payment journals go through the payment reversal so status, holds and refunds stay consistent.
        if (journal.BusinessReference.StartsWith("payment:") || journal.BusinessReference.StartsWith("refund:"))
            throw new PaymentException("use_payment_reversal", "Payment postings are reversed through the payment.", 409);
        var accountIds = await (from line in db.JournalLines
                                join ledger in db.LedgerAccounts on line.LedgerAccountId equals ledger.Id
                                where line.JournalEntryId == journalId && ledger.AccountId != null
                                select ledger.AccountId!.Value).ToListAsync(cancellationToken);
        await LedgerPostings.LockAccountsAsync(db, accountIds, cancellationToken);
        var reversal = await LedgerPostings.ReverseJournalAsync(db, journalId, cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await AuditService.RecordAuditAsync(db, actorId, "journal.reversed", "journal_entry", journalId,
            newValue: JsonSerializer.Serialize(new { reason, reversalId = reversal.Id }, JsonOptions),
            cancellationToken: cancellationToken);
        var result = new JournalReversalResult(journalId, reversal.Id);
        claim.ResponseJson = JsonSerializer.Serialize(result, JsonOptions);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return result;
    }

    public async Task<IReadOnlyList<AdminPaymentView>> ListCustomerPaymentsAsync(Guid customerId,
        CancellationToken cancellationToken)
    {
        var payments = await db.Payments.AsNoTracking().Where(item => item.CustomerId == customerId)
            .OrderByDescending(item => item.CreatedAt).Take(100).ToListAsync(cancellationToken);
        var ids = payments.Select(item => item.Id).ToList();
        var refunded = await db.PaymentRefunds.AsNoTracking().Where(item => ids.Contains(item.PaymentId))
            .GroupBy(item => item.PaymentId).Select(group => new { group.Key, Total = group.Sum(item => item.AmountMinor) })
            .ToDictionaryAsync(item => item.Key, item => item.Total, cancellationToken);
        return payments.Select(item => new AdminPaymentView(item.Id, item.Status.ToString().ToLowerInvariant(),
            item.AmountMinor, item.Currency, item.Reference, refunded.GetValueOrDefault(item.Id), item.CreatedAt)).ToList();
    }

    public async Task<IReadOnlyList<AdminDepositView>> ListCustomerDepositsAsync(Guid customerId,
        CancellationToken cancellationToken)
    {
        var rows = await (from entry in db.JournalEntries
                          join line in db.JournalLines on entry.Id equals line.JournalEntryId
                          join ledger in db.LedgerAccounts on line.LedgerAccountId equals ledger.Id
                          join account in db.Accounts on ledger.AccountId equals account.Id
                          where account.CustomerId == customerId && entry.BusinessReference.StartsWith("deposit:")
                                && line.Side == "credit"
                          orderby entry.PostedAt descending
                          select new
                          {
                              entry.Id, AccountId = account.Id, line.AmountMinor, entry.Currency, entry.PostedAt,
                              Reversed = db.JournalEntries.Any(other => other.ReversalOfId == entry.Id)
                          }).Take(100).ToListAsync(cancellationToken);
        return rows.Select(row => new AdminDepositView(row.Id, row.AccountId, row.AmountMinor, row.Currency,
            row.PostedAt, row.Reversed)).ToList();
    }

    /// <summary>
    /// Takes the same account locks as payments (same order), then locks the payment row, so refunds,
    /// reversals and payments touching these accounts serialise and balance checks cannot go stale.
    /// </summary>
    private async Task<(PaymentRecord Payment, Guid DestinationAccountId)> LockPaymentAsync(Guid paymentId,
        CancellationToken cancellationToken)
    {
        var unlocked = await db.Payments.AsNoTracking().SingleOrDefaultAsync(item => item.Id == paymentId, cancellationToken)
            ?? throw new PaymentException("not_found", "Payment not found.", 404);
        var beneficiary = await db.Beneficiaries.AsNoTracking()
            .SingleOrDefaultAsync(item => item.Id == unlocked.BeneficiaryId, cancellationToken)
            ?? throw new PaymentException("beneficiary_missing", "Beneficiary no longer exists.", 409);
        await LedgerPostings.LockAccountsAsync(db, [unlocked.SourceAccountId, beneficiary.DestinationAccountId], cancellationToken);
        var payment = (await db.Payments.FromSql($"SELECT * FROM payments WHERE id = {paymentId} FOR UPDATE")
            .ToListAsync(cancellationToken)).Single();
        return (payment, beneficiary.DestinationAccountId);
    }

    private async Task<long> RefundedTotalAsync(Guid paymentId, CancellationToken cancellationToken) =>
        await db.PaymentRefunds.AsNoTracking().Where(item => item.PaymentId == paymentId)
            .SumAsync(item => (long?)item.AmountMinor, cancellationToken) ?? 0;

    private static void ValidateReason(string? reason)
    {
        if (string.IsNullOrWhiteSpace(reason) || reason.Trim().Length < 3 || reason.Length > 200)
            throw new PaymentException("validation_error", "A reason of 3 to 200 characters is required.", 422);
    }
}
