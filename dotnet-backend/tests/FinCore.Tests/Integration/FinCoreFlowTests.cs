using FinCore.Application;
using FinCore.Domain;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace FinCore.Tests;

public sealed class RequiresPostgresFactAttribute : FactAttribute
{
    public RequiresPostgresFactAttribute()
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("FINCORE_TEST_CONNECTION_STRING")))
            Skip = "FINCORE_TEST_CONNECTION_STRING is not set.";
    }
}

public sealed class DatabaseFixture : IAsyncLifetime
{
    public string? ConnectionString { get; private set; }
    public DbContextOptions<FinCoreDbContext> Options { get; private set; } = default!;

    public Task InitializeAsync()
    {
        ConnectionString = Environment.GetEnvironmentVariable("FINCORE_TEST_CONNECTION_STRING");
        if (ConnectionString is not null)
            Options = new DbContextOptionsBuilder<FinCoreDbContext>().UseNpgsql(ConnectionString).Options;
        return Task.CompletedTask;
    }

    public FinCoreDbContext CreateContext() => new(Options);

    public Task DisposeAsync() => Task.CompletedTask;
}

[CollectionDefinition("postgres")]
public sealed class PostgresCollection : ICollectionFixture<DatabaseFixture>;

[Collection("postgres")]
public class FinCoreFlowTests(DatabaseFixture fixture)
{
    private static readonly IConfiguration EmptyConfig = new ConfigurationBuilder().Build();

