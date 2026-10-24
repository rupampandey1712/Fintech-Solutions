using FinCore.Application;
using FinCore.Domain;
using FluentAssertions;
using Xunit;

namespace FinCore.Tests;

public class FraudEvaluationTests
{
    private static FraudRuleDefinition Rule(string code, long? thresholdMinor, int weight, bool enabled = true) =>
        new(code, thresholdMinor, weight, enabled);

    private static FraudRuleContext Context(
        long amountMinor = 100,
        long beneficiaryAgeSeconds = long.MaxValue / 4,
        long accountAgeSeconds = long.MaxValue / 4,
        int paymentsLast10Min = 0,
        int paymentsLastHour = 0,
        int failedLastHour = 0) => new(amountMinor, beneficiaryAgeSeconds, accountAgeSeconds,
        paymentsLast10Min, paymentsLastHour, failedLastHour);

    private static readonly FraudRuleDefinition[] Defaults =
    [
        Rule("large_amount", 5_000_000, 30),
        Rule("velocity_burst", 3, 25),
        Rule("new_beneficiary_high_amount", 2_000_000, 20),
        Rule("young_account_large_transfer", 1_000_000, 25),
        Rule("failed_attempts", 3, 20)
    ];

    [Fact]
    public void No_triggers_is_low_and_allow()
    {
        var result = FraudEvaluation.Evaluate(Defaults, Context());
        result.Score.Should().Be(0);
        result.Level.Should().Be("low");
        result.Decision.Should().Be("allow");
        result.Reasons.Should().BeEmpty();
    }

    [Fact]
    public void Score_30_is_low_and_allow()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 30)], Context(amountMinor: 101));
        result.Score.Should().Be(30);
        result.Level.Should().Be("low");
        result.Decision.Should().Be("allow");
    }

    [Fact]
    public void Score_31_is_medium()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 31)], Context(amountMinor: 101));
        result.Score.Should().Be(31);
        result.Level.Should().Be("medium");
        result.Decision.Should().Be("allow");
    }

    [Fact]
    public void Score_40_is_review()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 40)], Context(amountMinor: 101));
        result.Decision.Should().Be("review");
    }

    [Fact]
    public void Score_70_is_review()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 70)], Context(amountMinor: 101));
        result.Decision.Should().Be("review");
    }

    [Fact]
    public void Score_71_is_block()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 71)], Context(amountMinor: 101));
        result.Decision.Should().Be("block");
        result.Level.Should().Be("high");
    }

    [Fact]
    public void Score_is_capped_at_100()
    {
        var result = FraudEvaluation.Evaluate(Defaults, Context(
            amountMinor: 5_000_001,
            beneficiaryAgeSeconds: 0,
            accountAgeSeconds: 0,
            paymentsLast10Min: 4,
            failedLastHour: 4));
        result.Score.Should().Be(100);
        result.Level.Should().Be("critical");
        result.Reasons.Should().HaveCount(5);
    }

    [Fact]
    public void Thresholds_are_strictly_greater_than()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 5_000_000, 30)], Context(amountMinor: 5_000_000));
        result.Reasons.Should().BeEmpty();
    }

    [Fact]
    public void Velocity_counts_over_threshold_trigger()
    {
        var result = FraudEvaluation.Evaluate([Rule("velocity_burst", 3, 25)], Context(paymentsLast10Min: 3));
        result.Reasons.Should().BeEmpty();
        FraudEvaluation.Evaluate([Rule("velocity_burst", 3, 25)], Context(paymentsLast10Min: 4))
            .Reasons.Should().ContainSingle().Which.Should().Be("velocity_burst");
    }

    [Fact]
    public void New_beneficiary_requires_both_conditions()
    {
        var youngBeneficiary = Context(amountMinor: 2_000_001, beneficiaryAgeSeconds: 86_399);
        FraudEvaluation.Evaluate(Defaults, youngBeneficiary).Reasons.Should().Contain("new_beneficiary_high_amount");

        var oldBeneficiary = Context(amountMinor: 2_000_001, beneficiaryAgeSeconds: 86_400);
        FraudEvaluation.Evaluate(Defaults, oldBeneficiary).Reasons.Should().NotContain("new_beneficiary_high_amount");

        var youngAndSmall = Context(amountMinor: 100, beneficiaryAgeSeconds: 10);
        FraudEvaluation.Evaluate(Defaults, youngAndSmall).Reasons.Should().NotContain("new_beneficiary_high_amount");
    }

    [Fact]
    public void Young_account_requires_both_conditions()
    {
        var youngAccount = Context(amountMinor: 1_000_001, accountAgeSeconds: 86_399);
        FraudEvaluation.Evaluate(Defaults, youngAccount).Reasons.Should().Contain("young_account_large_transfer");

        var oldAccount = Context(amountMinor: 1_000_001, accountAgeSeconds: 86_400);
        FraudEvaluation.Evaluate(Defaults, oldAccount).Reasons.Should().NotContain("young_account_large_transfer");
    }

    [Fact]
    public void Disabled_rules_are_ignored()
    {
        var result = FraudEvaluation.Evaluate([Rule("large_amount", 100, 50, enabled: false)], Context(amountMinor: 200));
        result.Score.Should().Be(0);
    }

    [Fact]
    public void Unknown_rules_are_ignored()
    {
        var result = FraudEvaluation.Evaluate([Rule("unknown_rule", 1, 50)], Context(amountMinor: 100));
        result.Score.Should().Be(0);
    }
}

