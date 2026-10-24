using System.Globalization;
using System.Text;
using System.Text.Json;
using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;

namespace FinCore.Infrastructure;

public sealed record SettlementItemView(Guid PaymentId, long AmountMinor, long FeeMinor);

public sealed record SettlementBatchView(Guid Id, string BusinessDate, string Currency, string Status,
    long TotalMinor, long FeeMinor, long NetMinor, int PaymentCount, string? FailureReason,
    DateTimeOffset? ProcessedAt, DateTimeOffset CreatedAt);

public sealed record SettlementBatchDetailView(Guid Id, string BusinessDate, string Currency, string Status,
    long TotalMinor, long FeeMinor, long NetMinor, int PaymentCount, string? FailureReason,
    DateTimeOffset? ProcessedAt, DateTimeOffset CreatedAt, IReadOnlyList<SettlementItemView> Items);

public sealed record CreateSettlementBatchCommand(string BusinessDate, string Currency);

/// <summary>
/// Settlement batches (architecture spec, section 58): a business day's provider-settled
/// payments grouped for payout. Processing moves pending -> completed (or failed), computes
/// the net amount (gross minus a deterministic per-payment fee), renders the payout file, and
/// marks every included payment so it can never be settled twice.
/// </summary>
public sealed class SettlementsService(FinCoreDbContext db, IConfiguration configuration)
{
    public static readonly string[] StaffRoles = ["admin", "finance_user", "operations_user"];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>Deterministic settlement fee in minor units (configurable basis points).</summary>
    private long FeeFor(long amountMinor)
    {
        var bps = configuration.GetValue<int?>("SETTLEMENT_FEE_BPS") ?? 25;
        return amountMinor * bps / 10_000;
    }

    public async Task<SettlementBatchDetailView> CreateAsync(Guid actorId, string businessDate, string currency,
        CancellationToken cancellationToken)
    {
        if (!DateOnly.TryParseExact(businessDate, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var date))
            throw new PaymentException("validation_error", "businessDate must be formatted yyyy-MM-dd.", 422);
        if (currency.Length != 3 || currency.Any(c => c is < 'A' or > 'Z'))
            throw new PaymentException("validation_error", "Currency must be a 3-letter code.", 422);
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);

        var start = new DateTimeOffset(date, TimeOnly.MinValue, TimeSpan.Zero);
        var end = new DateTimeOffset(date, TimeOnly.MaxValue, TimeSpan.Zero);
        // Only payments that actually went through the provider are settleable; a payment
        // already in a batch is excluded by the unique index on settlement_batch_items.
        var settledPaymentIds = db.SettlementBatchItems.AsNoTracking().Select(item => item.PaymentId);
        var payments = await db.Payments.AsNoTracking()
            .Where(payment => payment.Status == PaymentStatus.Completed
                && payment.Currency == currency
                && payment.ProviderReference != null
                && payment.CreatedAt >= start && payment.CreatedAt <= end
                && !settledPaymentIds.Contains(payment.Id))
            .OrderBy(payment => payment.CreatedAt)
            .ToListAsync(cancellationToken);
        if (payments.Count == 0)
            throw new PaymentException("no_settleable_payments_for_date",
                "No unsettled provider-settled payments for this date.", 422);

        var batch = new SettlementBatchRecord
        {
            BusinessDate = start, Currency = currency, Status = "pending", CreatedBy = actorId,
            CreatedAt = DateTimeOffset.UtcNow
        };
        db.SettlementBatches.Add(batch);
        var items = new List<SettlementBatchItemRecord>(payments.Count);
        long total = 0, fees = 0;
        foreach (var payment in payments)
        {
            var fee = FeeFor(payment.AmountMinor);
            items.Add(new SettlementBatchItemRecord
            {
                BatchId = batch.Id, PaymentId = payment.Id, AmountMinor = payment.AmountMinor,
                FeeMinor = fee, CreatedAt = DateTimeOffset.UtcNow
            });
            total += payment.AmountMinor;
            fees += fee;
        }
        db.SettlementBatchItems.AddRange(items);
        batch.TotalMinor = total;
        batch.FeeMinor = fees;
        batch.PaymentCount = items.Count;
        await AuditService.RecordAuditAsync(db, actorId, "settlement.batch_created", "settlement_batch",
            batch.Id, newValue: JsonSerializer.Serialize(new
            {
                businessDate, currency, paymentCount = items.Count, totalMinor = total
            }, JsonOptions), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Detail(batch, items);
    }

