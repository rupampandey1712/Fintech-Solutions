using FinCore.Application;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace FinCore.Tests;

[Collection("postgres")]
public class CorrectionsTests(DatabaseFixture fixture)
{
    private static IAccessTokenIssuer Issuer() =>
        new JwtAccessTokenIssuer(new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["JWT_SIGNING_KEY"] = new string('k', 40) })
            .Build());

    private async Task<(Guid CustomerId, Guid AccountId)> CustomerWithAccountAsync()
    {
        await using var db = fixture.CreateContext();
        var auth = new AuthenticationService(new CustomerIdentityRepository(db), new CustomerPasswordHasher(), Issuer());
        var customer = await auth.RegisterAsync($"corr-{Guid.NewGuid():N}@example.com", "integration-password-123", default);
        var account = await new AccountService(db, new AccountLimitDefaults(10_000_000, 50_000_000, 100_000_000, 200_000_000))
            .CreateAsync(customer.Id, "wallet", "USD", default);
        return (customer.Id, account.Id);
    }

    private async Task<DepositView> DepositAsync(Guid staff, Guid accountId, long amount)
    {
        await using var db = fixture.CreateContext();
        return await new TreasuryService(db).DepositAsync(staff, accountId, amount, "funding", $"dep-{Guid.NewGuid():N}", default);
    }

    private async Task<long> BalanceAsync(Guid accountId)
    {
        await using var db = fixture.CreateContext();
        var ledger = await db.LedgerAccounts.SingleAsync(item => item.AccountId == accountId);
        return await LedgerPostings.BalanceAsync(db, ledger.Id, default);
    }

    private async Task<bool> LedgerIsBalancedAsync()
    {
        await using var db = fixture.CreateContext();
        return await db.JournalLines.SumAsync(line => line.Side == "debit" ? line.AmountMinor : -line.AmountMinor) == 0;
    }

    private sealed record Paid(Guid Staff, Guid Alice, Guid AliceAccount, Guid Bob, Guid BobAccount, PaymentView Payment);

    /// <summary>Alice (funded with 100,000) pays Bob <paramref name="amount"/>.</summary>
    private async Task<Paid> PaidAsync(long amount = 50_000)
    {
        var (staff, _) = await CustomerWithAccountAsync();
        var (alice, aliceAccount) = await CustomerWithAccountAsync();
        var (bob, bobAccount) = await CustomerWithAccountAsync();
        await DepositAsync(staff, aliceAccount, 100_000);
        await using var db = fixture.CreateContext();
        var beneficiaries = new BeneficiaryService(db, TimeSpan.Zero);
        var beneficiary = await beneficiaries.AddAsync(alice, bobAccount, "bob", default);
        await beneficiaries.VerifyAsync(alice, beneficiary.Id, default);
        await beneficiaries.ActivateAsync(alice, beneficiary.Id, default);
        var payment = await new PaymentService(db).CreateAsync(alice,
            new CreatePaymentCommand(aliceAccount, beneficiary.Id, amount, "USD", null), $"pay-{Guid.NewGuid():N}", default);
        payment.Status.Should().Be("completed");
        return new Paid(staff, alice, aliceAccount, bob, bobAccount, payment);
    }

    private async Task<RefundResult> RefundAsync(Paid paid, long amount, string? key = null)
    {
        await using var db = fixture.CreateContext();
        return await new CorrectionsService(db).RefundAsync(paid.Staff, paid.Payment.Id, amount, "test",
            key ?? $"ref-{Guid.NewGuid():N}", default);
    }

    [RequiresPostgresFact]
    public async Task Partial_and_repeated_refunds_stop_at_the_original_amount()
    {
        var paid = await PaidAsync();
        (await RefundAsync(paid, 20_000)).RemainingMinor.Should().Be(30_000);
        (await RefundAsync(paid, 30_000)).RemainingMinor.Should().Be(0);
        await FluentActions.Awaiting(() => RefundAsync(paid, 1)).Should().ThrowAsync<PaymentException>()
            .Where(e => e.Code == "refund_exceeds_payment");

        (await BalanceAsync(paid.AliceAccount)).Should().Be(100_000);
        (await BalanceAsync(paid.BobAccount)).Should().Be(0);
        await using var verify = fixture.CreateContext();
        (await verify.OutboxEvents.CountAsync(e => e.AggregateId == paid.Payment.Id && e.EventType == "payment.refunded.v1"))
            .Should().Be(2);
        (await LedgerIsBalancedAsync()).Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Refund_is_idempotent_per_key()
    {
        var paid = await PaidAsync();
        var key = $"ref-{Guid.NewGuid():N}";
        var first = await RefundAsync(paid, 10_000, key);
        var replay = await RefundAsync(paid, 10_000, key);
        replay.Id.Should().Be(first.Id);
        await FluentActions.Awaiting(() => RefundAsync(paid, 5_000, key)).Should().ThrowAsync<PaymentException>()
            .Where(e => e.Code == "idempotency_key_reused");
        (await BalanceAsync(paid.AliceAccount)).Should().Be(60_000);
    }

    [RequiresPostgresFact]
    public async Task Refund_needs_the_recipient_to_have_the_funds()
    {
        var paid = await PaidAsync();
        var (_, carolAccount) = await CustomerWithAccountAsync();
        await using (var db = fixture.CreateContext())
        {
            var beneficiaries = new BeneficiaryService(db, TimeSpan.Zero);
            var toCarol = await beneficiaries.AddAsync(paid.Bob, carolAccount, "carol", default);
            await beneficiaries.VerifyAsync(paid.Bob, toCarol.Id, default);
            await beneficiaries.ActivateAsync(paid.Bob, toCarol.Id, default);
            await new PaymentService(db).CreateAsync(paid.Bob,
                new CreatePaymentCommand(paid.BobAccount, toCarol.Id, 45_000, "USD", null), $"spend-{Guid.NewGuid():N}", default);
        }
        await FluentActions.Awaiting(() => RefundAsync(paid, 10_000)).Should().ThrowAsync<PaymentException>()
            .Where(e => e.Code == "recipient_insufficient_funds");
    }

    [RequiresPostgresFact]
    public async Task Reversal_mirrors_the_original_and_is_final()
    {
        var paid = await PaidAsync();
        await using (var db = fixture.CreateContext())
            (await new CorrectionsService(db).ReversePaymentAsync(paid.Staff, paid.Payment.Id, "wrong beneficiary", $"rev-{Guid.NewGuid():N}", default))
                .Status.Should().Be("reversed");

        (await BalanceAsync(paid.AliceAccount)).Should().Be(100_000);
        (await BalanceAsync(paid.BobAccount)).Should().Be(0);
        await using (var verify = fixture.CreateContext())
        {
            var original = await verify.JournalEntries.SingleAsync(e => e.BusinessReference == $"payment:{paid.Payment.Id}");
            (await verify.JournalEntries.AnyAsync(e => e.ReversalOfId == original.Id)).Should().BeTrue();
            (await verify.Payments.SingleAsync(p => p.Id == paid.Payment.Id)).Status.Should().Be(FinCore.Domain.PaymentStatus.Reversed);
        }
        await using (var db = fixture.CreateContext())
            await FluentActions.Awaiting(() => new CorrectionsService(db).ReversePaymentAsync(paid.Staff, paid.Payment.Id, "again", $"rev-{Guid.NewGuid():N}", default))
                .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "payment_not_reversible");
        await FluentActions.Awaiting(() => RefundAsync(paid, 1)).Should().ThrowAsync<PaymentException>()
            .Where(e => e.Code == "payment_not_refundable");
        (await LedgerIsBalancedAsync()).Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Refunded_payment_cannot_be_reversed()
    {
        var paid = await PaidAsync();
        await RefundAsync(paid, 100);
        await using var db = fixture.CreateContext();
        await FluentActions.Awaiting(() => new CorrectionsService(db).ReversePaymentAsync(paid.Staff, paid.Payment.Id, "late", $"rev-{Guid.NewGuid():N}", default))
            .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "payment_has_refunds");
    }

    [RequiresPostgresFact]
    public async Task Deposits_are_reversed_once_through_the_journal()
    {
        var (staff, _) = await CustomerWithAccountAsync();
        var (customer, account) = await CustomerWithAccountAsync();
        var deposit = await DepositAsync(staff, account, 7_500);

        JournalReversalResult reversal;
        await using (var db = fixture.CreateContext())
            reversal = await new CorrectionsService(db).ReverseJournalAsync(staff, deposit.JournalId, "wrong account", $"jr-{Guid.NewGuid():N}", default);
        (await BalanceAsync(account)).Should().Be(0);

        await using (var db = fixture.CreateContext())
            await FluentActions.Awaiting(() => new CorrectionsService(db).ReverseJournalAsync(staff, deposit.JournalId, "twice", $"jr-{Guid.NewGuid():N}", default))
                .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "already_reversed");
        await using (var db = fixture.CreateContext())
            await FluentActions.Awaiting(() => new CorrectionsService(db).ReverseJournalAsync(staff, reversal.ReversalJournalId, "undo", $"jr-{Guid.NewGuid():N}", default))
                .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "reversal_not_reversible");
        await using (var db = fixture.CreateContext())
            (await new CorrectionsService(db).ListCustomerDepositsAsync(customer, default)).Single().Reversed.Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Payment_journals_must_use_the_payment_reversal()
    {
        var paid = await PaidAsync();
        await using var db = fixture.CreateContext();
        var journal = await db.JournalEntries.SingleAsync(e => e.BusinessReference == $"payment:{paid.Payment.Id}");
        await FluentActions.Awaiting(() => new CorrectionsService(db).ReverseJournalAsync(paid.Staff, journal.Id, "bypass", $"jr-{Guid.NewGuid():N}", default))
            .Should().ThrowAsync<PaymentException>().Where(e => e.Code == "use_payment_reversal");
    }

    [RequiresPostgresFact]
    public async Task Concurrent_refunds_never_exceed_the_payment()
    {
        var paid = await PaidAsync();
        var attempts = await Task.WhenAll(Enumerable.Range(0, 4).Select(async _ =>
        {
            try
            {
                await RefundAsync(paid, 20_000);
                return true;
            }
            catch (PaymentException exception) when (exception.Code == "refund_exceeds_payment")
            {
                return false;
            }
        }));
        attempts.Count(ok => ok).Should().Be(2);
        (await BalanceAsync(paid.AliceAccount)).Should().Be(90_000);
        (await LedgerIsBalancedAsync()).Should().BeTrue();
    }
}
