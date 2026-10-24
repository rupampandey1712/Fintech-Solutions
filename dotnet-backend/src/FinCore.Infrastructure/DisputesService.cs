using System.Text.Json;
using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public sealed record DisputeEvidenceView(string Note, string AddedBy, string AddedAt);

public sealed record PaymentDisputeView(Guid Id, Guid PaymentId, Guid CustomerId, string Status, string Reason,
    IReadOnlyList<DisputeEvidenceView> Evidence, Guid? DecidedBy, DateTimeOffset? DecidedAt,
    string? Resolution, DateTimeOffset CreatedAt);

public sealed record OpenDisputeCommand(string Reason);

public sealed record AddEvidenceCommand(string Note);

public sealed record DecideDisputeCommand(string Outcome, string? Resolution);

/// <summary>
/// Chargeback simulation (architecture spec, section 61): transaction -> dispute ->
/// investigation (evidence) -> decision (won/lost). A customer opens a dispute on their own
/// completed payment; staff add evidence (which starts the review) and decide. A lost decision
/// upholds the chargeback and mirrors the payment's journals exactly like an operational
/// reversal, so the funds return to the payer. Everything is audited and announced as events.
/// </summary>
public sealed class DisputesService(FinCoreDbContext db)
{
    public static readonly string[] StaffRoles = ["admin", "fraud_analyst", "finance_user"];
    public static readonly string[] DecisionRoles = ["admin", "fraud_analyst"];
    public const int EvidenceLimit = 20;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public async Task<PaymentDisputeView> OpenAsync(Guid customerId, Guid paymentId, string reason,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(reason) || reason.Length is < 3 or > 200)
            throw new PaymentException("validation_error", "A reason of 3 to 200 characters is required.", 422);
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var payment = await db.Payments.AsNoTracking().SingleOrDefaultAsync(item =>
            item.Id == paymentId && item.CustomerId == customerId, cancellationToken)
            ?? throw new PaymentException("not_found", "Payment not found.", 404);
        if (payment.Status != PaymentStatus.Completed)
            throw new PaymentException("payment_not_disputable", "Only completed payments can be disputed.", 409);
        if (await db.PaymentDisputes.AsNoTracking().AnyAsync(item => item.PaymentId == paymentId, cancellationToken))
            throw new PaymentException("payment_already_disputed", "This payment already has a dispute.", 409);

        var dispute = new PaymentDisputeRecord
        {
            PaymentId = payment.Id, CustomerId = customerId, Status = "open", Reason = reason,
            Evidence = "[]", CreatedAt = DateTimeOffset.UtcNow
        };
        db.PaymentDisputes.Add(dispute);
        db.OutboxEvents.Add(DisputeEvent("payment.dispute.opened.v1", dispute, payment,
            new { reason }));
        await AuditService.RecordAuditAsync(db, customerId, "payment.dispute.opened", "dispute", dispute.Id,
            newValue: JsonSerializer.Serialize(new { paymentId = payment.Id, reason }, JsonOptions),
            cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Map(dispute);
    }

    /// <summary>Visible to the payer and to staff, like the payment itself; null means not found.</summary>
    public async Task<PaymentDisputeView?> GetAsync(Guid callerId, bool isStaff, Guid paymentId,
        CancellationToken cancellationToken)
    {
        var payment = await db.Payments.AsNoTracking().SingleOrDefaultAsync(item => item.Id == paymentId, cancellationToken);
        if (payment is null || (payment.CustomerId != callerId && !isStaff)) return null;
        var dispute = await db.PaymentDisputes.AsNoTracking()
            .SingleOrDefaultAsync(item => item.PaymentId == paymentId, cancellationToken);
        return dispute is null ? null : Map(dispute);
    }

