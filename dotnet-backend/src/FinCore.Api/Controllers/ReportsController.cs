using System.Globalization;
using System.Security.Claims;
using System.Text;
using FinCore.Application;
using FinCore.Domain;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

[Route("api/v1/reports")]
[Authorize]
[Tags("reports")]
public sealed class ReportsController(FinCoreDbContext db) : FinCoreControllerBase
{
    [HttpGet("dashboard")]
    [ProducesResponseType<DashboardView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Dashboard(CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        var seeAll = EndpointSupport.HasAnyRole(roles, "admin");
        var now = DateTimeOffset.UtcNow;

        var payments = await db.Payments.AsNoTracking()
            .Where(payment => seeAll || payment.CustomerId == CustomerId)
            .Select(payment => new { payment.Status, payment.RiskDecision, payment.AmountMinor, payment.Currency, payment.CreatedAt })
            .ToListAsync(cancellationToken);
        var total = payments.Count;
        var completed = payments.Count(payment => payment.Status == PaymentStatus.Completed);
        var failed = payments.Count(payment => payment.Status == PaymentStatus.Failed);
        var blocked = payments.Count(payment => payment.RiskDecision == "block");
        var successRate = total == 0 ? (double?)null : Math.Round(100.0 * completed / total, 2);

        var monthStart = new DateTimeOffset(now.Year, now.Month, 1, 0, 0, 0, TimeSpan.Zero).AddMonths(-5);
        var fraudCases = await db.FraudCases.AsNoTracking()
            .Where(item => seeAll || item.CustomerId == CustomerId).ToListAsync(cancellationToken);
        var amlAlerts = await db.AmlAlerts.AsNoTracking()
            .Where(item => seeAll || item.CustomerId == CustomerId).ToListAsync(cancellationToken);

        var months = Enumerable.Range(0, 6)
            .Select(index => monthStart.AddMonths(index).ToString("yyyy-MM", CultureInfo.InvariantCulture)).ToArray();
        var monthlyVolume = BucketByCurrency(months,
                payments.Select(payment => (MonthKey(payment.CreatedAt), payment.Currency, payment.AmountMinor)))
            .Select(point => new MonthVolume(point.Key, point.Currency, point.Count, point.VolumeMinor))
            .ToArray();

        return Ok(new DashboardView(
            new DashboardTotals(total, completed, failed, blocked),
            successRate,
            new CategoryCount(fraudCases.Count(item => item.Status == "open"), fraudCases.Count),
            new CategoryCount(amlAlerts.Count(item => item.Status == "open"), amlAlerts.Count),
            monthlyVolume));
    }

    [HttpGet("transactions")]
    [ProducesResponseType<TransactionsReport>(StatusCodes.Status200OK)]
    public async Task<IActionResult> TransactionSeries([FromQuery] int? days, CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        var seeAll = EndpointSupport.HasAnyRole(roles, "admin");
        var windowDays = Math.Clamp(days ?? 30, 1, 90);
        var now = DateTimeOffset.UtcNow;
        // Midnight UTC as a UTC DateTimeOffset. DateTimeOffset.Date is an unspecified-kind
        // DateTime that converts back with the host's local offset, which Npgsql rejects for
        // timestamptz on any host not running in UTC.
        var start = new DateTimeOffset(now.UtcDateTime.Date, TimeSpan.Zero).AddDays(-(windowDays - 1));

        var rows = await db.Payments.AsNoTracking()
            .Where(payment => (seeAll || payment.CustomerId == CustomerId) && payment.CreatedAt >= start)
            .Select(payment => new { payment.CreatedAt, payment.AmountMinor, payment.Currency })
            .ToListAsync(cancellationToken);
        var dayKeys = Enumerable.Range(0, windowDays)
            .Select(index => start.AddDays(index).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).ToArray();
        var series = BucketByCurrency(dayKeys, rows.Select(row => (DayKey(row.CreatedAt), row.Currency, row.AmountMinor)))
            .Select(point => new DayVolume(point.Key, point.Currency, point.Count, point.VolumeMinor))
            .ToArray();
        return Ok(new TransactionsReport(series));
    }

    [HttpGet("fraud")]
    public async Task<IActionResult> Fraud(CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        if (!EndpointSupport.HasAnyRole(roles, DashboardRoles))
            return ProblemResult(403, "forbidden", "Access denied");
        var cases = await db.FraudCases.AsNoTracking().ToListAsync(cancellationToken);
        var payments = await db.Payments.AsNoTracking()
            .Select(payment => new { payment.RiskLevel, payment.RiskDecision }).ToListAsync(cancellationToken);
        var casesByStatus = cases.GroupBy(item => item.Status)
            .ToDictionary(group => group.Key, group => group.Count());
        var riskDistribution = payments.GroupBy(payment => payment.RiskLevel ?? "low")
            .ToDictionary(group => group.Key, group => group.Count());
        var topRules = cases.SelectMany(item => JsonText.ParseStringArray(item.Reasons))
            .GroupBy(reason => reason).OrderByDescending(group => group.Count()).Take(5)
            .Select(group => new { rule = group.Key, triggered = group.Count() }).ToArray();
        return Ok(new
        {
            casesByStatus,
            riskDistribution,
            blockedPayments = payments.Count(payment => payment.RiskDecision == "block"),
            topRules
        });
    }

    [HttpGet("finance")]
    public async Task<IActionResult> Finance(CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        if (!EndpointSupport.HasAnyRole(roles, "admin", "finance_user", "operations_user"))
            return ProblemResult(403, "forbidden", "Access denied");
        var batches = await db.SettlementBatches.AsNoTracking().ToListAsync(cancellationToken);
        var runs = await db.ReconciliationRuns.AsNoTracking()
            .OrderByDescending(run => run.StartedAt).FirstOrDefaultAsync(cancellationToken);
        var volume = await db.Payments.AsNoTracking()
            .SumAsync(payment => (long?)payment.AmountMinor, cancellationToken) ?? 0;
        var count = await db.Payments.AsNoTracking().CountAsync(cancellationToken);
        return Ok(new
        {
            settlement = new
            {
                batches = batches.Count,
                completed = batches.Count(batch => batch.Status == "completed"),
                failed = batches.Count(batch => batch.Status == "failed"),
                settledVolumeMinor = batches.Sum(batch => batch.TotalMinor)
            },
            reconciliation = new Dictionary<string, int>
            {
                ["runs"] = await db.ReconciliationRuns.AsNoTracking().CountAsync(cancellationToken),
                ["matched"] = runs?.Matched ?? 0,
                ["amountMismatch"] = runs?.AmountMismatch ?? 0,
                ["statusMismatch"] = runs?.StatusMismatch ?? 0,
                ["missingExternal"] = runs?.MissingExternal ?? 0,
                ["missingInternal"] = runs?.MissingInternal ?? 0
            },
            transactionVolumeMinor = volume,
            paymentCount = count
        });
    }

    [HttpGet("{reportType}/export")]
    public async Task<IActionResult> Export(string reportType, CancellationToken cancellationToken)
    {
        var roles = await EndpointSupport.CurrentRolesAsync(db, CustomerId, cancellationToken);
        var staffOnly = reportType is "fraud" or "aml" or "reconciliation" or "settlement";
        if (staffOnly && !EndpointSupport.HasAnyRole(roles, DashboardRoles))
            return ProblemResult(403, "forbidden", "Access denied");
        var rows = await ExportRowsAsync(reportType, cancellationToken);
        if (rows is null)
            return ProblemResult(404, "not_found", "Resource not found",
                "Unknown report type; use daily-transactions, payment-success, fraud, aml, "
                + "reconciliation, ledger or settlement");
        var builder = new StringBuilder();
        foreach (var row in rows)
            builder.AppendLine(string.Join(",", row.Select(EscapeCsv)));
        var filename = $"{reportType}-{DateTimeOffset.UtcNow:yyyyMMdd}.csv";
        var storedPath = await ReportStore.SaveReportAsync(filename, Encoding.UTF8.GetBytes(builder.ToString()));
        Response.Headers["X-Report-Path"] = storedPath;
        return Content(builder.ToString(), "text/csv", Encoding.UTF8);
    }

    public static readonly string[] DashboardRoles =
        ["admin", "fraud_analyst", "finance_user", "operations_user", "compliance_officer"];

    private static string EscapeCsv(string value) =>
        value.Contains(',') || value.Contains('"') || value.Contains('\n')
            ? $"\"{value.Replace("\"", "\"\"")}\""
            : value;

    private async Task<List<List<string>>?> ExportRowsAsync(string reportType, CancellationToken cancellationToken)
    {
        switch (reportType)
        {
            case "daily-transactions":
            {
                var payments = await db.Payments.AsNoTracking().ToListAsync(cancellationToken);
                var byDay = payments.GroupBy(payment => payment.CreatedAt.UtcDateTime.ToString("yyyy-MM-dd"))
                    .OrderBy(group => group.Key);
                List<List<string>> result =
                [
                    ["day", "payments", "volume_minor", "completed", "failed"],
                    ..byDay.Select(group => new List<string>
                    {
                        group.Key, group.Count().ToString(),
                        group.Sum(payment => payment.AmountMinor).ToString(),
                        group.Count(payment => payment.Status == PaymentStatus.Completed).ToString(),
                        group.Count(payment => payment.Status == PaymentStatus.Failed).ToString()
                    })
                ];
                return result;
            }
            case "payment-success":
            {
                var counts = await db.Payments.AsNoTracking()
                    .GroupBy(payment => payment.Status.ToString().ToLowerInvariant())
                    .Select(group => new { Status = group.Key, Count = group.Count() })
                    .ToListAsync(cancellationToken);
                var total = Math.Max(counts.Sum(item => item.Count), 1);
                List<List<string>> result =
                [
                    ["status", "count", "share_percent"],
                    ..counts.OrderBy(item => item.Status).Select(item => new List<string>
                    {
                        item.Status, item.Count.ToString(),
                        Math.Round(100.0 * item.Count / total, 2).ToString(CultureInfo.InvariantCulture)
                    })
                ];
                return result;
            }
            case "fraud":
            {
                var cases = await db.FraudCases.AsNoTracking().OrderBy(item => item.CreatedAt)
                    .ToListAsync(cancellationToken);
                List<List<string>> result =
                [
                    ["case_id", "payment_id", "score", "level", "status", "reasons", "created_at"],
                    ..cases.Select(item => new List<string>
                    {
                        item.Id.ToString(), item.PaymentId?.ToString() ?? "", item.Score.ToString(),
                        item.Level, item.Status, string.Join("|", JsonText.ParseStringArray(item.Reasons)),
                        item.CreatedAt.ToString("O")
                    })
                ];
                return result;
            }
            case "aml":
            {
                var alerts = await db.AmlAlerts.AsNoTracking().OrderBy(item => item.CreatedAt)
                    .ToListAsync(cancellationToken);
                List<List<string>> result =
                [
                    ["alert_id", "customer_id", "payment_id", "rule_code", "status", "created_at"],
                    ..alerts.Select(item => new List<string>
                    {
                        item.Id.ToString(), item.CustomerId.ToString(), item.PaymentId?.ToString() ?? "",
                        item.RuleCode, item.Status, item.CreatedAt.ToString("O")
                    })
                ];
                return result;
            }
            case "reconciliation":
            {
                var runs = await db.ReconciliationRuns.AsNoTracking()
                    .OrderByDescending(run => run.StartedAt).ToListAsync(cancellationToken);
                List<List<string>> result =
                [
                    ["run_id", "started_at", "finished_at", "matched", "amount_mismatch", "status_mismatch",
                        "missing_external", "missing_internal"],
                    ..runs.Select(run => new List<string>
                    {
                        run.Id.ToString(), run.StartedAt.ToString("O"),
                        run.FinishedAt?.ToString("O") ?? "", run.Matched.ToString(),
                        run.AmountMismatch.ToString(), run.StatusMismatch.ToString(),
                        run.MissingExternal.ToString(), run.MissingInternal.ToString()
                    })
                ];
                return result;
            }
            case "ledger":
            {
                var lines = await (from line in db.JournalLines.AsNoTracking()
                                   join entry in db.JournalEntries.AsNoTracking() on line.JournalEntryId equals entry.Id
                                   orderby entry.PostedAt
                                   select new
                                   {
                                       entry.BusinessReference, entry.Currency, entry.PostedAt, line.Side,
                                       line.AmountMinor
                                   })
                    .ToListAsync(cancellationToken);
                List<List<string>> result =
                [
                    ["business_reference", "currency", "posted_at", "side", "amount_minor"],
                    ..lines.Select(line => new List<string>
                    {
                        line.BusinessReference, line.Currency, line.PostedAt.ToString("O"),
                        line.Side, line.AmountMinor.ToString()
                    })
                ];
                return result;
            }
            case "settlement":
            {
                var batches = await db.SettlementBatches.AsNoTracking()
                    .OrderByDescending(batch => batch.CreatedAt).ToListAsync(cancellationToken);
                List<List<string>> result =
                [
                    ["batch_id", "business_date", "currency", "status", "payment_count", "total_minor",
                        "fee_minor", "net_minor", "processed_at"],
                    ..batches.Select(batch => new List<string>
                    {
                        batch.Id.ToString(),
                        batch.BusinessDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                        batch.Currency, batch.Status, batch.PaymentCount.ToString(),
                        batch.TotalMinor.ToString(), batch.FeeMinor.ToString(),
                        (batch.TotalMinor - batch.FeeMinor).ToString(),
                        batch.ProcessedAt?.ToString("O") ?? ""
                    })
                ];
                return result;
            }
            default:
                return null;
        }
    }

    /// <summary>
    /// One point per key per currency seen within the keys. Amounts in different currencies are
    /// never added together: each currency with activity in the window gets its own zero-filled
    /// series, ordered by key then currency (ordinal), so a chart can plot one currency at a time.
    /// </summary>
    private static IEnumerable<(string Key, string Currency, int Count, long VolumeMinor)> BucketByCurrency(
        IReadOnlyList<string> keys, IEnumerable<(string Key, string Currency, long AmountMinor)> items)
    {
        var wanted = keys.ToHashSet();
        var totals = items.Where(item => wanted.Contains(item.Key))
            .GroupBy(item => (item.Key, item.Currency))
            .ToDictionary(group => group.Key, group => (Count: group.Count(), Volume: group.Sum(item => item.AmountMinor)));
        var currencies = totals.Keys.Select(key => key.Currency).Distinct().Order(StringComparer.Ordinal).ToArray();
        foreach (var key in keys)
            foreach (var currency in currencies)
            {
                totals.TryGetValue((key, currency), out var total);
                yield return (key, currency, total.Count, total.Volume);
            }
    }

    private static string MonthKey(DateTimeOffset value) =>
        value.UtcDateTime.ToString("yyyy-MM", CultureInfo.InvariantCulture);

    private static string DayKey(DateTimeOffset value) =>
        value.UtcDateTime.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
}
