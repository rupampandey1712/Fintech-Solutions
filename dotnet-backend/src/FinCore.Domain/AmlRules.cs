namespace FinCore.Domain;

public static class AmlRules
{
    public const string Structuring = "structuring";
    public const string RapidMovement = "rapid_movement";
    public const string UnusualAmount = "unusual_amount";
    public const string MultipleBeneficiaries = "multiple_beneficiaries";

    public const int MultipleBeneficiariesThreshold = 5;

    public static IReadOnlyList<string> Evaluate(int paymentsLastHour, int paymentsLast10Min, long amountMinor,
        long unusualAmountThresholdMinor = 5_000_000, int distinctBeneficiaries24h = 0,
        int multipleBeneficiariesThreshold = MultipleBeneficiariesThreshold)
    {
        List<string> triggered = [];
        if (paymentsLastHour >= 3) triggered.Add(Structuring);
        if (paymentsLast10Min >= 5) triggered.Add(RapidMovement);
        if (amountMinor > unusualAmountThresholdMinor) triggered.Add(UnusualAmount);
        if (distinctBeneficiaries24h >= multipleBeneficiariesThreshold) triggered.Add(MultipleBeneficiaries);
        return triggered;
    }
}