    private static IConfiguration KycConfig() => new ConfigurationBuilder()
        .AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["KYC_STORAGE_PATH"] = Path.Combine(Path.GetTempPath(), "fincore-kyc-" + Guid.NewGuid().ToString("N"))
        }).Build();

    private static IAccessTokenIssuer Issuer() =>
        new JwtAccessTokenIssuer(new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["JWT_SIGNING_KEY"] = new string('k', 40) })
            .Build());

    private static async Task<(Guid CustomerId, Guid AccountId)> CreateFundedCustomerAsync(FinCoreDbContext db,
        long balanceMinor)
    {
        var auth = new AuthenticationService(new CustomerIdentityRepository(db), new CustomerPasswordHasher(), Issuer());
        var customer = await auth.RegisterAsync($"it-{Guid.NewGuid():N}@example.com", "integration-password-123", default);
        var accountService = new AccountService(db, new AccountLimitDefaults(10_000_000, 50_000_000, 100_000_000, 200_000_000));
        var account = await accountService.CreateAsync(customer.Id, "wallet", "USD", default);
        // Journals reject zero amounts, so an unfunded account gets no funding entry.
        if (balanceMinor == 0)
            return (customer.Id, account.Id);

        var ledger = await db.LedgerAccounts.SingleAsync(item => item.AccountId == account.Id);
        var funding = new LedgerAccountRecord { AccountId = null, Code = $"funding:{Guid.NewGuid():N}", Currency = "USD" };
        db.LedgerAccounts.Add(funding);
        var journal = new JournalEntryRecord { BusinessReference = $"funding:{Guid.NewGuid():N}", Currency = "USD" };
        var draft = JournalDraft.Create(journal.BusinessReference, "USD",
        [
            new JournalLineDraft(funding.Id, EntrySide.Debit, new Money(balanceMinor, "USD")),
            new JournalLineDraft(ledger.Id, EntrySide.Credit, new Money(balanceMinor, "USD"))
        ]);
        journal.Lines = draft.Lines.Select(line => new JournalLineRecord
        {
            JournalEntryId = journal.Id,
            LedgerAccountId = line.LedgerAccountId,
            Side = line.Side.ToString().ToLowerInvariant(),
            AmountMinor = line.Amount.AmountMinor
        }).ToList();
        db.JournalEntries.Add(journal);
        await db.SaveChangesAsync();
        db.ChangeTracker.Clear();
        return (customer.Id, account.Id);
    }

    private static async Task<(Guid SourceAccountId, Guid BeneficiaryId)> ActivateBeneficiaryAsync(FinCoreDbContext db,
        Guid sourceCustomerId, Guid sourceAccountId)
    {
        var (otherCustomerId, destinationAccountId) = await CreateFundedCustomerAsync(db, 0);
        var beneficiaryService = new BeneficiaryService(db, TimeSpan.Zero);
        var beneficiary = await beneficiaryService.AddAsync(sourceCustomerId, destinationAccountId, "integration", default);
        beneficiary = await beneficiaryService.VerifyAsync(sourceCustomerId, beneficiary.Id, default);
        beneficiary = await beneficiaryService.ActivateAsync(sourceCustomerId, beneficiary.Id, default);
        return (sourceAccountId, beneficiary.Id);
    }

    [RequiresPostgresFact]
    public async Task Payment_flow_supports_idempotent_replay()
    {
        await using var db = fixture.CreateContext();
        var (customerId, accountId) = await CreateFundedCustomerAsync(db, 100_000);
        var (_, beneficiaryId) = await ActivateBeneficiaryAsync(db, customerId, accountId);
        var paymentService = new PaymentService(db);
        var command = new CreatePaymentCommand(accountId, beneficiaryId, 12_345, "USD", null);

        var first = await paymentService.CreateAsync(customerId, command, "replay-key-0001", default);
        first.Status.Should().Be("completed");
        first.Risk.Decision.Should().Be("allow");
        first.Risk.Score.Should().Be(0);

        var replay = await paymentService.CreateAsync(customerId, command, "replay-key-0001", default);
        replay.Id.Should().Be(first.Id);
        replay.Risk.Should().BeEquivalentTo(first.Risk);

        await using var verify = fixture.CreateContext();
        (await verify.Payments.CountAsync(payment => payment.Id == first.Id)).Should().Be(1);
    }

    [RequiresPostgresFact]
    public async Task Concurrent_payments_allow_exactly_one_success()
    {
        await using var setup = fixture.CreateContext();
        var (customerId, accountId) = await CreateFundedCustomerAsync(setup, 100_000);
        var (_, beneficiaryId) = await ActivateBeneficiaryAsync(setup, customerId, accountId);

        async Task<PaymentView> TryPayAsync(int key)
        {
            await using var db = fixture.CreateContext();
            var service = new PaymentService(db);
            return await service.CreateAsync(customerId,
                new CreatePaymentCommand(accountId, beneficiaryId, 80_000, "USD", null),
                $"concurrent-key-{Guid.NewGuid():N}-{key}", default);
        }

        var first = TryPayAsync(1);
        var second = TryPayAsync(2);
        var outcomes = new List<PaymentView>();
        var failures = new List<PaymentException>();
        try
        {
            outcomes.Add(await first);
        }
        catch (PaymentException exception) { failures.Add(exception); }
        try
        {
            outcomes.Add(await second);
        }
        catch (PaymentException exception) { failures.Add(exception); }

        outcomes.Should().HaveCount(1);
        failures.Should().HaveCount(1).And.ContainSingle(exception => exception.Code == "insufficient_funds");

        await using var verify = fixture.CreateContext();
        var ledger = await verify.LedgerAccounts.SingleAsync(item => item.AccountId == accountId);
        var balance = await verify.JournalLines.Where(line => line.LedgerAccountId == ledger.Id)
            .SumAsync(line => line.Side == "credit" ? line.AmountMinor : -line.AmountMinor);
        balance.Should().Be(20_000);
    }

    [RequiresPostgresFact]
    public async Task Fraud_rules_block_payment_and_discard_idempotency_claim()
    {
        await using var setup = fixture.CreateContext();
        var (customerId, accountId) = await CreateFundedCustomerAsync(setup, 500_000);
        var (_, beneficiaryId) = await ActivateBeneficiaryAsync(setup, customerId, accountId);

        // Rules are seeded at API startup, which tests do not run, so make sure the rule exists.
        // Weight 80 alone crosses the block threshold (score > 70); the default weight 30 does not.
        var rule = await setup.FraudRules.SingleOrDefaultAsync(item => item.Code == "large_amount");
        if (rule is null)
        {
            rule = new FraudRuleRecord { Code = "large_amount", Description = "Large amount", ThresholdMinor = 5_000_000, Weight = 30 };
            setup.FraudRules.Add(rule);
        }
        var originalThreshold = rule.ThresholdMinor;
        var originalWeight = rule.Weight;
        rule.ThresholdMinor = 1_000;
        rule.Weight = 80;
        await setup.SaveChangesAsync();

        try
        {
            var service = new PaymentService(setup);
            var blocked = await FluentActions.Awaiting(() => service.CreateAsync(customerId,
                new CreatePaymentCommand(accountId, beneficiaryId, 50_000, "USD", "blocked-attempt"),
                "fraud-block-key-01", default)).Should().ThrowAsync<PaymentException>();
            blocked.Which.Code.Should().Be("fraud_blocked");

            await using var verify = fixture.CreateContext();
            (await verify.Payments.CountAsync(payment => payment.CustomerId == customerId)).Should().Be(0);
            (await verify.IdempotencyRecords.CountAsync(record => record.CustomerId == customerId)).Should().Be(0);

            // Restoring the threshold lets the same payment succeed.
            var restored = await verify.FraudRules.SingleAsync(item => item.Code == "large_amount");
            restored.ThresholdMinor = originalThreshold;
            await verify.SaveChangesAsync();
            var recovered = await new PaymentService(verify).CreateAsync(customerId,
                new CreatePaymentCommand(accountId, beneficiaryId, 50_000, "USD", "blocked-attempt"),
                "fraud-block-key-01", default);
            recovered.Status.Should().Be("completed");
            recovered.Risk.Decision.Should().Be("allow");
        }
        finally
        {
            await using var teardown = fixture.CreateContext();
            var cleanup = await teardown.FraudRules.SingleAsync(item => item.Code == "large_amount");
            cleanup.ThresholdMinor = originalThreshold;
            cleanup.Weight = originalWeight;
            await teardown.SaveChangesAsync();
        }
    }

    [RequiresPostgresFact]
    public async Task Kyc_approval_activates_customer_and_emits_outbox_event()
    {
        await using var db = fixture.CreateContext();
        var (customerId, _) = await CreateFundedCustomerAsync(db, 0);
        var configuration = KycConfig();
        var kycService = new KycService(db, new KycDocumentStore(configuration), configuration);

        var application = await kycService.CreateAsync(customerId, "Jane Smith", "US", default);
        application.Status.Should().Be("pending");

        await FluentActions.Awaiting(() => kycService.SubmitAsync(customerId, application.Id, default))
            .Should().ThrowAsync<KycException>().Where(exception => exception.Code == "kyc_documents_required");

        var document = await kycService.AddDocumentAsync(customerId, application.Id, "passport", "aGVsbG8=", default);
        document.Status.Should().Be("uploaded");
        document.BlobPath.Should().StartWith("kyc-documents/");

        var submitted = await kycService.SubmitAsync(customerId, application.Id, default);
        submitted.Status.Should().Be("approved");

        await using var verify = fixture.CreateContext();
        var customer = await verify.Customers.SingleAsync(item => item.Id == customerId);
        customer.Status.Should().Be("active");
        // payload is jsonb, which Postgres cannot LIKE-match, so match on the aggregate instead.
        (await verify.OutboxEvents.AnyAsync(item => item.EventType == "kyc.approved.v1"
            && item.AggregateId == application.Id)).Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Refresh_tokens_rotate_and_revoke()
    {
        await using var db = fixture.CreateContext();
        var auth = new AuthenticationService(new CustomerIdentityRepository(db), new CustomerPasswordHasher(), Issuer());
        var customer = await auth.RegisterAsync($"it-{Guid.NewGuid():N}@example.com", "integration-password-123", default);
        var refreshTokens = new RefreshTokenService(db, Issuer(), EmptyConfig);

        var first = await refreshTokens.IssueAsync(customer.Id, default);
        var rotated = await refreshTokens.RotateAsync(first, default);
        rotated.CustomerId.Should().Be(customer.Id);
        rotated.RefreshToken.Should().NotBe(first);

        await FluentActions.Awaiting(() => refreshTokens.RotateAsync(first, default))
            .Should().ThrowAsync<AuthenticationException>().Where(exception => exception.StatusCode == 401);

        await refreshTokens.RevokeAsync(rotated.RefreshToken, default);
        await FluentActions.Awaiting(() => refreshTokens.RotateAsync(rotated.RefreshToken, default))
            .Should().ThrowAsync<AuthenticationException>();
    }
}
