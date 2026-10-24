using FinCore.Application;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace FinCore.Tests;

[Collection("postgres")]
public partial class HoldsAndTreasuryTests(DatabaseFixture fixture)
{
    private static IAccessTokenIssuer Issuer() =>
        new JwtAccessTokenIssuer(new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["JWT_SIGNING_KEY"] = new string('k', 40) })
            .Build());

    private async Task<(Guid CustomerId, Guid AccountId)> CustomerWithAccountAsync()
    {
        await using var db = fixture.CreateContext();
        var auth = new AuthenticationService(new CustomerIdentityRepository(db), new CustomerPasswordHasher(), Issuer());
        var customer = await auth.RegisterAsync($"hold-{Guid.NewGuid():N}@example.com", "integration-password-123", default);
        var account = await new AccountService(db, new AccountLimitDefaults(10_000_000, 50_000_000, 100_000_000, 200_000_000))
            .CreateAsync(customer.Id, "wallet", "USD", default);
        return (customer.Id, account.Id);
    }

    private async Task<long> BalanceAsync(Guid accountId)
    {
        await using var db = fixture.CreateContext();
        return await (from line in db.JournalLines
                      join ledger in db.LedgerAccounts on line.LedgerAccountId equals ledger.Id
                      where ledger.AccountId == accountId
                      select line.Side == "credit" ? line.AmountMinor : -line.AmountMinor).SumAsync();
    }

    private async Task<DepositView> DepositAsync(Guid accountId, long amount, string key)
    {
        var (staff, _) = await CustomerWithAccountAsync();  // deposits are attributed to a real staff customer
        await using var db = fixture.CreateContext();
        return await new TreasuryService(db).DepositAsync(staff, accountId, amount, "funding", key, default);
    }

    /// <summary>Runs <paramref name="body"/> with large_amount set to land payments in the review band (40-70).</summary>
    private async Task WithReviewRuleAsync(Func<Task> body)
    {
        await using var setup = fixture.CreateContext();
        var rule = await setup.FraudRules.SingleOrDefaultAsync(item => item.Code == "large_amount");
        if (rule is null)
        {
            rule = new FraudRuleRecord { Code = "large_amount", Description = "Large amount", ThresholdMinor = 5_000_000, Weight = 30 };
            setup.FraudRules.Add(rule);
        }
        var (threshold, weight) = (rule.ThresholdMinor, rule.Weight);
        rule.ThresholdMinor = 100;
        rule.Weight = 50;
        await setup.SaveChangesAsync();
        try
        {
            await body();
        }
        finally
        {
            await using var teardown = fixture.CreateContext();
            var restore = await teardown.FraudRules.SingleAsync(item => item.Code == "large_amount");
            restore.ThresholdMinor = threshold;
            restore.Weight = weight;
            await teardown.SaveChangesAsync();
        }
    }

    private async Task<(Guid Alice, Guid AliceAccount, Guid BobAccount, PaymentView Payment, Guid CaseId)> HeldPaymentAsync()
    {
        var (alice, aliceAccount) = await CustomerWithAccountAsync();
        var (_, bobAccount) = await CustomerWithAccountAsync();
        await DepositAsync(aliceAccount, 100_000, $"fund-{Guid.NewGuid():N}");
        Guid beneficiaryId;
        await using (var db = fixture.CreateContext())
        {
            var beneficiaries = new BeneficiaryService(db, TimeSpan.Zero);
            var beneficiary = await beneficiaries.AddAsync(alice, bobAccount, "bob", default);
            await beneficiaries.VerifyAsync(alice, beneficiary.Id, default);
            beneficiaryId = (await beneficiaries.ActivateAsync(alice, beneficiary.Id, default)).Id;
        }
        PaymentView payment = null!;
        await WithReviewRuleAsync(async () =>
        {
            await using var db = fixture.CreateContext();
            payment = await new PaymentService(db).CreateAsync(alice,
                new CreatePaymentCommand(aliceAccount, beneficiaryId, 30_000, "USD", null), $"hold-{Guid.NewGuid():N}", default);
        });
        await using var verify = fixture.CreateContext();
        var caseId = await verify.FraudCases.Where(item => item.PaymentId == payment.Id).Select(item => item.Id).SingleAsync();
        return (alice, aliceAccount, bobAccount, payment, caseId);
    }

    private async Task<string?> SettleAsync(Guid caseId, string resolution)
    {
        await using var db = fixture.CreateContext();
        await using var transaction = await db.Database.BeginTransactionAsync();
        var fraudCase = await db.FraudCases.SingleAsync(item => item.Id == caseId);
        var status = await PaymentHoldService.SettleAsync(db, fraudCase.PaymentId, resolution, default);
        await db.SaveChangesAsync();
        await transaction.CommitAsync();
        return status;
    }

    private async Task<(Guid AliceAccount, Guid BobAccount, PaymentView Payment)> TimedOutPaymentAsync()
    {
        var (alice, aliceAccount) = await CustomerWithAccountAsync();
        var (_, bobAccount) = await CustomerWithAccountAsync();
        await DepositAsync(aliceAccount, 100_000, $"fund-{Guid.NewGuid():N}");
        await using var db = fixture.CreateContext();
        var beneficiaries = new BeneficiaryService(db, TimeSpan.Zero);
        var beneficiary = await beneficiaries.AddAsync(alice, bobAccount, "bob", default);
        await beneficiaries.VerifyAsync(alice, beneficiary.Id, default);
        var beneficiaryId = (await beneficiaries.ActivateAsync(alice, beneficiary.Id, default)).Id;
        var payment = await new PaymentService(db).CreateAsync(alice,
            new CreatePaymentCommand(aliceAccount, beneficiaryId, 25_000, "USD", "force-provider-timeout"),
            $"timeout-{Guid.NewGuid():N}", default);
        return (aliceAccount, bobAccount, payment);
    }

    private async Task<FinCore.Application.ReconciliationRecordView> ReconcileAsync(Guid paymentId)
    {
        await using var db = fixture.CreateContext();
        var run = await ReconciliationService.ExecuteRunAsync(db, default);
        return run.Records.Single(record => record.PaymentId == paymentId);
    }

    private async Task<string> PaymentStatusAsync(Guid paymentId)
    {
        await using var db = fixture.CreateContext();
        var payment = await db.Payments.SingleAsync(item => item.Id == paymentId);
        return payment.Status.ToString().ToLowerInvariant();
    }

    [RequiresPostgresFact]
    public async Task Timed_out_payment_is_held_until_reconciliation_releases_it()
    {
        var (aliceAccount, bobAccount, payment) = await TimedOutPaymentAsync();
        payment.Status.Should().Be("processing");
        (await BalanceAsync(aliceAccount)).Should().Be(75_000, "the funds are reserved while the outcome is unknown");
        (await BalanceAsync(bobAccount)).Should().Be(0);

        var found = await ReconcileAsync(payment.Id);
        found.Status.Should().Be("status_mismatch");
        found.InternalStatus.Should().Be("processing");
        found.ExternalStatus.Should().Be("completed");
        (await PaymentStatusAsync(payment.Id)).Should().Be("completed");
        (await BalanceAsync(aliceAccount)).Should().Be(75_000);
        (await BalanceAsync(bobAccount)).Should().Be(25_000);

        (await ReconcileAsync(payment.Id)).Status.Should().Be("matched");
        (await BalanceAsync(bobAccount)).Should().Be(25_000, "a settled payment is not settled twice");
    }

    [RequiresPostgresFact]
    public async Task Timed_out_payment_the_provider_failed_is_refunded()
    {
        var (aliceAccount, bobAccount, payment) = await TimedOutPaymentAsync();
        await using (var db = fixture.CreateContext())
        {
            var provider = await db.ProviderTransactions.SingleAsync(item => item.PaymentId == payment.Id);
            provider.Status = "failed";
            await db.SaveChangesAsync();
        }

        await ReconcileAsync(payment.Id);
        (await PaymentStatusAsync(payment.Id)).Should().Be("failed");
        (await BalanceAsync(aliceAccount)).Should().Be(100_000);
        (await BalanceAsync(bobAccount)).Should().Be(0);
    }

    [RequiresPostgresFact]
    public async Task Flagged_payment_is_held_until_released()
    {
        var (_, aliceAccount, bobAccount, payment, caseId) = await HeldPaymentAsync();
        payment.Status.Should().Be("pending");
        payment.Risk.Decision.Should().Be("review");
        (await BalanceAsync(aliceAccount)).Should().Be(70_000);
        (await BalanceAsync(bobAccount)).Should().Be(0);

        (await SettleAsync(caseId, "false_positive")).Should().Be("completed");
        (await BalanceAsync(bobAccount)).Should().Be(30_000);
        (await SettleAsync(caseId, "false_positive")).Should().BeNull("a settled payment is not settled twice");
        (await BalanceAsync(bobAccount)).Should().Be(30_000);

        await using var verify = fixture.CreateContext();
        (await verify.OutboxEvents.AnyAsync(e => e.AggregateId == payment.Id && e.EventType == "payment.completed.v1"))
            .Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Confirmed_fraud_refunds_the_held_payment()
    {
        var (_, aliceAccount, bobAccount, payment, caseId) = await HeldPaymentAsync();

        (await SettleAsync(caseId, "closed")).Should().Be("rejected");

        (await BalanceAsync(aliceAccount)).Should().Be(100_000);
        (await BalanceAsync(bobAccount)).Should().Be(0);
        await using var verify = fixture.CreateContext();
        (await verify.OutboxEvents.AnyAsync(e => e.AggregateId == payment.Id && e.EventType == "payment.rejected.v1"))
            .Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Deposits_are_idempotent_per_key()
    {
        var (_, account) = await CustomerWithAccountAsync();
        var (actor, _) = await CustomerWithAccountAsync();
        var key = $"dep-{Guid.NewGuid():N}";

        DepositView first, replay;
        await using (var db = fixture.CreateContext())
            first = await new TreasuryService(db).DepositAsync(actor, account, 250_000, "opening", key, default);
        await using (var db = fixture.CreateContext())
            replay = await new TreasuryService(db).DepositAsync(actor, account, 250_000, "opening", key, default);

        replay.JournalId.Should().Be(first.JournalId);
        first.BalanceMinor.Should().Be(250_000);
        (await BalanceAsync(account)).Should().Be(250_000);
        await using (var db = fixture.CreateContext())
            await FluentActions.Awaiting(() => new TreasuryService(db).DepositAsync(actor, account, 1, "opening", key, default))
                .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "idempotency_key_reused");
    }
}
