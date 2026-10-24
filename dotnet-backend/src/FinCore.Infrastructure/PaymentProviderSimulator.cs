using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public sealed record ProviderResult(string Mode, bool Accepted, string ProviderReference, string ProviderStatus);

/// <summary>
/// Deterministic external payment provider simulator (architecture spec, sections 21 and 85).
/// Payments are submitted once and the simulator writes its own record
/// (provider_transactions) as the external system of record that reconciliation compares
/// against. Outcomes are driven by markers in the payment reference:
/// "force-provider-failure", "force-provider-timeout", "force-provider-duplicate",
/// "force-provider-delay", "force-provider-partial" — plus the historical reconciliation
/// markers "force-status-mismatch" / "force-amount-mismatch", which now produce disagreeing
/// provider records instead of imaginary external views.
/// </summary>
public static class PaymentProviderSimulator
{
    public const string MarkerFailure = "force-provider-failure";
    public const string MarkerTimeout = "force-provider-timeout";
    public const string MarkerDuplicate = "force-provider-duplicate";
    public const string MarkerDelay = "force-provider-delay";
    public const string MarkerPartial = "force-provider-partial";

    public const string ModeSuccess = "success";
    public const string ModeFailure = "failure";
    public const string ModeTimeout = "timeout";
    public const string ModeDuplicate = "duplicate";
    public const string ModeDelayed = "delayed";
    public const string ModePartial = "partial_failure";

    public static string ModeForReference(string? reference)
    {
        var text = reference ?? string.Empty;
        if (text.Contains(MarkerFailure, StringComparison.Ordinal)) return ModeFailure;
        if (text.Contains(MarkerTimeout, StringComparison.Ordinal)) return ModeTimeout;
        if (text.Contains(MarkerDuplicate, StringComparison.Ordinal)) return ModeDuplicate;
        if (text.Contains(MarkerDelay, StringComparison.Ordinal)) return ModeDelayed;
        if (text.Contains(MarkerPartial, StringComparison.Ordinal)) return ModePartial;
        return ModeSuccess;
    }

    private static string ProviderStatusFor(string mode) => mode switch
    {
        ModeFailure => "failed",
        // The provider completed the transfer even though the response was lost.
        ModeTimeout => "completed",
        ModeDuplicate => "completed",
        ModeDelayed => "completed",
        ModePartial => "failed",
        _ => "completed"
    };

    /// <summary>Submits a payment to the provider and records the outcome in its ledger.</summary>
    public static async Task<ProviderResult> ProcessPaymentAsync(FinCoreDbContext db, PaymentRecord payment,
        CancellationToken cancellationToken)
    {
        var mode = ModeForReference(payment.Reference);
        var accepted = mode is not (ModeFailure or ModePartial);
        var providerStatus = ProviderStatusFor(mode);
        var providerAmount = payment.AmountMinor;
        // Legacy reconciliation markers keep their meaning through the provider's own record:
        // the transfer completes, but the provider's ledger disagrees with ours.
        var text = payment.Reference ?? string.Empty;
        if (mode == ModeSuccess)
        {
            if (text.Contains("force-status-mismatch", StringComparison.Ordinal))
                providerStatus = "failed";
            else if (text.Contains("force-amount-mismatch", StringComparison.Ordinal))
                providerAmount = payment.AmountMinor * 2;
        }
        var reference = $"provider:{payment.Id}";
        // One payment, one provider row, even if a retried request reaches the provider twice.
        var existing = await db.ProviderTransactions.AsNoTracking()
            .SingleOrDefaultAsync(record => record.PaymentId == payment.Id, cancellationToken);
        if (existing is null)
        {
            db.ProviderTransactions.Add(new ProviderTransactionRecord
            {
                PaymentId = payment.Id, Reference = reference, Status = providerStatus,
                AmountMinor = providerAmount, Currency = payment.Currency, Mode = mode,
                ProcessedAt = DateTimeOffset.UtcNow
            });
        }
        return new ProviderResult(mode, accepted, reference, accepted ? providerStatus : mode);
    }
}
