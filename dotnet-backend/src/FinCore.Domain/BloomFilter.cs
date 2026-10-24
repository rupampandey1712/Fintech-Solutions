using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace FinCore.Domain;

/// <summary>
/// A compact set that answers "definitely not present" or "probably present". Used to refuse
/// common passwords at registration: a filter for a million passwords at a 0.1% false-positive
/// rate needs about 1.8 MB instead of tens of megabytes. A false positive only asks a user to pick
/// a different password, the safe direction; there are no false negatives.
/// </summary>
/// <remarks>
/// Bit positions use double hashing over SHA-256 (Kirsch-Mitzenmacher): two 64-bit values h1, h2
/// from the digest give position i as (h1 + i * h2) mod m, with the modulo applied to each term
/// first so the arithmetic fits in 64 bits. The Python implementation (app.domain.bloom) computes
/// exactly the same positions.
/// </remarks>
public sealed class BloomFilter
{
    private readonly byte[] _bits;

    public BloomFilter(int bitCount, int hashCount)
    {
        if (bitCount < 1 || hashCount < 1)
            throw new ArgumentException("A Bloom filter needs at least one bit and one hash.");
        BitCount = bitCount;
        HashCount = hashCount;
        _bits = new byte[(bitCount + 7) / 8];
    }

    public int BitCount { get; }
    public int HashCount { get; }

    /// <summary>Sizes the filter optimally: m = -n ln(p) / (ln 2)^2 bits, k = (m / n) ln 2 hashes.</summary>
    public static BloomFilter ForCapacity(int expectedItems, double falsePositiveRate)
    {
        if (falsePositiveRate is <= 0 or >= 1)
            throw new ArgumentOutOfRangeException(nameof(falsePositiveRate), "Must be between 0 and 1.");
        var n = Math.Max(1, expectedItems);
        var bits = (int)Math.Ceiling(-n * Math.Log(falsePositiveRate) / (Math.Log(2) * Math.Log(2)));
        var hashes = Math.Max(1, (int)Math.Round((double)bits / n * Math.Log(2), MidpointRounding.ToEven));
        return new BloomFilter(bits, hashes);
    }

    public static BloomFilter Of(IReadOnlyCollection<string> items, double falsePositiveRate)
    {
        var bloom = ForCapacity(items.Count, falsePositiveRate);
        foreach (var item in items) bloom.Add(item);
        return bloom;
    }

    public IEnumerable<int> Positions(string item)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(item));
        var m = (ulong)BitCount;
        var h1 = BinaryPrimitives.ReadUInt64BigEndian(digest.AsSpan(0, 8)) % m;
        // Odd, so the probe sequence does not collapse when h2 shares a factor with m.
        var h2 = (BinaryPrimitives.ReadUInt64BigEndian(digest.AsSpan(8, 8)) | 1) % m;
        for (var index = 0UL; index < (ulong)HashCount; index++)
            yield return (int)((h1 + index * h2) % m);
    }

    public void Add(string item)
    {
        foreach (var position in Positions(item))
            _bits[position >> 3] |= (byte)(1 << (position & 7));
    }

    public bool MightContain(string item) =>
        Positions(item).All(position => (_bits[position >> 3] & (1 << (position & 7))) != 0);
}
