using FinCore.Domain;
using FluentAssertions;
using Xunit;

namespace FinCore.Tests;

public class MoneyTests
{
    [Fact]
    public void Constructor_accepts_valid_currency()
    {
        var money = new Money(1000, "USD");
        money.AmountMinor.Should().Be(1000);
        money.Currency.Should().Be("USD");
    }

    [Theory]
    [InlineData("usd")]
    [InlineData("US")]
    [InlineData("USDX")]
    [InlineData("")]
    public void Constructor_rejects_invalid_currency(string currency) =>
        FluentActions.Invoking(() => new Money(1, currency)).Should().Throw<ArgumentException>();

    [Fact]
    public void Add_combines_same_currency()
    {
        var total = new Money(150, "EUR").Add(new Money(50, "EUR"));
        total.AmountMinor.Should().Be(200);
    }

    [Fact]
    public void Subtract_combines_same_currency()
    {
        var result = new Money(150, "EUR").Subtract(new Money(50, "EUR"));
        result.AmountMinor.Should().Be(100);
    }

    [Fact]
    public void Add_rejects_currency_mismatch() =>
        FluentActions.Invoking(() => new Money(1, "USD").Add(new Money(1, "EUR")))
            .Should().Throw<InvalidOperationException>();

    [Fact]
    public void Overflow_is_detected()
    {
        var max = new Money(long.MaxValue, "USD");
        FluentActions.Invoking(() => max.Add(new Money(1, "USD"))).Should().Throw<OverflowException>();
    }
}

public class JournalDraftTests
{
    private static JournalLineDraft Line(Guid id, EntrySide side, long amount) =>
        new(id, side, new Money(amount, "USD"));

    [Fact]
    public void Balanced_draft_is_created()
    {
        var draft = JournalDraft.Create("payment:1", "USD",
        [
            Line(Guid.NewGuid(), EntrySide.Debit, 500),
            Line(Guid.NewGuid(), EntrySide.Credit, 500)
        ]);
        draft.Lines.Should().HaveCount(2);
    }

    [Fact]
    public void Unbalanced_draft_throws() =>
        FluentActions.Invoking(() => JournalDraft.Create("payment:1", "USD",
        [
            Line(Guid.NewGuid(), EntrySide.Debit, 500),
            Line(Guid.NewGuid(), EntrySide.Credit, 400)
        ])).Should().Throw<InvalidOperationException>();

    [Fact]
    public void Single_line_throws() =>
        FluentActions.Invoking(() => JournalDraft.Create("payment:1", "USD",
            [Line(Guid.NewGuid(), EntrySide.Debit, 500)])).Should().Throw<ArgumentException>();

    [Fact]
    public void Negative_amount_throws() =>
        FluentActions.Invoking(() => JournalDraft.Create("payment:1", "USD",
        [
            Line(Guid.NewGuid(), EntrySide.Debit, -1),
            Line(Guid.NewGuid(), EntrySide.Credit, -1)
        ])).Should().Throw<ArgumentException>();

    [Fact]
    public void Currency_mismatch_throws() =>
        FluentActions.Invoking(() => JournalDraft.Create("payment:1", "USD",
        [
            new JournalLineDraft(Guid.NewGuid(), EntrySide.Debit, new Money(500, "EUR")),
            Line(Guid.NewGuid(), EntrySide.Credit, 500)
        ])).Should().Throw<ArgumentException>();
}

public class PaymentStatusTests
{
    [Theory]
    [InlineData(PaymentStatus.Initiated, PaymentStatus.Pending)]
    [InlineData(PaymentStatus.Initiated, PaymentStatus.Rejected)]
    [InlineData(PaymentStatus.Initiated, PaymentStatus.Cancelled)]
    [InlineData(PaymentStatus.Pending, PaymentStatus.Processing)]
    [InlineData(PaymentStatus.Pending, PaymentStatus.Cancelled)]
    [InlineData(PaymentStatus.Pending, PaymentStatus.Expired)]
    [InlineData(PaymentStatus.Pending, PaymentStatus.Rejected)]
    [InlineData(PaymentStatus.Processing, PaymentStatus.Completed)]
    [InlineData(PaymentStatus.Processing, PaymentStatus.Failed)]
    [InlineData(PaymentStatus.Completed, PaymentStatus.Reversed)]
    public void Valid_transitions_are_allowed(PaymentStatus from, PaymentStatus to) =>
        PaymentStateMachine.CanTransition(from, to).Should().BeTrue();

    [Theory]
    [InlineData(PaymentStatus.Completed, PaymentStatus.Completed)]
    [InlineData(PaymentStatus.Completed, PaymentStatus.Failed)]
    [InlineData(PaymentStatus.Failed, PaymentStatus.Processing)]
    [InlineData(PaymentStatus.Pending, PaymentStatus.Completed)]
    [InlineData(PaymentStatus.Expired, PaymentStatus.Pending)]
    [InlineData(PaymentStatus.Cancelled, PaymentStatus.Processing)]
    [InlineData(PaymentStatus.Reversed, PaymentStatus.Completed)]
    public void Invalid_transitions_are_rejected(PaymentStatus from, PaymentStatus to) =>
        PaymentStateMachine.CanTransition(from, to).Should().BeFalse();

    [Fact]
    public void Invalid_transition_throws() =>
        FluentActions.Invoking(() => PaymentStateMachine.Transition(PaymentStatus.Completed, PaymentStatus.Pending))
            .Should().Throw<InvalidOperationException>();

    [Fact]
    public void Valid_transition_returns_target() =>
        PaymentStateMachine.Transition(PaymentStatus.Processing, PaymentStatus.Completed)
            .Should().Be(PaymentStatus.Completed);
}
