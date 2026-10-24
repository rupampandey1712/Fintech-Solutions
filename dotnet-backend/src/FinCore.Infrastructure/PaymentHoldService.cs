using System.Text.Json;
using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

/// <summary>
/// Settles payments held for fraud review. A held payment is Pending and its funds sit in the
/// payment-holds clearing ledger. The reviewer's decision settles it:
/// false_positive releases (holds → beneficiary, Completed); closed, i.e. confirmed fraud, refunds
/// (holds → source, Rejected); escalated moves no money and the payment stays held.
/// A payment whose provider call timed out is Processing with its funds in the same holds ledger;
/// reconciliation settles it from the provider's record (<see cref="SettleUnconfirmedAsync"/>).
/// </summary>
public static class PaymentHoldService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static OutboxEventRecord CompletedEvent(PaymentRecord payment, DateTimeOffset at) =>
        LifecycleEvent("payment.completed.v1", "completed", payment, at);

    private static OutboxEventRecord LifecycleEvent(string eventType, string status, PaymentRecord payment,
        DateTimeOffset at) => new()
    {
        AggregateId = payment.Id,
        EventType = eventType,
        SchemaVersion = 1,
        PayloadJson = JsonSerializer.Serialize(new
        {
            paymentId = payment.Id,
            customerId = payment.CustomerId,
            sourceAccountId = payment.SourceAccountId,
            beneficiaryId = payment.BeneficiaryId,
            amountMinor = payment.AmountMinor,
            currency = payment.Currency,
            status,
            createdAt = payment.CreatedAt
        }, JsonOptions),
        CreatedAt = at
    };

    /// <summary>
    /// Applies a review decision inside the caller's transaction. Returns the payment's new status,
    /// or null when nothing changed. The payment row is locked so two reviewers cannot both settle
    /// it, and anything no longer Pending is left alone.
    /// </summary>
    public static async Task<string?> SettleAsync(FinCoreDbContext db, Guid? paymentId, string resolution,
        CancellationToken cancellationToken)
    {
        if (paymentId is null || resolution is not ("false_positive" or "closed"))
            return null;
        var payment = (await db.Payments
            .FromSql($"SELECT * FROM payments WHERE id = {paymentId.Value} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault();
        if (payment is null || payment.Status != PaymentStatus.Pending)
            return null;

        var now = DateTimeOffset.UtcNow;
        var holds = await LedgerPostings.SystemLedgerAsync(db, LedgerPostings.PaymentHolds, payment.Currency, cancellationToken);
        if (resolution == "false_positive")
        {
            var beneficiary = await db.Beneficiaries.AsNoTracking()
                .SingleOrDefaultAsync(item => item.Id == payment.BeneficiaryId, cancellationToken)
                ?? throw new PaymentException("beneficiary_missing", "Beneficiary no longer exists.", 409);
            var destination = await LedgerPostings.LedgerForAccountAsync(db, beneficiary.DestinationAccountId, cancellationToken);
            LedgerPostings.AddTransfer(db, $"payment:{payment.Id}:release", payment.Currency, holds.Id, destination.Id,
                payment.AmountMinor, now);
            payment.Status = PaymentStateMachine.Transition(
                PaymentStateMachine.Transition(PaymentStatus.Pending, PaymentStatus.Processing), PaymentStatus.Completed);
            db.OutboxEvents.Add(CompletedEvent(payment, now));
        }
        else
        {
            var source = await LedgerPostings.LedgerForAccountAsync(db, payment.SourceAccountId, cancellationToken);
            LedgerPostings.AddTransfer(db, $"payment:{payment.Id}:refund", payment.Currency, holds.Id, source.Id,
                payment.AmountMinor, now);
            payment.Status = PaymentStateMachine.Transition(PaymentStatus.Pending, PaymentStatus.Rejected);
            db.OutboxEvents.Add(new OutboxEventRecord
            {
                AggregateId = payment.Id,
                EventType = "payment.rejected.v1",
                SchemaVersion = 1,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    paymentId = payment.Id,
                    customerId = payment.CustomerId,
                    amountMinor = payment.AmountMinor,
                    currency = payment.Currency
                }, JsonOptions),
                CreatedAt = now
            });
        }
        return payment.Status.ToString().ToLowerInvariant();
    }

    /// <summary>
    /// Settles a timed-out payment from the provider's record inside the caller's transaction:
    /// provider completed releases (holds → beneficiary, Completed), provider failed refunds
    /// (holds → source, Failed). Returns the new status, or null when nothing changed. Only a
    /// Processing payment whose provider record has a final status and the same amount is
    /// settled; contradictory evidence is left for a human rather than moving money on it.
    /// </summary>
    public static async Task<string?> SettleUnconfirmedAsync(FinCoreDbContext db, Guid paymentId,
        string providerStatus, long providerAmountMinor, CancellationToken cancellationToken)
    {
        if (providerStatus is not ("completed" or "failed"))
            return null;
        var payment = (await db.Payments
            .FromSql($"SELECT * FROM payments WHERE id = {paymentId} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault();
        if (payment is null || payment.Status != PaymentStatus.Processing || payment.AmountMinor != providerAmountMinor)
            return null;

        var now = DateTimeOffset.UtcNow;
        var holds = await LedgerPostings.SystemLedgerAsync(db, LedgerPostings.PaymentHolds, payment.Currency, cancellationToken);
        if (providerStatus == "completed")
        {
            var beneficiary = await db.Beneficiaries.AsNoTracking()
                .SingleOrDefaultAsync(item => item.Id == payment.BeneficiaryId, cancellationToken)
                ?? throw new PaymentException("beneficiary_missing", "Beneficiary no longer exists.", 409);
            var destination = await LedgerPostings.LedgerForAccountAsync(db, beneficiary.DestinationAccountId, cancellationToken);
            LedgerPostings.AddTransfer(db, $"payment:{payment.Id}:release", payment.Currency, holds.Id, destination.Id,
                payment.AmountMinor, now);
            payment.Status = PaymentStateMachine.Transition(PaymentStatus.Processing, PaymentStatus.Completed);
            payment.ProviderStatus = providerStatus;
            db.OutboxEvents.Add(CompletedEvent(payment, now));
        }
        else
        {
            var source = await LedgerPostings.LedgerForAccountAsync(db, payment.SourceAccountId, cancellationToken);
            LedgerPostings.AddTransfer(db, $"payment:{payment.Id}:refund", payment.Currency, holds.Id, source.Id,
                payment.AmountMinor, now);
            payment.Status = PaymentStateMachine.Transition(PaymentStatus.Processing, PaymentStatus.Failed);
            payment.ProviderStatus = providerStatus;
            db.OutboxEvents.Add(LifecycleEvent("payment.failed.v1", "failed", payment, now));
        }
        return payment.Status.ToString().ToLowerInvariant();
    }
}
