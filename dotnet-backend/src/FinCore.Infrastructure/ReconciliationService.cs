using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

/// <summary>
/// One reconciliation pass (spec section 20): internal payments are compared with the
/// provider's own transaction records as two independent systems of record. Shared by the
/// manual API endpoint and the scheduled worker.
/// </summary>
public static class ReconciliationService
{
    // Payment statuses that participate in reconciliation: everything money could have moved
    // for, plus "processing" (a timed-out payment whose provider outcome is unknown).
    public static readonly PaymentStatus[] ReconciledStatuses =
        [PaymentStatus.Completed, PaymentStatus.Failed, PaymentStatus.Processing];

    public static async Task<FinCore.Application.ReconciliationRunResponse> ExecuteRunAsync(
        FinCoreDbContext db, CancellationToken cancellationToken)
    {
        // Settling timed-out payments locks their rows, which only holds inside a transaction.
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var now = DateTimeOffset.UtcNow;
        var run = new ReconciliationRunRecord { StartedAt = now };
        var payments = await db.Payments.AsNoTracking()
            .Where(payment => ReconciledStatuses.Contains(payment.Status)).ToListAsync(cancellationToken);
        // The provider's own ledger is the external system of record.
        var providerRecords = await db.ProviderTransactions.AsNoTracking().ToListAsync(cancellationToken);
        var byPaymentId = providerRecords.ToDictionary(record => record.PaymentId);
        var records = new List<ReconciliationRecordRow>(payments.Count + providerRecords.Count);

        void Classify(PaymentRecord? payment, ProviderTransactionRecord? provider, Guid paymentId)
        {
            string status;
            string? externalStatus = null;
            long? externalAmount = null;
            string? externalReference = null;
            if (payment is null || provider is null)
            {
                // missing_internal keeps the provider's numbers (they are the only evidence);
                // missing_external has nothing external to show.
                status = provider is null ? "missing_external" : "missing_internal";
                if (provider is not null)
                {
                    externalStatus = provider.Status;
                    externalAmount = provider.AmountMinor;
                    externalReference = provider.Reference;
                }
            }
            else
            {
                externalStatus = provider.Status;
                externalAmount = provider.AmountMinor;
                externalReference = provider.Reference;
                var internalStatus = payment.Status.ToString().ToLowerInvariant();
                status = provider.Status != internalStatus ? "status_mismatch"
                    : provider.AmountMinor != payment.AmountMinor ? "amount_mismatch"
                    : "matched";
            }
            if (status != "matched")
                FinCoreTelemetry.ReconciliationMismatchTotal.Add(1,
                    new KeyValuePair<string, object?>("type", status));
            records.Add(new ReconciliationRecordRow
            {
                RunId = run.Id,
                PaymentId = paymentId,
                ExternalReference = externalReference,
                InternalStatus = payment?.Status.ToString().ToLowerInvariant() ?? "missing",
                ExternalStatus = externalStatus,
                InternalAmountMinor = payment?.AmountMinor ?? 0,
                ExternalAmountMinor = externalAmount,
                Status = status,
                CreatedAt = now
            });
        }

        foreach (var payment in payments)
        {
            byPaymentId.TryGetValue(payment.Id, out var provider);
            Classify(payment, provider, payment.Id);
        }
        // A payment that exists internally in ANY state (e.g. reversed or rejected after the
        // provider accepted it) is accounted for; only provider records with no internal
        // payment at all are genuinely "missing internal".
        var knownPaymentIds = (await db.Payments.AsNoTracking().Select(payment => payment.Id)
            .ToListAsync(cancellationToken)).ToHashSet();
        foreach (var provider in providerRecords.Where(record => !knownPaymentIds.Contains(record.PaymentId)))
            Classify(null, provider, provider.PaymentId);

        // The records above keep what reconciliation found. A timed-out payment is then settled
        // from the provider's answer, so its held funds reach the beneficiary or return home.
        foreach (var payment in payments.Where(item => item.Status == PaymentStatus.Processing))
            if (byPaymentId.TryGetValue(payment.Id, out var provider))
                await PaymentHoldService.SettleUnconfirmedAsync(db, payment.Id, provider.Status, provider.AmountMinor,
                    cancellationToken);

        run.FinishedAt = DateTimeOffset.UtcNow;
        run.Matched = records.Count(record => record.Status == "matched");
        run.AmountMismatch = records.Count(record => record.Status == "amount_mismatch");
        run.StatusMismatch = records.Count(record => record.Status == "status_mismatch");
        run.MissingExternal = records.Count(record => record.Status == "missing_external");
        run.MissingInternal = records.Count(record => record.Status == "missing_internal");
        db.ReconciliationRuns.Add(run);
        db.ReconciliationRecords.AddRange(records);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return new FinCore.Application.ReconciliationRunResponse(run.Id, run.Matched, run.AmountMismatch,
            run.StatusMismatch, run.MissingExternal, run.MissingInternal, records.Select(ToView).ToArray());
    }

    private static FinCore.Application.ReconciliationRecordView ToView(ReconciliationRecordRow record) =>
        new(record.PaymentId, record.Status, record.InternalStatus, record.ExternalStatus,
            record.InternalAmountMinor, record.ExternalAmountMinor, record.ExternalReference);
}
