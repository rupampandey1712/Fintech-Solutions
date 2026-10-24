namespace FinCore.Domain;

public static class SanctionsList
{
    public const string Clear = "clear";
    public const string PotentialMatch = "potential_match";

    public static readonly IReadOnlyCollection<string> DefaultNames = ["john doe", "jane roe", "viktor alergi"];

    public static string ScreenName(string fullName, IEnumerable<string>? additionalNames = null)
    {
        var candidate = fullName.Trim().ToLowerInvariant();
        foreach (var name in Names(additionalNames))
        {
            if (candidate == name) return PotentialMatch;
        }
        return Clear;
    }

    private static IEnumerable<string> Names(IEnumerable<string>? additionalNames)
    {
        foreach (var name in DefaultNames) yield return name;
        if (additionalNames is null) yield break;
        foreach (var name in additionalNames)
        {
            var trimmed = name.Trim().ToLowerInvariant();
            if (trimmed.Length > 0) yield return trimmed;
        }
    }
}
