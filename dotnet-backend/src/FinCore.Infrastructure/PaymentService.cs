using System.Data;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using FinCore.Application;
using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

// The cache is optional so tests and tools can construct the service with a DbContext alone.
public sealed class PaymentService(FinCoreDbContext db, FinCoreCache? cache = null) : IPaymentService
{
    private const string Operation = "payment:create";
    private const long UnusualAmountThreshold = 5_000_000;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.CamelCase) }
    };

    public async Task<PaymentView> CreateAsync(Guid customerId, CreatePaymentCommand command,
        string idempotencyKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(idempotencyKey) || idempotencyKey.Length is < 8 or > 128
            || idempotencyKey.Any(char.IsWhiteSpace))
            throw new PaymentException("invalid_idempotency_key", "Idempotency-Key must be 8 to 128 non-space characters.", 422);
        if (command.AmountMinor <= 0 || string.IsNullOrWhiteSpace(command.Currency)
            || command.Currency.Length != 3 || command.Currency.Any(c => c is < 'A' or > 'Z')
            || command.Reference?.Length > 140)
            throw new PaymentException("validation_error", "Payment request is invalid.", 422);
        if (command.AmountMinor > long.MaxValue - 200_000_000)
            throw new PaymentException("validation_error", "Amount exceeds supported configured transfer limits.", 422);

        var requestHash = CreateRequestHash(command);

        db.ChangeTracker.Clear();

        await using var transaction = await db.Database.BeginTransactionAsync(IsolationLevel.ReadCommitted, cancellationToken);
        var existing = await db.IdempotencyRecords.AsNoTracking().SingleOrDefaultAsync(
            record => record.CustomerId == customerId && record.Operation == Operation && record.Key == idempotencyKey,
            cancellationToken);
        if (existing is not null)
        {
            if (existing.RequestHash != requestHash)
                throw new PaymentException("idempotency_key_reused", "Idempotency key was used with a different request.", 409);
            if (string.IsNullOrWhiteSpace(existing.ResponseJson))
                throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409);
            var replay = JsonSerializer.Deserialize<PaymentView>(existing.ResponseJson, JsonOptions)
                ?? throw new PaymentException("idempotency_response_missing", "Stored idempotency response is invalid.", 409);
            return replay;
        }

        // Persisting this claim before business validation lets PostgreSQL's unique
        // index serialize concurrent requests using the same customer-scoped key.
        var claim = new IdempotencyRecord
        {
            CustomerId = customerId,
            Operation = Operation,
            Key = idempotencyKey,
            RequestHash = requestHash
        };
        db.IdempotencyRecords.Add(claim);
        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception) when (exception.InnerException is Npgsql.PostgresException
            { SqlState: Npgsql.PostgresErrorCodes.UniqueViolation })
        {
            // A concurrent request with the same key claimed it between our read and insert.
            throw new PaymentException("request_in_progress", "An identical request is still being processed.", 409);
        }

        try
        {
            var source = await db.Accounts.SingleOrDefaultAsync(account =>
                account.Id == command.SourceAccountId && account.CustomerId == customerId, cancellationToken);
            if (source is null) throw new PaymentException("not_found", "Source account not found.", 404);
            if (source.Status != "active") throw new PaymentException("account_not_active", "Source account is not active.", 409);
            if (source.Currency != command.Currency) throw new PaymentException("currency_mismatch", "Payment currency must match source account.", 422);

            var beneficiary = await db.Beneficiaries.AsNoTracking().SingleOrDefaultAsync(item =>
                item.Id == command.BeneficiaryId && item.CustomerId == customerId, cancellationToken);
            if (beneficiary is null) throw new PaymentException("not_found", "Beneficiary not found.", 404);
            if (beneficiary.Status != "active") throw new PaymentException("beneficiary_not_active", "Beneficiary is not active.", 409);

            var destination = await db.Accounts.AsNoTracking().SingleOrDefaultAsync(account =>
                account.Id == beneficiary.DestinationAccountId && account.Status == "active", cancellationToken);
            if (destination is null) throw new PaymentException("destination_unavailable", "Beneficiary destination is unavailable.", 409);
            if (destination.Currency != command.Currency) throw new PaymentException("currency_mismatch", "Cross-currency transfers are not supported.", 422);
            if (destination.Id == source.Id) throw new PaymentException("self_transfer", "Source and destination accounts must differ.", 422);

            // Configured caps come from the cache; the running totals compared with them below are
            // always recomputed from the database under the account locks.
            var limit = await PaymentConfiguration.AccountLimitsAsync(db, cache, source.Id, cancellationToken);
            if (limit is null) throw new PaymentException("limits_not_configured", "Source account has no configured limits.", 409);
            if (command.AmountMinor > limit.PerTransactionMinor)
                throw new PaymentException("per_transaction_limit_exceeded", "Per-transaction limit exceeded.", 422);

            // Advisory locks make balances and rolling limits serial across distinct
            // idempotency keys. Sorting both account UUIDs prevents opposite transfer deadlocks.
            await LedgerPostings.LockAccountsAsync(db, [source.Id, destination.Id], cancellationToken);

            var ledgerRows = await db.LedgerAccounts.AsNoTracking()
                .Where(item => item.AccountId == source.Id || item.AccountId == destination.Id)
                .ToDictionaryAsync(item => item.AccountId ?? Guid.Empty, cancellationToken);
            if (!ledgerRows.TryGetValue(source.Id, out var sourceLedger)
                || !ledgerRows.TryGetValue(destination.Id, out var destinationLedger))
                throw new PaymentException("ledger_not_configured", "Account ledger is not configured.", 409);

            var balance = await (from line in db.JournalLines.AsNoTracking()
                                 where line.LedgerAccountId == sourceLedger.Id
                                 select line.Side == "credit" ? line.AmountMinor : -line.AmountMinor)
                .SumAsync(cancellationToken);
            if (balance < command.AmountMinor)
                throw new PaymentException("insufficient_funds", "Insufficient available balance.", 422);

            var now = DateTimeOffset.UtcNow;
            var totals = await db.Payments.AsNoTracking().Where(payment =>
                    payment.SourceAccountId == source.Id
                    && (payment.Status == PaymentStatus.Pending || payment.Status == PaymentStatus.Processing || payment.Status == PaymentStatus.Completed))
                .GroupBy(_ => 1)
                .Select(group => new
                {
                    Daily = group.Where(payment => payment.CreatedAt >= now.AddDays(-1)).Sum(payment => (long?)payment.AmountMinor) ?? 0,
                    Weekly = group.Where(payment => payment.CreatedAt >= now.AddDays(-7)).Sum(payment => (long?)payment.AmountMinor) ?? 0,
                    Monthly = group.Where(payment => payment.CreatedAt >= now.AddDays(-30)).Sum(payment => (long?)payment.AmountMinor) ?? 0
                }).SingleOrDefaultAsync(cancellationToken);
            if (command.AmountMinor > limit.DailyMinor - (totals?.Daily ?? 0))
                throw new PaymentException("daily_limit_exceeded", "Daily transfer limit exceeded.", 422);
            if (command.AmountMinor > limit.WeeklyMinor - (totals?.Weekly ?? 0))
                throw new PaymentException("weekly_limit_exceeded", "Weekly transfer limit exceeded.", 422);
            if (command.AmountMinor > limit.MonthlyMinor - (totals?.Monthly ?? 0))
                throw new PaymentException("monthly_limit_exceeded", "Monthly transfer limit exceeded.", 422);

            var payment = new PaymentRecord
            {
                CustomerId = customerId,
                SourceAccountId = source.Id,
                BeneficiaryId = beneficiary.Id,
                AmountMinor = command.AmountMinor,
                Currency = command.Currency,
                Status = PaymentStatus.Initiated,
                Reference = command.Reference,
                CreatedAt = now
            };

            var fraudRules = (await PaymentConfiguration.FraudRulesAsync(db, cache, cancellationToken))
                .Select(rule => new FraudRuleDefinition(rule.Code, rule.ThresholdMinor, rule.Weight, rule.Enabled))
                .ToArray();
            var fraudContext = await BuildFraudContextAsync(customerId, source, beneficiary, command.AmountMinor, now, cancellationToken);
            var evaluation = FraudEvaluation.Evaluate(fraudRules, fraudContext);
            payment.RiskScore = evaluation.Score;
            payment.RiskLevel = evaluation.Level;
            payment.RiskDecision = evaluation.Decision;
            payment.RiskReasons = JsonSerializer.Serialize(evaluation.Reasons, JsonOptions);

            if (evaluation.Decision is "block" or "review")
                FinCoreTelemetry.FraudDetectedTotal.Add(1, new KeyValuePair<string, object?>("decision", evaluation.Decision));
            if (evaluation.Decision == "block")
                throw new PaymentException("fraud_blocked", "Payment blocked by fraud rules", 422);

            // A payment that needs fraud review is held: the funds leave the source account for
            // the payment-holds clearing ledger, so they cannot be spent twice, and only reach the
            // beneficiary when a reviewer releases them (PaymentHoldService). Direct payments are
            // submitted to the provider simulator, which records its own outcome; money reaches the
            // beneficiary only once the provider accepts the transfer (a timeout holds it instead,
            // until reconciliation settles the payment).
            var held = evaluation.Decision == "review";
            // Tracked before anything below can call SaveChanges (the review branch's audit does):
            // the fraud case and AML alerts reference this row, so it must be inserted first.
            db.Payments.Add(payment);
            db.OutboxEvents.Add(PaymentEvent("payment.initiated.v1", payment, now));
            payment.Status = PaymentStateMachine.Transition(PaymentStatus.Initiated, PaymentStatus.Pending);

            if (held)
            {
                var holdsLedger = await LedgerPostings.SystemLedgerAsync(db, LedgerPostings.PaymentHolds,
                    command.Currency, cancellationToken);
                LedgerPostings.AddTransfer(db, $"payment:{payment.Id}", command.Currency, sourceLedger.Id,
                    holdsLedger.Id, command.AmountMinor, now);
                var fraudCase = new FraudCaseRecord
                {
                    CustomerId = customerId,
                    PaymentId = payment.Id,
                    Score = evaluation.Score,
                    Level = evaluation.Level,
                    Reasons = JsonSerializer.Serialize(evaluation.Reasons, JsonOptions),
                    CreatedAt = now
                };
                db.FraudCases.Add(fraudCase);
                db.OutboxEvents.Add(new OutboxEventRecord
                {
                    AggregateId = payment.Id,
                    EventType = "fraud.case.opened.v1",
                    SchemaVersion = 1,
                    PayloadJson = JsonSerializer.Serialize(new
                    {
                        caseId = fraudCase.Id,
                        customerId,
                        paymentId = payment.Id,
                        score = evaluation.Score,
                        level = evaluation.Level,
                        reasons = evaluation.Reasons,
                        createdAt = now
                    }, JsonOptions),
                    CreatedAt = now
                });
                db.OutboxEvents.Add(new OutboxEventRecord
                {
                    AggregateId = payment.Id,
                    EventType = "payment.flagged.v1",
                    SchemaVersion = 1,
                    PayloadJson = JsonSerializer.Serialize(new
                    {
                        paymentId = payment.Id,
                        customerId,
                        amountMinor = command.AmountMinor,
                        currency = command.Currency,
                        score = evaluation.Score,
                        level = evaluation.Level,
                        reasons = evaluation.Reasons,
                        createdAt = now
                    }, JsonOptions),
                    CreatedAt = now
                });
                await AuditService.RecordAuditAsync(db, customerId, "payment.flagged", "payment", payment.Id,
                    oldValue: null,
                    newValue: JsonSerializer.Serialize(new { score = evaluation.Score, level = evaluation.Level }, JsonOptions),
                    cancellationToken: cancellationToken);
            }
            else
            {
                // The provider hop happens before any internal posting: a declined or partially
                // failed transfer must never move money. A timeout leaves the payment in
                // "processing" — the outcome is genuinely unknown (the provider may have
                // completed it). Its funds are reserved in the payment-holds ledger so they
                // cannot be spent again, and reconciliation settles them from the provider's record.
                payment.Status = PaymentStateMachine.Transition(PaymentStatus.Pending, PaymentStatus.Processing);
                var result = await PaymentProviderSimulator.ProcessPaymentAsync(db, payment, cancellationToken);
                payment.ProviderReference = result.ProviderReference;
                payment.ProviderStatus = result.ProviderStatus;
                db.OutboxEvents.Add(PaymentEvent("payment.processed.v1", payment, now,
                    providerMode: result.Mode, providerStatus: result.ProviderStatus,
                    accepted: result.Accepted));
                if (!result.Accepted)
                {
                    payment.Status = PaymentStateMachine.Transition(PaymentStatus.Processing, PaymentStatus.Failed);
                    db.OutboxEvents.Add(PaymentEvent("payment.failed.v1", payment, now, providerMode: result.Mode));
                }
                else if (result.Mode == PaymentProviderSimulator.ModeTimeout)
                {
                    var holdsLedger = await LedgerPostings.SystemLedgerAsync(db, LedgerPostings.PaymentHolds,
                        command.Currency, cancellationToken);
                    LedgerPostings.AddTransfer(db, $"payment:{payment.Id}", command.Currency, sourceLedger.Id,
                        holdsLedger.Id, command.AmountMinor, now);
                }
                else
                {
                    payment.Status = PaymentStateMachine.Transition(PaymentStatus.Processing, PaymentStatus.Completed);
                    LedgerPostings.AddTransfer(db, $"payment:{payment.Id}", command.Currency, sourceLedger.Id,
                        destinationLedger.Id, command.AmountMinor, now);
                    db.OutboxEvents.Add(PaymentEvent("ledger.posted.v1", payment, now,
                        journalReference: $"payment:{payment.Id}"));
                    db.OutboxEvents.Add(PaymentEvent("transaction.created.v1", payment, now,
                        journalReference: $"payment:{payment.Id}"));
                    db.OutboxEvents.Add(PaymentHoldService.CompletedEvent(payment, now));
                    // Ask the operations side to reconcile this transfer against the provider.
                    db.OutboxEvents.Add(PaymentEvent("reconciliation.required.v1", payment, now));
                }
            }

            var unusualThreshold = fraudRules
                .Where(rule => rule.Code == "large_amount" && rule.ThresholdMinor is > 0)
                .Select(rule => rule.ThresholdMinor!.Value)
                .DefaultIfEmpty(UnusualAmountThreshold).First();
            var amlRuleCodes = AmlRules.Evaluate(fraudContext.PaymentsLastHour, fraudContext.PaymentsLast10Min,
                command.AmountMinor, unusualThreshold, fraudContext.DistinctBeneficiaries24h);
            foreach (var ruleCode in amlRuleCodes)
            {
                var alert = new AmlAlertRecord
                {
                    CustomerId = customerId,
                    PaymentId = payment.Id,
                    RuleCode = ruleCode,
                    Details = JsonSerializer.Serialize(new
                    {
                        amountMinor = command.AmountMinor,
                        paymentsLast10Min = fraudContext.PaymentsLast10Min,
                        paymentsLastHour = fraudContext.PaymentsLastHour,
                        distinctBeneficiaries24h = fraudContext.DistinctBeneficiaries24h
                    }, JsonOptions),
                    CreatedAt = now
                };
                db.AmlAlerts.Add(alert);
                db.OutboxEvents.Add(new OutboxEventRecord
                {
                    AggregateId = payment.Id,
                    EventType = "aml.alert.opened.v1",
                    SchemaVersion = 1,
                    PayloadJson = JsonSerializer.Serialize(new
                    {
                        alertId = alert.Id,
                        customerId,
                        paymentId = payment.Id,
                        ruleCode = ruleCode,
                        createdAt = now
                    }, JsonOptions),
                    CreatedAt = now
                });
            }

            if (payment.Status == PaymentStatus.Failed)
            {
                // The provider declined the transfer: an outcome worth its own audit trail, even
                // though nothing was posted and the payment cannot be found in the ledger.
                await AuditService.RecordAuditAsync(db, customerId, "payment.failed", "payment", payment.Id,
                    newValue: JsonSerializer.Serialize(new
                    {
                        providerStatus = payment.ProviderStatus
                    }, JsonOptions),
                    cancellationToken: cancellationToken);
            }

            var response = new PaymentView(payment.Id, payment.Status.ToString().ToLowerInvariant(), command.AmountMinor,
                command.Currency, now, payment.Reference, payment.ProviderReference, payment.ProviderStatus,
                new RiskView(evaluation.Score, evaluation.Level, evaluation.Decision, evaluation.Reasons));
            claim.ResponseJson = JsonSerializer.Serialize(response, JsonOptions);

            await db.SaveChangesAsync(cancellationToken);
            await AuditService.RecordAuditAsync(db, customerId, "payment.created", "payment", payment.Id,
                cancellationToken: cancellationToken);
            await transaction.CommitAsync(cancellationToken);
            return response;
        }
        catch
        {
            await transaction.RollbackAsync(cancellationToken);
            throw;
        }
    }

    public async Task<PaymentView?> GetAsync(Guid customerId, Guid paymentId, CancellationToken cancellationToken)
    {
        var payment = await db.Payments.AsNoTracking().SingleOrDefaultAsync(
            item => item.Id == paymentId && item.CustomerId == customerId, cancellationToken);
        return payment is null ? null : Map(payment);
    }

    public async Task<PaymentPage> ListAsync(Guid customerId, string? status, int limit, string? cursor,
        CancellationToken cancellationToken)
    {
        PaymentStatus? statusFilter = null;
        if (!string.IsNullOrWhiteSpace(status))
        {
            if (!Enum.TryParse<PaymentStatus>(status, ignoreCase: true, out var parsed))
                throw new PaymentException("validation_error", "Status filter is invalid.", 422);
            statusFilter = parsed;
        }
        DateTimeOffset? cursorCreatedAt = null;
        Guid? cursorId = null;
        if (cursor is not null)
        {
            if (!CursorCodec.TryDecode(cursor, out var createdAt, out var id))
                throw new PaymentException("invalid_cursor", "Cursor is invalid.", 422);
            cursorCreatedAt = createdAt;
            cursorId = id;
        }

        var query = db.Payments.AsNoTracking().Where(payment => payment.CustomerId == customerId);
        if (statusFilter is not null)
            query = query.Where(payment => payment.Status == statusFilter);
        if (cursorCreatedAt is not null)
            query = query.Where(payment =>
                payment.CreatedAt < cursorCreatedAt || (payment.CreatedAt == cursorCreatedAt && payment.Id < cursorId));

        var rows = await query
            .OrderByDescending(payment => payment.CreatedAt).ThenByDescending(payment => payment.Id)
            .Take(limit + 1).ToListAsync(cancellationToken);

        string? nextCursor = null;
        if (rows.Count > limit)
        {
            rows = rows.Take(limit).ToList();
            var last = rows[^1];
            nextCursor = CursorCodec.Encode(last.CreatedAt, last.Id);
        }
        return new PaymentPage(rows.Select(Map).ToArray(), nextCursor);
    }

    private async Task<FraudRuleContext> BuildFraudContextAsync(Guid customerId, CustomerAccountRecord source,
        BeneficiaryRecord beneficiary, long amountMinor, DateTimeOffset now, CancellationToken cancellationToken)
    {
        var since10Min = now.AddMinutes(-10);
        var since1Hour = now.AddHours(-1);
        var since24h = now.AddHours(-24);
        var activeStatuses = new[] { PaymentStatus.Pending, PaymentStatus.Processing, PaymentStatus.Completed };
        var paymentsLast10Min = await db.Payments.AsNoTracking().CountAsync(payment =>
            payment.CustomerId == customerId && payment.CreatedAt >= since10Min && activeStatuses.Contains(payment.Status),
            cancellationToken);
        var paymentsLastHour = await db.Payments.AsNoTracking().CountAsync(payment =>
            payment.CustomerId == customerId && payment.CreatedAt >= since1Hour && activeStatuses.Contains(payment.Status),
            cancellationToken);
        var failedLastHour = await db.Payments.AsNoTracking().CountAsync(payment =>
            payment.CustomerId == customerId && payment.CreatedAt >= since1Hour && payment.Status == PaymentStatus.Failed,
            cancellationToken);
        // Feeds the AML "multiple beneficiaries" rule: many distinct recipients in one day is a
        // structuring pattern even when every single payment is small.
        var distinctBeneficiaries24h = await db.Payments.AsNoTracking()
            .Where(payment => payment.CustomerId == customerId && payment.CreatedAt >= since24h)
            .Select(payment => payment.BeneficiaryId).Distinct().CountAsync(cancellationToken);
        return new FraudRuleContext(amountMinor,
            (long)(now - beneficiary.CreatedAt).TotalSeconds,
            (long)(now - source.CreatedAt).TotalSeconds,
            paymentsLast10Min, paymentsLastHour, failedLastHour, distinctBeneficiaries24h);
    }

    private static PaymentView Map(PaymentRecord payment) =>
        new(payment.Id, payment.Status.ToString().ToLowerInvariant(), payment.AmountMinor, payment.Currency,
            payment.CreatedAt, payment.Reference, payment.ProviderReference, payment.ProviderStatus,
            new RiskView(payment.RiskScore ?? 0, payment.RiskLevel ?? "low", payment.RiskDecision ?? "allow",
                JsonText.ParseStringArray(payment.RiskReasons)));

    /// <summary>The payload shape shared across implementations (spec section 17).</summary>
    private static OutboxEventRecord PaymentEvent(string eventType, PaymentRecord payment, DateTimeOffset now,
        string? providerMode = null, string? providerStatus = null, bool? accepted = null, string? journalReference = null)
    {
        var payload = new Dictionary<string, object?>
        {
            ["paymentId"] = payment.Id,
            ["customerId"] = payment.CustomerId,
            ["sourceAccountId"] = payment.SourceAccountId,
            ["beneficiaryId"] = payment.BeneficiaryId,
            ["amountMinor"] = payment.AmountMinor,
            ["currency"] = payment.Currency,
            ["status"] = payment.Status.ToString().ToLowerInvariant(),
            ["createdAt"] = payment.CreatedAt
        };
        if (providerMode is not null) payload["providerMode"] = providerMode;
        if (providerStatus is not null) payload["providerStatus"] = providerStatus;
        if (accepted is not null) payload["accepted"] = accepted.Value;
        if (journalReference is not null) payload["journalReference"] = journalReference;
        return new OutboxEventRecord
        {
            AggregateId = payment.Id,
            EventType = eventType,
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(payload, JsonOptions),
            CreatedAt = now
        };
    }

    public static string CreateRequestHash(CreatePaymentCommand command)
    {
        // Match the Python JSON representation exactly: lowercase hex SHA-256,
        // compact object with lexicographically ordered keys, UUID D format.
        var canonical = string.Concat(
            "{\"amount_minor\":", command.AmountMinor.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ",\"beneficiary_id\":\"", command.BeneficiaryId.ToString("D"),
            "\",\"currency\":", JsonSerializer.Serialize(command.Currency),
            ",\"reference\":", command.Reference is null ? "null" : JsonSerializer.Serialize(command.Reference),
            ",\"source_account_id\":\"", command.SourceAccountId.ToString("D"), "\"}");
        return Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
    }
}
