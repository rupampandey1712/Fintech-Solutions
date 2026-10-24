namespace FinCore.Domain;

public sealed record FraudRuleContext(long AmountMinor, long BeneficiaryAgeSeconds, long AccountAgeSeconds,
    int PaymentsLast10Min, int PaymentsLastHour, int FailedLastHour, int DistinctBeneficiaries24h = 0);

public sealed record FraudRuleDefinition(string Code, long? ThresholdMinor, int Weight, bool Enabled);

public sealed record FraudEvaluationResult(int Score, string Level, string Decision, IReadOnlyList<string> Reasons);

public static class FraudEvaluation
{
    public const long NewBeneficiaryWindowSeconds = 86_400;
    public const long YoungAccountWindowSeconds = 86_400;
    public const long YoungAccountAmountThreshold = 1_000_000;

    public static FraudEvaluationResult Evaluate(IEnumerable<FraudRuleDefinition> rules, FraudRuleContext context)
    {
        var reasons = new List<string>();
        var score = 0;
        foreach (var rule in rules)
        {
            if (!rule.Enabled || !IsTriggered(rule, context)) continue;
            score += rule.Weight;
            reasons.Add(rule.Code);
        }
        score = Math.Min(score, 100);
        return new FraudEvaluationResult(score, LevelFor(score), DecisionFor(score), reasons);
    }

    private static bool IsTriggered(FraudRuleDefinition rule, FraudRuleContext context) => rule.Code switch
    {
        "large_amount" => context.AmountMinor > (rule.ThresholdMinor ?? long.MaxValue),
        "velocity_burst" => context.PaymentsLast10Min > ToCount(rule.ThresholdMinor),
        "new_beneficiary_high_amount" => context.BeneficiaryAgeSeconds < NewBeneficiaryWindowSeconds
            && context.AmountMinor > (rule.ThresholdMinor ?? long.MaxValue),
        "young_account_large_transfer" => context.AccountAgeSeconds < YoungAccountWindowSeconds
            && context.AmountMinor > (rule.ThresholdMinor ?? YoungAccountAmountThreshold),
        "failed_attempts" => context.FailedLastHour > ToCount(rule.ThresholdMinor),
        _ => false
    };

    private static int ToCount(long? thresholdMinor) =>
        thresholdMinor is null or < 0 ? int.MaxValue : (int)Math.Min(thresholdMinor.Value, int.MaxValue);

    public static string LevelFor(int score) =>
        score <= 30 ? "low" : score <= 60 ? "medium" : score <= 80 ? "high" : "critical";

    public static string DecisionFor(int score) =>
        score < 40 ? "allow" : score <= 70 ? "review" : "block";
}