    public async Task<IReadOnlyList<PaymentDisputeView>> ListAsync(string? status, int limit,
        CancellationToken cancellationToken)
    {
        var query = db.PaymentDisputes.AsNoTracking();
        if (!string.IsNullOrWhiteSpace(status)) query = query.Where(item => item.Status == status);
        var rows = await query.OrderByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id)
            .Take(limit).ToListAsync(cancellationToken);
        return rows.Select(Map).ToList();
    }

    public async Task<PaymentDisputeView> AddEvidenceAsync(Guid actorId, Guid disputeId, string note,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(note) || note.Length is < 3 or > 300)
            throw new PaymentException("validation_error", "An evidence note of 3 to 300 characters is required.", 422);
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var dispute = await LockDisputeAsync(disputeId, cancellationToken);
        if (dispute.Status is "won" or "lost")
            throw new PaymentException("dispute_already_decided", "This dispute has already been decided.", 409);
        var entries = ParseEvidence(dispute.Evidence);
        if (entries.Count >= EvidenceLimit)
            throw new PaymentException("evidence_limit_reached", "The evidence limit for this dispute was reached.", 422);
        entries.Add(new DisputeEvidenceView(note, actorId.ToString("D"), DateTimeOffset.UtcNow.ToString("O")));
        dispute.Evidence = JsonSerializer.Serialize(entries, JsonOptions);
        var previousStatus = dispute.Status;
        if (dispute.Status == "open") dispute.Status = "under_review";
        await AuditService.RecordAuditAsync(db, actorId, "dispute.evidence_added", "dispute", dispute.Id,
            oldValue: JsonSerializer.Serialize(new { status = previousStatus }, JsonOptions),
            newValue: JsonSerializer.Serialize(new { status = dispute.Status, evidenceCount = entries.Count }, JsonOptions),
            cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Map(dispute);
    }

    public async Task<PaymentDisputeView> DecideAsync(Guid actorId, Guid disputeId, string outcome,
        string? resolution, CancellationToken cancellationToken)
    {
        if (outcome is not ("won" or "lost"))
            throw new PaymentException("validation_error", "Outcome must be 'won' or 'lost'.", 422);
        db.ChangeTracker.Clear();
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var dispute = await LockDisputeAsync(disputeId, cancellationToken);
        if (dispute.Status is "won" or "lost")
            throw new PaymentException("dispute_already_decided", "This dispute has already been decided.", 409);
        var payment = await db.Payments.AsNoTracking().SingleOrDefaultAsync(item => item.Id == dispute.PaymentId,
            cancellationToken)
            ?? throw new PaymentException("payment_missing", "Payment no longer exists.", 409);

        dispute.Status = outcome;
        dispute.DecidedBy = actorId;
        dispute.DecidedAt = DateTimeOffset.UtcNow;
        dispute.Resolution = resolution ?? (outcome == "lost"
            ? "Chargeback upheld: funds returned to the payer"
            : "Chargeback rejected: original transfer stands");

        var reversalIds = new List<Guid>();
        if (outcome == "lost")
        {
            var (locked, _) = await LockPaymentAsync(payment.Id, cancellationToken);
            if (locked.Status != PaymentStatus.Completed)
                throw new PaymentException("payment_not_reversible", "This payment can no longer be charged back.", 409);
            // A direct payment has one journal; a released hold has two. Mirroring all of them
            // restores every ledger, exactly like an operational reversal.
            var prefix = $"payment:{locked.Id}:";
            var journalIds = await db.JournalEntries.AsNoTracking()
                .Where(entry => (entry.BusinessReference == $"payment:{locked.Id}" || entry.BusinessReference.StartsWith(prefix))
                    && entry.ReversalOfId == null)
                .OrderByDescending(entry => entry.PostedAt).Select(entry => entry.Id)
                .ToListAsync(cancellationToken);
            foreach (var journalId in journalIds)
                reversalIds.Add((await LedgerPostings.ReverseJournalAsync(db, journalId, cancellationToken)).Id);
            locked.Status = PaymentStateMachine.Transition(PaymentStatus.Completed, PaymentStatus.Reversed);
        }

        db.OutboxEvents.Add(DisputeEvent("payment.dispute.decided.v1", dispute, payment,
            new { outcome, reversalJournalIds = reversalIds }));
        await AuditService.RecordAuditAsync(db, actorId, "payment.dispute.decided", "dispute", dispute.Id,
            newValue: JsonSerializer.Serialize(new { outcome, resolution = dispute.Resolution }, JsonOptions),
            cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return Map(dispute);
    }

    private async Task<PaymentDisputeRecord> LockDisputeAsync(Guid disputeId, CancellationToken cancellationToken) =>
        (await db.PaymentDisputes.FromSql($"SELECT * FROM payment_disputes WHERE id = {disputeId} FOR UPDATE")
            .ToListAsync(cancellationToken)).SingleOrDefault()
        ?? throw new PaymentException("not_found", "Dispute not found.", 404);

    /// <summary>Same locks, same order, as payments and refunds (see CorrectionsService).</summary>
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

    private static List<DisputeEvidenceView> ParseEvidence(string? evidence) =>
        string.IsNullOrWhiteSpace(evidence)
            ? []
            : JsonSerializer.Deserialize<List<DisputeEvidenceView>>(evidence, JsonOptions) ?? [];

    private static OutboxEventRecord DisputeEvent(string eventType, PaymentDisputeRecord dispute,
        PaymentRecord payment, object extra)
    {
        var payload = new Dictionary<string, object?>
        {
            ["disputeId"] = dispute.Id,
            ["paymentId"] = payment.Id,
            ["customerId"] = dispute.CustomerId,
            ["status"] = dispute.Status,
            ["amountMinor"] = payment.AmountMinor,
            ["currency"] = payment.Currency
        };
        foreach (var (key, value) in JsonDocument.Parse(JsonSerializer.Serialize(extra, JsonOptions)).RootElement.EnumerateObject()
                     .Select(property => (property.Name, (object?)property.Value.Clone())))
            payload[key] = value;
        return new OutboxEventRecord
        {
            AggregateId = dispute.Id, EventType = eventType, SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(payload, JsonOptions),
            CreatedAt = DateTimeOffset.UtcNow
        };
    }

    private static PaymentDisputeView Map(PaymentDisputeRecord dispute) => new(
        dispute.Id, dispute.PaymentId, dispute.CustomerId, dispute.Status, dispute.Reason,
        ParseEvidence(dispute.Evidence), dispute.DecidedBy, dispute.DecidedAt, dispute.Resolution, dispute.CreatedAt);
}
