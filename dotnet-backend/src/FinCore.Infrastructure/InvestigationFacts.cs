using System.Globalization;
using FinCore.Application;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

/// <summary>
/// Read-only facts about one fraud case, gathered for the investigator agent. Mirrors the Python
/// <c>app.services.investigation_facts</c> key for key, so the model sees the same evidence
/// whichever backend runs it.
/// </summary>
/// <remarks>
/// Every member is scoped to the case under investigation: none takes an identifier the model
/// could choose, so a manipulated model cannot reach another customer's data. Results are
/// aggregates rather than raw rows. Text a customer typed is returned under
/// <c>customerSuppliedText</c> so the instructions can treat it as data, never as instructions.
/// </remarks>
public sealed class InvestigationFacts(FinCoreDbContext db, FraudCaseRecord fraudCase, DateTimeOffset? now = null)
{
    private readonly DateTimeOffset _now = now ?? DateTimeOffset.UtcNow;
    private PaymentRecord? _payment;
    private bool _loadedPayment;

    private static string Money(long amountMinor, string currency) =>
        $"{(amountMinor / 100m).ToString("N2", CultureInfo.InvariantCulture)} {currency}";

    private int? AgeDays(DateTimeOffset? moment) =>
        moment is null ? null : Math.Max(0, (int)Math.Floor((_now - moment.Value).TotalDays));

    private static string Status(PaymentRecord payment) => payment.Status.ToString().ToLowerInvariant();

    private async Task<PaymentRecord?> PaymentAsync(CancellationToken cancellationToken)
    {
        if (!_loadedPayment)
        {
            _payment = fraudCase.PaymentId is { } id
                ? await db.Payments.AsNoTracking().SingleOrDefaultAsync(item => item.Id == id, cancellationToken)
                : null;
            _loadedPayment = true;
        }
        return _payment;
    }

    /// <summary>The flagged payment and why the fraud rules flagged it.</summary>
    public async Task<object> OverviewAsync(CancellationToken cancellationToken)
    {
        var caseFacts = new
        {
            score = fraudCase.Score, level = fraudCase.Level, status = fraudCase.Status,
            triggeredRules = JsonText.ParseStringArray(fraudCase.Reasons),
            openedDaysAgo = AgeDays(fraudCase.CreatedAt)
        };
        var payment = await PaymentAsync(cancellationToken);
        if (payment is null)
            return new { @case = caseFacts, payment = (object?)null };
        var beneficiary = await db.Beneficiaries.AsNoTracking()
            .SingleOrDefaultAsync(item => item.Id == payment.BeneficiaryId, cancellationToken);
        var paymentFacts = new
        {
            amount = Money(payment.AmountMinor, payment.Currency), status = Status(payment),
            createdDaysAgo = AgeDays(payment.CreatedAt),
            customerSuppliedText = new { reference = payment.Reference }
        };
        if (beneficiary is null)
            return new { @case = caseFacts, payment = paymentFacts };
        return new
        {
            @case = caseFacts, payment = paymentFacts,
            beneficiary = new
            {
                status = beneficiary.Status, addedDaysAgo = AgeDays(beneficiary.CreatedAt),
                verified = beneficiary.VerifiedAt is not null,
                customerSuppliedText = new { label = beneficiary.Label }
            }
        };
    }