public class AmlRulesTests
{
    [Fact]
    public void Structuring_triggers_at_three_payments_last_hour() =>
        AmlRules.Evaluate(3, 0, 100).Should().Contain("structuring");

    [Fact]
    public void Structuring_does_not_trigger_below_three() =>
        AmlRules.Evaluate(2, 0, 100).Should().NotContain("structuring");

    [Fact]
    public void Rapid_movement_triggers_at_five_payments_last_10_min() =>
        AmlRules.Evaluate(0, 5, 100).Should().Contain("rapid_movement");

    [Fact]
    public void Rapid_movement_does_not_trigger_below_five() =>
        AmlRules.Evaluate(0, 4, 100).Should().NotContain("rapid_movement");

    [Fact]
    public void Unusual_amount_triggers_above_threshold() =>
        AmlRules.Evaluate(0, 0, 5_000_001).Should().Contain("unusual_amount");

    [Fact]
    public void Unusual_amount_supports_custom_threshold()
    {
        AmlRules.Evaluate(0, 0, 1_001, 1_000).Should().Contain("unusual_amount");
        AmlRules.Evaluate(0, 0, 1_000, 1_000).Should().NotContain("unusual_amount");
    }

    [Fact]
    public void All_rules_can_trigger_together() =>
        AmlRules.Evaluate(5, 6, 6_000_000).Should().BeEquivalentTo(
            ["structuring", "rapid_movement", "unusual_amount"]);
}

public class SanctionsListTests
{
    [Theory]
    [InlineData("john doe")]
    [InlineData("John Doe")]
    [InlineData("  JOHN DOE  ")]
    [InlineData("jane roe")]
    [InlineData("viktor alergi")]
    public void Default_names_match(string fullName) =>
        SanctionsList.ScreenName(fullName).Should().Be(SanctionsList.PotentialMatch);

    [Theory]
    [InlineData("John Smith")]
    [InlineData("john doe2")]
    [InlineData("")]
    public void Unknown_names_are_clear(string fullName) =>
        SanctionsList.ScreenName(fullName).Should().Be(SanctionsList.Clear);

    [Fact]
    public void Additional_names_are_merged() =>
        SanctionsList.ScreenName("ACME Ltd", ["acme ltd"]).Should().Be(SanctionsList.PotentialMatch);
}

public class CursorCodecTests
{
    [Fact]
    public void Roundtrip_preserves_timestamp_and_id()
    {
        var timestamp = DateTimeOffset.Parse("2026-01-02T03:04:05.678+00:00");
        var id = Guid.Parse("0b9e6b6e-8f0a-4a1e-9c2d-3f4a5b6c7d8e");
        var decoded = CursorCodec.TryDecode(CursorCodec.Encode(timestamp, id), out var outAt, out var outId);
        decoded.Should().BeTrue();
        outAt.Should().Be(timestamp);
        outId.Should().Be(id);
    }

    [Theory]
    [InlineData("not-base64!!!")]
    [InlineData("aGVsbG8=")] // "hello" without separator
    public void Invalid_cursors_are_rejected(string cursor)
    {
        CursorCodec.TryDecode(cursor, out _, out _).Should().BeFalse();
    }
}
