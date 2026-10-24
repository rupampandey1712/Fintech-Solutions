using FinCore.Domain;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public sealed class FinCoreDbContext(DbContextOptions<FinCoreDbContext> options) : DbContext(options)
{
    // Every new outbox row records the trace of the request that wrote it, with no call-site changes.
    public override int SaveChanges(bool acceptAllChangesOnSuccess)
    {
        StampOutboxTraces();
        return base.SaveChanges(acceptAllChangesOnSuccess);
    }

    public override Task<int> SaveChangesAsync(bool acceptAllChangesOnSuccess, CancellationToken cancellationToken = default)
    {
        StampOutboxTraces();
        return base.SaveChangesAsync(acceptAllChangesOnSuccess, cancellationToken);
    }

    private void StampOutboxTraces()
    {
        var traceparent = FinCoreTelemetry.CurrentTraceparent();
        if (traceparent is null) return;
        foreach (var entry in ChangeTracker.Entries<OutboxEventRecord>())
            if (entry.State == EntityState.Added && entry.Entity.TraceContext is null)
                entry.Entity.TraceContext = traceparent;
    }

    public DbSet<CustomerRecord> Customers => Set<CustomerRecord>();
    public DbSet<CustomerAccountRecord> Accounts => Set<CustomerAccountRecord>();
    public DbSet<AccountLimitRecord> AccountLimits => Set<AccountLimitRecord>();
    public DbSet<BeneficiaryRecord> Beneficiaries => Set<BeneficiaryRecord>();
    public DbSet<LedgerAccountRecord> LedgerAccounts => Set<LedgerAccountRecord>();
    public DbSet<JournalEntryRecord> JournalEntries => Set<JournalEntryRecord>();
    public DbSet<JournalLineRecord> JournalLines => Set<JournalLineRecord>();
    public DbSet<PaymentRecord> Payments => Set<PaymentRecord>();
    public DbSet<IdempotencyRecord> IdempotencyRecords => Set<IdempotencyRecord>();
    public DbSet<OutboxEventRecord> OutboxEvents => Set<OutboxEventRecord>();
    public DbSet<KycApplicationRecord> KycApplications => Set<KycApplicationRecord>();
    public DbSet<KycDocumentRecord> KycDocuments => Set<KycDocumentRecord>();
    public DbSet<FraudRuleRecord> FraudRules => Set<FraudRuleRecord>();
    public DbSet<FraudCaseRecord> FraudCases => Set<FraudCaseRecord>();
    public DbSet<AmlAlertRecord> AmlAlerts => Set<AmlAlertRecord>();
    public DbSet<NotificationRecord> Notifications => Set<NotificationRecord>();
    public DbSet<AuditLogRecord> AuditLogs => Set<AuditLogRecord>();
    public DbSet<ReconciliationRunRecord> ReconciliationRuns => Set<ReconciliationRunRecord>();
    public DbSet<ReconciliationRecordRow> ReconciliationRecords => Set<ReconciliationRecordRow>();
    public DbSet<OutboxDeadLetterRecord> OutboxDeadLetters => Set<OutboxDeadLetterRecord>();
    public DbSet<InboxMessageRecord> InboxMessages => Set<InboxMessageRecord>();
    public DbSet<PaymentRefundRecord> PaymentRefunds => Set<PaymentRefundRecord>();
    public DbSet<RefreshTokenRecord> RefreshTokens => Set<RefreshTokenRecord>();
    public DbSet<ProviderTransactionRecord> ProviderTransactions => Set<ProviderTransactionRecord>();
    public DbSet<PaymentDisputeRecord> PaymentDisputes => Set<PaymentDisputeRecord>();
    public DbSet<SettlementBatchRecord> SettlementBatches => Set<SettlementBatchRecord>();
    public DbSet<SettlementBatchItemRecord> SettlementBatchItems => Set<SettlementBatchItemRecord>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<CustomerRecord>(entity =>
        {
            entity.ToTable("customers");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.EmailNormalized).HasColumnName("email_normalized").HasMaxLength(320).IsRequired();
            entity.Property(x => x.PasswordHash).HasColumnName("password_hash").HasMaxLength(255).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("pending");
            entity.Property(x => x.Roles).HasColumnName("roles").HasColumnType("jsonb")
                .HasDefaultValueSql("'[\"customer\"]'::jsonb");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.EmailNormalized).IsUnique();
        });
        modelBuilder.Entity<CustomerAccountRecord>(entity =>
        {
            entity.ToTable("customer_accounts", table => table.HasCheckConstraint(
                "ck_account_currency", "currency ~ '^[A-Z]{3}$'"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.AccountType).HasColumnName("account_type").HasMaxLength(24).HasDefaultValue("wallet");
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("active");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
            entity.HasIndex(x => x.CustomerId);
        });
        modelBuilder.Entity<AccountLimitRecord>(entity =>
        {
            entity.ToTable("account_limits", table => table.HasCheckConstraint(
                "ck_account_limits_positive", "per_transaction_minor > 0 AND daily_minor > 0 AND weekly_minor > 0 AND monthly_minor > 0"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.AccountId).HasColumnName("account_id");
            entity.Property(x => x.PerTransactionMinor).HasColumnName("per_transaction_minor");
            entity.Property(x => x.DailyMinor).HasColumnName("daily_minor");
            entity.Property(x => x.WeeklyMinor).HasColumnName("weekly_minor");
            entity.Property(x => x.MonthlyMinor).HasColumnName("monthly_minor");
            entity.Property(x => x.EffectiveAt).HasColumnName("effective_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.AccountId).IsUnique();
            entity.HasOne<CustomerAccountRecord>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<BeneficiaryRecord>(entity =>
        {
            entity.ToTable("beneficiaries", table => table.HasCheckConstraint(
                "ck_beneficiary_status", "status IN ('pending_verification','cooling','active','disabled')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.DestinationAccountId).HasColumnName("destination_account_id");
            entity.Property(x => x.Label).HasColumnName("label").HasMaxLength(80).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("pending_verification");
            entity.Property(x => x.VerifiedAt).HasColumnName("verified_at").HasColumnType("timestamptz");
            entity.Property(x => x.CoolingUntil).HasColumnName("cooling_until").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<CustomerAccountRecord>().WithMany().HasForeignKey(x => x.DestinationAccountId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<LedgerAccountRecord>(entity =>
        {
            entity.ToTable("ledger_accounts");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.AccountId).HasColumnName("account_id");
            entity.Property(x => x.Code).HasColumnName("code").HasMaxLength(120).IsRequired();
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.AccountId).IsUnique();
            entity.HasIndex(x => x.Code).IsUnique();
            entity.HasOne<CustomerAccountRecord>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<JournalEntryRecord>(entity =>
        {
            entity.ToTable("journal_entries");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.BusinessReference).HasColumnName("business_reference").HasMaxLength(120).IsRequired();
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.ReversalOfId).HasColumnName("reversal_of_id");
            entity.Property(x => x.PostedAt).HasColumnName("posted_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.BusinessReference);
            entity.HasMany(x => x.Lines).WithOne().HasForeignKey(x => x.JournalEntryId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<JournalEntryRecord>().WithMany().HasForeignKey(x => x.ReversalOfId).OnDelete(DeleteBehavior.Restrict);
            // An entry is reversed at most once (partial unique index, created by Alembic 0005).
            entity.HasIndex(x => x.ReversalOfId).IsUnique().HasFilter("reversal_of_id IS NOT NULL")
                .HasDatabaseName("ux_journal_entries_reversal_of_id");
        });
        modelBuilder.Entity<JournalLineRecord>(entity =>
        {
            entity.ToTable("journal_lines", table => table.HasCheckConstraint(
                "ck_journal_line_positive_amount", "amount_minor > 0"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.JournalEntryId).HasColumnName("journal_id");
            entity.Property(x => x.LedgerAccountId).HasColumnName("ledger_account_id");
            entity.Property(x => x.Side).HasColumnName("side").HasMaxLength(6).IsRequired();
            entity.Property(x => x.AmountMinor).HasColumnName("amount_minor").HasColumnType("bigint");
            entity.HasIndex(x => x.JournalEntryId);
            entity.HasOne<LedgerAccountRecord>().WithMany().HasForeignKey(x => x.LedgerAccountId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<PaymentRecord>(entity =>
        {
            entity.ToTable("payments", table =>
            {
                table.HasCheckConstraint("ck_payment_positive_amount", "amount_minor > 0");
                table.HasCheckConstraint("ck_payment_status", "status IN ('initiated','pending','processing','completed','failed','cancelled','rejected','expired','reversed')");
            });
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.SourceAccountId).HasColumnName("source_account_id");
            entity.Property(x => x.BeneficiaryId).HasColumnName("beneficiary_id");
            entity.Property(x => x.AmountMinor).HasColumnName("amount_minor").HasColumnType("bigint");
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status")
                .HasConversion(value => value.ToString().ToLowerInvariant(), value => Enum.Parse<PaymentStatus>(value, true))
                .HasMaxLength(16);
            entity.Property(x => x.Reference).HasColumnName("reference").HasMaxLength(140);
            entity.Property(x => x.ProviderReference).HasColumnName("provider_reference").HasMaxLength(120);
            entity.Property(x => x.ProviderStatus).HasColumnName("provider_status").HasMaxLength(24);
            entity.Property(x => x.RiskScore).HasColumnName("risk_score");
            entity.Property(x => x.RiskLevel).HasColumnName("risk_level").HasMaxLength(16);
            entity.Property(x => x.RiskDecision).HasColumnName("risk_decision").HasMaxLength(16);
            entity.Property(x => x.RiskReasons).HasColumnName("risk_reasons").HasColumnType("jsonb");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<CustomerAccountRecord>().WithMany().HasForeignKey(x => x.SourceAccountId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<BeneficiaryRecord>().WithMany().HasForeignKey(x => x.BeneficiaryId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<IdempotencyRecord>(entity =>
        {
            entity.ToTable("idempotency_records");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.Operation).HasColumnName("operation").HasMaxLength(120).IsRequired();
            entity.Property(x => x.Key).HasColumnName("key").HasMaxLength(128).IsRequired();
            entity.Property(x => x.RequestHash).HasColumnName("request_hash").HasMaxLength(64).IsRequired();
            entity.Property(x => x.ResponseJson).HasColumnName("response_json").HasColumnType("jsonb");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => new { x.CustomerId, x.Operation, x.Key }).IsUnique().HasDatabaseName("uq_idempotency_scope");
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<OutboxEventRecord>(entity =>
        {
            entity.ToTable("outbox_events");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.AggregateId).HasColumnName("aggregate_id");
            entity.Property(x => x.EventType).HasColumnName("event_type").HasMaxLength(160).IsRequired();
            entity.Property(x => x.SchemaVersion).HasColumnName("schema_version").HasDefaultValue(1);
            entity.Property(x => x.PayloadJson).HasColumnName("payload").HasColumnType("jsonb").IsRequired();
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.Property(x => x.PublishedAt).HasColumnName("published_at").HasColumnType("timestamptz");
            entity.Property(x => x.Attempts).HasColumnName("attempts").HasDefaultValue(0);
            entity.Property(x => x.NextAttemptAt).HasColumnName("next_attempt_at").HasColumnType("timestamptz");
            entity.Property(x => x.TraceContext).HasColumnName("trace_context").HasMaxLength(64);
            entity.HasIndex(x => x.AggregateId);
            entity.HasIndex(x => new { x.PublishedAt, x.CreatedAt }).HasDatabaseName("ix_outbox_pending");
        });
        modelBuilder.Entity<KycApplicationRecord>(entity =>
        {
            entity.ToTable("kyc_applications", table => table.HasCheckConstraint(
                "ck_kyc_status", "status IN ('pending','under_review','approved','rejected','expired')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.FullName).HasColumnName("full_name").HasMaxLength(120).IsRequired();
            entity.Property(x => x.Country).HasColumnName("country").HasMaxLength(2).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("pending");
            entity.Property(x => x.RejectionReason).HasColumnName("rejection_reason").HasMaxLength(300);
            entity.Property(x => x.SubmittedAt).HasColumnName("submitted_at").HasColumnType("timestamptz");
            entity.Property(x => x.DecidedAt).HasColumnName("decided_at").HasColumnType("timestamptz");
            entity.Property(x => x.DecidedBy).HasColumnName("decided_by");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
            entity.HasMany(x => x.Documents).WithOne().HasForeignKey(x => x.KycApplicationId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<KycDocumentRecord>(entity =>
        {
            entity.ToTable("kyc_documents", table => table.HasCheckConstraint(
                "ck_kyc_doc_type", "document_type IN ('passport','national_id','proof_of_address')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.KycApplicationId).HasColumnName("kyc_application_id");
            entity.Property(x => x.DocumentType).HasColumnName("document_type").HasMaxLength(24).IsRequired();
            entity.Property(x => x.BlobPath).HasColumnName("blob_path").HasMaxLength(300).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("uploaded");
            entity.Property(x => x.UploadedAt).HasColumnName("uploaded_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.KycApplicationId);
        });
        modelBuilder.Entity<FraudRuleRecord>(entity =>
        {
            entity.ToTable("fraud_rules");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.Code).HasColumnName("code").HasMaxLength(60).IsRequired();
            entity.Property(x => x.Description).HasColumnName("description").HasMaxLength(200).IsRequired();
            entity.Property(x => x.ThresholdMinor).HasColumnName("threshold_minor").HasColumnType("bigint");
            entity.Property(x => x.Weight).HasColumnName("weight").HasDefaultValue(0);
            entity.Property(x => x.Enabled).HasColumnName("enabled").HasDefaultValue(true);
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.Code).IsUnique();
        });
        modelBuilder.Entity<FraudCaseRecord>(entity =>
        {
            entity.ToTable("fraud_cases", table => table.HasCheckConstraint(
                "ck_fraud_case_status", "status IN ('open','investigating','escalated','closed','false_positive')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            // Modelled so EF inserts the payment before rows that reference it in one SaveChanges.
            entity.HasOne<PaymentRecord>().WithMany().HasForeignKey(x => x.PaymentId).OnDelete(DeleteBehavior.Restrict);
            entity.Property(x => x.Score).HasColumnName("score");
            entity.Property(x => x.Level).HasColumnName("level").HasMaxLength(16).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("open");
            entity.Property(x => x.Reasons).HasColumnName("reasons").HasColumnType("jsonb").IsRequired();
            entity.Property(x => x.Resolution).HasColumnName("resolution").HasMaxLength(200);
            entity.Property(x => x.ReviewedBy).HasColumnName("reviewed_by");
            entity.Property(x => x.ReviewedAt).HasColumnName("reviewed_at").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<AmlAlertRecord>(entity =>
        {
            entity.ToTable("aml_alerts", table => table.HasCheckConstraint(
                "ck_aml_status", "status IN ('open','investigating','escalated','closed','false_positive')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            // Modelled so EF inserts the payment before rows that reference it in one SaveChanges.
            entity.HasOne<PaymentRecord>().WithMany().HasForeignKey(x => x.PaymentId).OnDelete(DeleteBehavior.Restrict);
            entity.Property(x => x.RuleCode).HasColumnName("rule_code").HasMaxLength(60).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).HasDefaultValue("open");
            entity.Property(x => x.Details).HasColumnName("details").HasColumnType("jsonb").IsRequired();
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<NotificationRecord>(entity =>
        {
            entity.ToTable("notifications", table => table.HasCheckConstraint(
                "ck_notification_channel", "channel IN ('email','sms','push','in_app')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.Channel).HasColumnName("channel").HasMaxLength(12).IsRequired();
            entity.Property(x => x.EventType).HasColumnName("event_type").HasMaxLength(80).IsRequired();
            entity.Property(x => x.Subject).HasColumnName("subject").HasMaxLength(160).IsRequired();
            entity.Property(x => x.Body).HasColumnName("body").HasMaxLength(1000).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(12).HasDefaultValue("queued");
            entity.Property(x => x.SentAt).HasColumnName("sent_at").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<AuditLogRecord>(entity =>
        {
            entity.ToTable("audit_logs");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.ActorId).HasColumnName("actor_id");
            entity.Property(x => x.Action).HasColumnName("action").HasMaxLength(80).IsRequired();
            entity.Property(x => x.EntityType).HasColumnName("entity_type").HasMaxLength(60).IsRequired();
            entity.Property(x => x.EntityId).HasColumnName("entity_id");
            entity.Property(x => x.IpAddress).HasColumnName("ip_address").HasMaxLength(64);
            entity.Property(x => x.CorrelationId).HasColumnName("correlation_id").HasMaxLength(128);
            entity.Property(x => x.OldValue).HasColumnName("old_value").HasColumnType("jsonb");
            entity.Property(x => x.NewValue).HasColumnName("new_value").HasColumnType("jsonb");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
        });
        modelBuilder.Entity<ReconciliationRunRecord>(entity =>
        {
            entity.ToTable("reconciliation_runs");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.StartedAt).HasColumnName("started_at").HasColumnType("timestamptz");
            entity.Property(x => x.FinishedAt).HasColumnName("finished_at").HasColumnType("timestamptz");
            entity.Property(x => x.Matched).HasColumnName("matched").HasDefaultValue(0);
            entity.Property(x => x.AmountMismatch).HasColumnName("amount_mismatch").HasDefaultValue(0);
            entity.Property(x => x.StatusMismatch).HasColumnName("status_mismatch").HasDefaultValue(0);
            entity.Property(x => x.MissingExternal).HasColumnName("missing_external").HasDefaultValue(0);
            entity.Property(x => x.MissingInternal).HasColumnName("missing_internal").HasDefaultValue(0);
        });
        modelBuilder.Entity<ReconciliationRecordRow>(entity =>
        {
            entity.ToTable("reconciliation_records", table => table.HasCheckConstraint(
                "ck_recon_status", "status IN ('matched','unmatched','amount_mismatch','status_mismatch','missing_internal','missing_external')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.RunId).HasColumnName("run_id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            entity.Property(x => x.ExternalReference).HasColumnName("external_reference").HasMaxLength(120);
            entity.Property(x => x.InternalStatus).HasColumnName("internal_status").HasMaxLength(16).IsRequired();
            entity.Property(x => x.ExternalStatus).HasColumnName("external_status").HasMaxLength(24);
            entity.Property(x => x.InternalAmountMinor).HasColumnName("internal_amount_minor").HasColumnType("bigint");
            entity.Property(x => x.ExternalAmountMinor).HasColumnName("external_amount_minor").HasColumnType("bigint");
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).IsRequired();
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.RunId);
            entity.HasOne<ReconciliationRunRecord>().WithMany().HasForeignKey(x => x.RunId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<OutboxDeadLetterRecord>(entity =>
        {
            entity.ToTable("outbox_dead_letters");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.OutboxEventId).HasColumnName("outbox_event_id");
            entity.Property(x => x.EventType).HasColumnName("event_type").HasMaxLength(160).IsRequired();
            entity.Property(x => x.Payload).HasColumnName("payload").HasColumnType("jsonb").IsRequired();
            entity.Property(x => x.FailureReason).HasColumnName("failure_reason").HasMaxLength(300).IsRequired();
            entity.Property(x => x.ReplayedAt).HasColumnName("replayed_at").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
        });
        modelBuilder.Entity<PaymentRefundRecord>(entity =>
        {
            entity.ToTable("payment_refunds", table => table.HasCheckConstraint("ck_refund_amount_positive", "amount_minor > 0"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            entity.Property(x => x.AmountMinor).HasColumnName("amount_minor").HasColumnType("bigint");
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.Reason).HasColumnName("reason").HasMaxLength(200);
            entity.Property(x => x.JournalId).HasColumnName("journal_id");
            entity.Property(x => x.RequestedBy).HasColumnName("requested_by");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.PaymentId);
            entity.HasOne<PaymentRecord>().WithMany().HasForeignKey(x => x.PaymentId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<JournalEntryRecord>().WithMany().HasForeignKey(x => x.JournalId).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<InboxMessageRecord>(entity =>
        {
            entity.ToTable("inbox_messages");
            entity.HasKey(x => new { x.Consumer, x.MessageId });
            entity.Property(x => x.Consumer).HasColumnName("consumer").HasMaxLength(60);
            entity.Property(x => x.MessageId).HasColumnName("message_id").HasMaxLength(128);
            entity.Property(x => x.EventType).HasColumnName("event_type").HasMaxLength(160).IsRequired();
            entity.Property(x => x.ProcessedAt).HasColumnName("processed_at").HasColumnType("timestamptz");
        });
        modelBuilder.Entity<RefreshTokenRecord>(entity =>
        {
            entity.ToTable("refresh_tokens");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.TokenHash).HasColumnName("token_hash").HasMaxLength(64).IsRequired();
            entity.Property(x => x.ExpiresAt).HasColumnName("expires_at").HasColumnType("timestamptz");
            entity.Property(x => x.RevokedAt).HasColumnName("revoked_at").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.TokenHash).IsUnique();
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        // The provider simulator's own record of every processed payment (Alembic 0007); the
        // payment_id has no foreign key on purpose — the provider may hold records we do not.
        modelBuilder.Entity<ProviderTransactionRecord>(entity =>
        {
            entity.ToTable("provider_transactions", table => table.HasCheckConstraint(
                "ck_provider_txn_status",
                "status IN ('completed','failed','timeout','duplicate','delayed','partial_failure')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            entity.Property(x => x.Reference).HasColumnName("reference").HasMaxLength(120).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(24).IsRequired();
            entity.Property(x => x.AmountMinor).HasColumnName("amount_minor").HasColumnType("bigint");
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.Mode).HasColumnName("mode").HasMaxLength(24).IsRequired();
            entity.Property(x => x.ProcessedAt).HasColumnName("processed_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.PaymentId).IsUnique();
            entity.HasIndex(x => x.Reference).IsUnique();
        });
        // Chargeback simulation: one dispute per payment, lifecycle open -> under_review -> won/lost.
        modelBuilder.Entity<PaymentDisputeRecord>(entity =>
        {
            entity.ToTable("payment_disputes", table => table.HasCheckConstraint(
                "ck_dispute_status", "status IN ('open','under_review','won','lost')"));
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            entity.Property(x => x.CustomerId).HasColumnName("customer_id");
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(16).HasDefaultValue("open");
            entity.Property(x => x.Reason).HasColumnName("reason").HasMaxLength(200).IsRequired();
            entity.Property(x => x.Evidence).HasColumnName("evidence").HasColumnType("jsonb").HasDefaultValueSql("'[]'::jsonb");
            entity.Property(x => x.DecidedBy).HasColumnName("decided_by");
            entity.Property(x => x.DecidedAt).HasColumnName("decided_at").HasColumnType("timestamptz");
            entity.Property(x => x.Resolution).HasColumnName("resolution").HasMaxLength(200);
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.PaymentId).IsUnique();
            entity.HasIndex(x => x.CustomerId);
            entity.HasOne<PaymentRecord>().WithMany().HasForeignKey(x => x.PaymentId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.Restrict);
        });
        // Settlement batches group one business day's provider-settled payments (spec section 58).
        modelBuilder.Entity<SettlementBatchRecord>(entity =>
        {
            entity.ToTable("settlement_batches", table =>
            {
                table.HasCheckConstraint("ck_settlement_status",
                    "status IN ('pending','processing','completed','failed')");
                table.HasCheckConstraint("ck_settlement_total_non_negative", "total_minor >= 0");
            });
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.BusinessDate).HasColumnName("business_date").HasColumnType("timestamptz");
            entity.Property(x => x.Currency).HasColumnName("currency").HasMaxLength(3).IsRequired();
            entity.Property(x => x.Status).HasColumnName("status").HasMaxLength(16).HasDefaultValue("pending");
            entity.Property(x => x.TotalMinor).HasColumnName("total_minor").HasColumnType("bigint").HasDefaultValue(0);
            entity.Property(x => x.FeeMinor).HasColumnName("fee_minor").HasColumnType("bigint").HasDefaultValue(0);
            entity.Property(x => x.PaymentCount).HasColumnName("payment_count").HasDefaultValue(0);
            entity.Property(x => x.FileContent).HasColumnName("file_content");
            entity.Property(x => x.FailureReason).HasColumnName("failure_reason").HasMaxLength(300);
            entity.Property(x => x.CreatedBy).HasColumnName("created_by");
            entity.Property(x => x.ProcessedAt).HasColumnName("processed_at").HasColumnType("timestamptz");
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasOne<CustomerRecord>().WithMany().HasForeignKey(x => x.CreatedBy).OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<SettlementBatchItemRecord>(entity =>
        {
            entity.ToTable("settlement_batch_items");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Id).HasColumnName("id");
            entity.Property(x => x.BatchId).HasColumnName("batch_id");
            entity.Property(x => x.PaymentId).HasColumnName("payment_id");
            entity.Property(x => x.AmountMinor).HasColumnName("amount_minor").HasColumnType("bigint");
            entity.Property(x => x.FeeMinor).HasColumnName("fee_minor").HasColumnType("bigint").HasDefaultValue(0);
            entity.Property(x => x.CreatedAt).HasColumnName("created_at").HasColumnType("timestamptz");
            entity.HasIndex(x => x.BatchId);
            entity.HasIndex(x => x.PaymentId).IsUnique();
            entity.HasOne<SettlementBatchRecord>().WithMany().HasForeignKey(x => x.BatchId).OnDelete(DeleteBehavior.Restrict);
            entity.HasOne<PaymentRecord>().WithMany().HasForeignKey(x => x.PaymentId).OnDelete(DeleteBehavior.Restrict);
        });
    }
}

public sealed class CustomerRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string EmailNormalized { get; set; }
    public required string PasswordHash { get; set; }
    public string Status { get; set; } = "pending";
    public string Roles { get; set; } = "[\"customer\"]";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class CustomerAccountRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public string AccountType { get; set; } = "wallet";
    public required string Currency { get; set; }
    public string Status { get; set; } = "active";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class LedgerAccountRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid? AccountId { get; set; }
    public required string Code { get; set; }
    public required string Currency { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class AccountLimitRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid AccountId { get; set; }
    public long PerTransactionMinor { get; set; }
    public long DailyMinor { get; set; }
    public long WeeklyMinor { get; set; }
    public long MonthlyMinor { get; set; }
    public DateTimeOffset EffectiveAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class BeneficiaryRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public Guid DestinationAccountId { get; set; }
    public required string Label { get; set; }
    public string Status { get; set; } = "pending_verification";
    public DateTimeOffset? VerifiedAt { get; set; }
    public DateTimeOffset? CoolingUntil { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class JournalEntryRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string BusinessReference { get; set; }
    public required string Currency { get; set; }
    public Guid? ReversalOfId { get; set; }
    public DateTimeOffset PostedAt { get; set; } = DateTimeOffset.UtcNow;
    public List<JournalLineRecord> Lines { get; set; } = [];
}

public sealed class JournalLineRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid JournalEntryId { get; set; }
    public Guid LedgerAccountId { get; set; }
    public required string Side { get; set; }
    public long AmountMinor { get; set; }
}

public sealed class PaymentRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public Guid SourceAccountId { get; set; }
    public Guid BeneficiaryId { get; set; }
    public long AmountMinor { get; set; }
    public required string Currency { get; set; }
    public PaymentStatus Status { get; set; } = PaymentStatus.Pending;
    public string? Reference { get; set; }
    public string? ProviderReference { get; set; }
    public string? ProviderStatus { get; set; }
    public int? RiskScore { get; set; }
    public string? RiskLevel { get; set; }
    public string? RiskDecision { get; set; }
    public string? RiskReasons { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class IdempotencyRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public required string Operation { get; set; }
    public required string Key { get; set; }
    public required string RequestHash { get; set; }
    public string? ResponseJson { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class OutboxEventRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid AggregateId { get; set; }
    public required string EventType { get; set; }
    public int SchemaVersion { get; set; } = 1;
    public required string PayloadJson { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? PublishedAt { get; set; }
    public int Attempts { get; set; }
    public DateTimeOffset? NextAttemptAt { get; set; }
    /// <summary>W3C traceparent of the request that wrote the row; the dispatcher continues that trace.</summary>
    public string? TraceContext { get; set; }
}

public sealed class KycApplicationRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public required string FullName { get; set; }
    public required string Country { get; set; }
    public string Status { get; set; } = "pending";
    public string? RejectionReason { get; set; }
    public DateTimeOffset? SubmittedAt { get; set; }
    public DateTimeOffset? DecidedAt { get; set; }
    public Guid? DecidedBy { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public List<KycDocumentRecord> Documents { get; set; } = [];
}

public sealed class KycDocumentRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid KycApplicationId { get; set; }
    public required string DocumentType { get; set; }
    public required string BlobPath { get; set; }
    public string Status { get; set; } = "uploaded";
    public DateTimeOffset UploadedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class FraudRuleRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string Code { get; set; }
    public required string Description { get; set; }
    public long? ThresholdMinor { get; set; }
    public int Weight { get; set; }
    public bool Enabled { get; set; } = true;
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class FraudCaseRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public Guid? PaymentId { get; set; }
    public int Score { get; set; }
    public required string Level { get; set; }
    public string Status { get; set; } = "open";
    public required string Reasons { get; set; }
    public string? Resolution { get; set; }
    public Guid? ReviewedBy { get; set; }
    public DateTimeOffset? ReviewedAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class AmlAlertRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public Guid? PaymentId { get; set; }
    public required string RuleCode { get; set; }
    public string Status { get; set; } = "open";
    public required string Details { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class NotificationRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public required string Channel { get; set; }
    public required string EventType { get; set; }
    public required string Subject { get; set; }
    public required string Body { get; set; }
    public string Status { get; set; } = "queued";
    public DateTimeOffset? SentAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class AuditLogRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid? ActorId { get; set; }
    public required string Action { get; set; }
    public required string EntityType { get; set; }
    public Guid? EntityId { get; set; }
    public string? IpAddress { get; set; }
    public string? CorrelationId { get; set; }
    public string? OldValue { get; set; }
    public string? NewValue { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class ReconciliationRunRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public DateTimeOffset StartedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? FinishedAt { get; set; }
    public int Matched { get; set; }
    public int AmountMismatch { get; set; }
    public int StatusMismatch { get; set; }
    public int MissingExternal { get; set; }
    public int MissingInternal { get; set; }
}

public sealed class ReconciliationRecordRow
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid RunId { get; set; }
    public Guid PaymentId { get; set; }
    public string? ExternalReference { get; set; }
    public required string InternalStatus { get; set; }
    public string? ExternalStatus { get; set; }
    public long InternalAmountMinor { get; set; }
    public long? ExternalAmountMinor { get; set; }
    public required string Status { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class OutboxDeadLetterRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid OutboxEventId { get; set; }
    public required string EventType { get; set; }
    public required string Payload { get; set; }
    public required string FailureReason { get; set; }
    public DateTimeOffset? ReplayedAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class ProviderTransactionRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid PaymentId { get; set; }
    public required string Reference { get; set; }
    public required string Status { get; set; }
    public long AmountMinor { get; set; }
    public required string Currency { get; set; }
    public required string Mode { get; set; }
    public DateTimeOffset ProcessedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PaymentDisputeRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid PaymentId { get; set; }
    public Guid CustomerId { get; set; }
    public string Status { get; set; } = "open";
    public required string Reason { get; set; }
    public string Evidence { get; set; } = "[]";
    public Guid? DecidedBy { get; set; }
    public DateTimeOffset? DecidedAt { get; set; }
    public string? Resolution { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class SettlementBatchRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public DateTimeOffset BusinessDate { get; set; }
    public required string Currency { get; set; }
    public string Status { get; set; } = "pending";
    public long TotalMinor { get; set; }
    public long FeeMinor { get; set; }
    public int PaymentCount { get; set; }
    public string? FileContent { get; set; }
    public string? FailureReason { get; set; }
    public Guid CreatedBy { get; set; }
    public DateTimeOffset? ProcessedAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class SettlementBatchItemRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid BatchId { get; set; }
    public Guid PaymentId { get; set; }
    public long AmountMinor { get; set; }
    public long FeeMinor { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PaymentRefundRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid PaymentId { get; set; }
    public long AmountMinor { get; set; }
    public required string Currency { get; set; }
    public string? Reason { get; set; }
    public Guid JournalId { get; set; }
    public Guid RequestedBy { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class InboxMessageRecord
{
    public required string Consumer { get; set; }
    public required string MessageId { get; set; }
    public required string EventType { get; set; }
    public DateTimeOffset ProcessedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class RefreshTokenRecord
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid CustomerId { get; set; }
    public required string TokenHash { get; set; }
    public DateTimeOffset ExpiresAt { get; set; }
    public DateTimeOffset? RevokedAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}