    /// <summary>The customer's payments over the window: outcomes and what a typical amount is.</summary>
    public async Task<object> PaymentHistoryAsync(int days, CancellationToken cancellationToken)
    {
        days = Math.Clamp(days, 1, 365);
        var since = _now.AddDays(-days);
        var flagged = await PaymentAsync(cancellationToken);
        var others = await db.Payments.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId && item.CreatedAt >= since)
            .ToListAsync(cancellationToken);
        others = others.Where(item => flagged is null || item.Id != flagged.Id).ToList();
        var byStatus = others.GroupBy(Status).ToDictionary(group => group.Key, group => group.Count());
        var currency = flagged?.Currency ?? others.FirstOrDefault()?.Currency ?? "USD";
        var amounts = others.Where(item => item.Currency == currency).Select(item => item.AmountMinor)
            .Order().ToArray();
        var history = new Dictionary<string, object?>
        {
            ["windowDays"] = days, ["otherPayments"] = others.Count, ["byStatus"] = byStatus
        };
        if (amounts.Length > 0)
        {
            // Same rule as Python's statistics.median truncated to whole minor units.
            var median = amounts.Length % 2 == 1
                ? amounts[amounts.Length / 2]
                : (amounts[amounts.Length / 2 - 1] + amounts[amounts.Length / 2]) / 2;
            history["typicalAmount"] = Money(median, currency);
            history["largestAmount"] = Money(amounts[^1], currency);
            if (flagged is not null)
                history["paymentsAtLeastAsLargeAsFlagged"] = amounts.Count(amount => amount >= flagged.AmountMinor);
        }
        return history;
    }

    /// <summary>Prior payments to this beneficiary, and how many customers pay its account.</summary>
    public async Task<object> BeneficiaryProfileAsync(CancellationToken cancellationToken)
    {
        var payment = await PaymentAsync(cancellationToken);
        var beneficiary = payment is null
            ? null
            : await db.Beneficiaries.AsNoTracking()
                .SingleOrDefaultAsync(item => item.Id == payment.BeneficiaryId, cancellationToken);
        if (payment is null || beneficiary is null)
            return new { available = false };
        var prior = await db.Payments.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId && item.BeneficiaryId == beneficiary.Id
                           && item.Id != payment.Id)
            .ToListAsync(cancellationToken);
        // Many unrelated customers paying one destination account is a money-mule signal.
        var payers = await db.Beneficiaries.AsNoTracking()
            .Where(item => item.DestinationAccountId == beneficiary.DestinationAccountId)
            .Select(item => item.CustomerId).Distinct().CountAsync(cancellationToken);
        var destination = await db.Accounts.AsNoTracking()
            .SingleOrDefaultAsync(item => item.Id == beneficiary.DestinationAccountId, cancellationToken);
        return new
        {
            available = true,
            priorPaymentsToBeneficiary = prior.Count,
            priorOutcomes = prior.GroupBy(Status).ToDictionary(group => group.Key, group => group.Count()),
            customersPayingThisDestinationAccount = payers,
            destinationAccountAgeDays = AgeDays(destination?.CreatedAt),
            destinationAccountStatus = destination?.Status
        };
    }

    /// <summary>KYC standing and AML alerts for the customer (no names, no documents).</summary>
    public async Task<object> ComplianceAsync(CancellationToken cancellationToken)
    {
        var kyc = await db.KycApplications.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId)
            .OrderByDescending(item => item.CreatedAt).FirstOrDefaultAsync(cancellationToken);
        var alerts = await db.AmlAlerts.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId)
            .OrderByDescending(item => item.CreatedAt).Take(20).ToListAsync(cancellationToken);
        return new
        {
            kyc = kyc is null ? null : new { status = kyc.Status, country = kyc.Country, decidedDaysAgo = AgeDays(kyc.DecidedAt) },
            amlAlerts = alerts.Select(alert => new { rule = alert.RuleCode, status = alert.Status, daysAgo = AgeDays(alert.CreatedAt) })
                .ToArray()
        };
    }

    /// <summary>Account ages, recent sign-in activity, and the customer's other fraud cases.</summary>
    public async Task<object> AccountActivityAsync(CancellationToken cancellationToken)
    {
        var accounts = await db.Accounts.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId).ToListAsync(cancellationToken);
        var weekAgo = _now.AddDays(-7);
        var logins = await db.AuditLogs.AsNoTracking()
            .Where(item => item.ActorId == fraudCase.CustomerId && item.CreatedAt >= weekAgo
                           && (item.Action == "auth.login" || item.Action == "auth.login_failed"))
            .GroupBy(item => item.Action).Select(group => new { group.Key, Count = group.Count() })
            .ToDictionaryAsync(item => item.Key, item => item.Count, cancellationToken);
        var otherCases = await db.FraudCases.AsNoTracking()
            .Where(item => item.CustomerId == fraudCase.CustomerId && item.Id != fraudCase.Id)
            .Select(item => item.Status).ToListAsync(cancellationToken);
        return new
        {
            accounts = accounts.Select(account => new { type = account.AccountType, status = account.Status, ageDays = AgeDays(account.CreatedAt) })
                .ToArray(),
            signInsLast7Days = logins.GetValueOrDefault("auth.login"),
            failedSignInsLast7Days = logins.GetValueOrDefault("auth.login_failed"),
            otherFraudCases = otherCases.GroupBy(status => status).ToDictionary(group => group.Key, group => group.Count())
        };
    }
}