    public async Task<SettlementBatchDetailView> ProcessAsync(Guid actorId, Guid batchId,
        CancellationToken cancellationToken)
    {
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var batch = (await db.SettlementBatches.FromSql(
                $"SELECT * FROM settlement_batches WHERE id = {batchId} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault()
            ?? throw new PaymentException("not_found", "Settlement batch not found.", 404);
        if (batch.Status != "pending")
            throw new PaymentException("settlement_batch_not_pending", "Only pending batches can be processed.", 409);
        batch.Status = "processing";
        var items = await db.SettlementBatchItems.AsNoTracking()
            .Where(item => item.BatchId == batch.Id).OrderBy(item => item.CreatedAt)
            .ToListAsync(cancellationToken);
        if (items.Count == 0)
        {
            // Defensive: an empty batch must fail loudly rather than settle nothing silently.
            batch.Status = "failed";
            batch.FailureReason = "Settlement batch has no payments";
        }
        else
        {
            batch.FileContent = RenderFile(batch, items);
            batch.Status = "completed";
            batch.ProcessedAt = DateTimeOffset.UtcNow;
        }
        db.OutboxEvents.Add(SettlementEvent($"settlement.{batch.Status}.v1", batch));
        await AuditService.RecordAuditAsync(db, actorId, $"settlement.batch_{batch.Status}",
            "settlement_batch", batch.Id,
            oldValue: JsonSerializer.Serialize(new { status = "pending" }, JsonOptions),
            newValue: JsonSerializer.Serialize(new { status = batch.Status, netMinor = batch.TotalMinor - batch.FeeMinor },
                JsonOptions), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Detail(batch, items);
    }

    public async Task<IReadOnlyList<SettlementBatchView>> ListAsync(int limit, CancellationToken cancellationToken)
    {
        var rows = await db.SettlementBatches.AsNoTracking()
            .OrderByDescending(batch => batch.CreatedAt).ThenByDescending(batch => batch.Id)
            .Take(limit).ToListAsync(cancellationToken);
        return rows.Select(View).ToList();
    }

    public async Task<SettlementBatchDetailView?> GetAsync(Guid batchId, CancellationToken cancellationToken)
    {
        var batch = await db.SettlementBatches.AsNoTracking().SingleOrDefaultAsync(item => item.Id == batchId,
            cancellationToken);
        if (batch is null) return null;
        var items = await db.SettlementBatchItems.AsNoTracking()
            .Where(item => item.BatchId == batch.Id).OrderBy(item => item.CreatedAt)
            .ToListAsync(cancellationToken);
        return Detail(batch, items);
    }

    public async Task<string?> GetFileAsync(Guid batchId, CancellationToken cancellationToken)
    {
        var batch = await db.SettlementBatches.AsNoTracking().SingleOrDefaultAsync(item => item.Id == batchId,
            cancellationToken);
        return batch is { Status: "completed" } && batch.FileContent is not null ? batch.FileContent : null;
    }

    private static string RenderFile(SettlementBatchRecord batch,
        IReadOnlyList<SettlementBatchItemRecord> items)
    {
        var builder = new StringBuilder();
        builder.AppendLine("batch_id,business_date,currency,payment_id,amount_minor,fee_minor,net_amount_minor");
        foreach (var item in items)
        {
            builder.Append(batch.Id.ToString("D")).Append(',')
                .Append(batch.BusinessDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).Append(',')
                .Append(batch.Currency).Append(',')
                .Append(item.PaymentId.ToString("D")).Append(',')
                .Append(item.AmountMinor.ToString(CultureInfo.InvariantCulture)).Append(',')
                .Append(item.FeeMinor.ToString(CultureInfo.InvariantCulture)).Append(',')
                .Append((item.AmountMinor - item.FeeMinor).ToString(CultureInfo.InvariantCulture))
                .Append('\n');
        }
        return builder.ToString();
    }

    private static SettlementBatchView View(SettlementBatchRecord batch) => new(
        batch.Id, batch.BusinessDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), batch.Currency,
        batch.Status, batch.TotalMinor, batch.FeeMinor, batch.TotalMinor - batch.FeeMinor,
        batch.PaymentCount, batch.FailureReason, batch.ProcessedAt, batch.CreatedAt);

    private static SettlementBatchDetailView Detail(SettlementBatchRecord batch,
        IEnumerable<SettlementBatchItemRecord> items)
    {
        var view = View(batch);
        return new SettlementBatchDetailView(view.Id, view.BusinessDate, view.Currency, view.Status,
            view.TotalMinor, view.FeeMinor, view.NetMinor, view.PaymentCount, view.FailureReason,
            view.ProcessedAt, view.CreatedAt,
            items.Select(item => new SettlementItemView(item.PaymentId, item.AmountMinor, item.FeeMinor)).ToList());
    }

    private static OutboxEventRecord SettlementEvent(string eventType, SettlementBatchRecord batch)
    {
        var payload = new
        {
            settlementBatchId = batch.Id,
            businessDate = batch.BusinessDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            currency = batch.Currency,
            status = batch.Status,
            totalMinor = batch.TotalMinor,
            netMinor = batch.TotalMinor - batch.FeeMinor,
            paymentCount = batch.PaymentCount,
            customerId = batch.CreatedBy
        };
        return new OutboxEventRecord
        {
            AggregateId = batch.Id, EventType = eventType, SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(payload, JsonOptions),
            CreatedAt = DateTimeOffset.UtcNow
        };
    }
}
