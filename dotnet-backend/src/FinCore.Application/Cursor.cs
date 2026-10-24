using System.Globalization;
using System.Text;

namespace FinCore.Application;

public static class CursorCodec
{
    public static string Encode(DateTimeOffset timestamp, Guid id) =>
        Convert.ToBase64String(Encoding.UTF8.GetBytes($"{timestamp:O}|{id}"));

    public static bool TryDecode(string? cursor, out DateTimeOffset timestamp, out Guid id)
    {
        timestamp = default;
        id = default;
        if (string.IsNullOrWhiteSpace(cursor)) return false;
        string decoded;
        try
        {
            decoded = Encoding.UTF8.GetString(Convert.FromBase64String(cursor));
        }
        catch (FormatException)
        {
            return false;
        }
        var separator = decoded.IndexOf('|');
        if (separator <= 0) return false;
        return DateTimeOffset.TryParse(decoded[..separator], CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind, out timestamp)
            && Guid.TryParse(decoded[(separator + 1)..], out id);
    }
}

public static class JsonText
{
    public static IReadOnlyList<string> ParseStringArray(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];
        try
        {
            return System.Text.Json.JsonSerializer.Deserialize<string[]>(json) ?? [];
        }
        catch (System.Text.Json.JsonException)
        {
            return [];
        }
    }
}
