using FinCore.Application;
using FinCore.Domain;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace FinCore.Infrastructure;

/// <summary>
/// Common-password screening backed by a <see cref="BloomFilter"/>, built once at startup from
/// COMMON_PASSWORDS_PATH or, when unset, the bundled list (embedded in this assembly; the Python
/// API ships an identical copy).
/// </summary>
public sealed class CommonPasswordList : ICommonPasswordCheck
{
    public const double FalsePositiveRate = 0.001;
    private const string BundledResource = "FinCore.Infrastructure.Resources.common_passwords.txt";

    private readonly BloomFilter _filter;

    public CommonPasswordList(IEnumerable<string> lines)
    {
        var entries = lines.Where(line => line.Trim().Length > 0 && !line.StartsWith('#'))
            .Select(Normalize).ToArray();
        _filter = BloomFilter.Of(entries, FalsePositiveRate);
        EntryCount = entries.Length;
    }

    public int EntryCount { get; }
    public int BitCount => _filter.BitCount;
    public int HashCount => _filter.HashCount;

    /// <summary>Case-insensitive: "Password1234" is as guessable as "password1234".</summary>
    public static string Normalize(string password) => password.Trim().ToLowerInvariant();

    public bool IsCommon(string password) => _filter.MightContain(Normalize(password));

    public static CommonPasswordList Load(IConfiguration configuration, ILogger logger)
    {
        var configured = configuration["COMMON_PASSWORDS_PATH"];
        var list = new CommonPasswordList(string.IsNullOrWhiteSpace(configured) ? ReadBundled() : File.ReadLines(configured));
        logger.LogInformation("Common-password filter loaded: {Entries} entries, {Bits} bits, {Hashes} hashes, bundled={Bundled}",
            list.EntryCount, list.BitCount, list.HashCount, string.IsNullOrWhiteSpace(configured));
        return list;
    }

    public static IEnumerable<string> ReadBundled()
    {
        using var stream = typeof(CommonPasswordList).Assembly.GetManifestResourceStream(BundledResource)
            ?? throw new InvalidOperationException($"Embedded resource {BundledResource} is missing.");
        using var reader = new StreamReader(stream);
        var lines = new List<string>();
        while (reader.ReadLine() is { } line) lines.Add(line);
        return lines;
    }
}
