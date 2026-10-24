using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Serilog;
using StackExchange.Redis;

namespace FinCore.Infrastructure;

/// <summary>
/// Redis cache-aside for slow-changing configuration (fraud rules, account limits).
/// Read: try Redis, on a miss load from PostgreSQL and store with a TTL. Write: commit first, then
/// delete the key — deleting before the commit would let a concurrent reader re-cache the old row.
/// Keys and JSON shapes match the Python API, so an invalidation by either implementation is seen
/// by both. Redis is an optimisation, never a dependency: when it is unreachable, reads fall
/// through to the database and Redis is retried after a back-off; the TTL bounds staleness if an
/// invalidation fails. Never cached: balances, and anything used to authenticate or authorise a
/// request (customer status, roles).
/// </summary>
public sealed class FinCoreCache(IConnectionMultiplexer? redis, Func<DateTimeOffset>? clock = null)
{
    public const string FraudRulesKey = "fincore:cache:v1:fraud-rules";
    public static readonly TimeSpan FraudRulesTtl = TimeSpan.FromSeconds(60);
    public static readonly TimeSpan AccountLimitsTtl = TimeSpan.FromSeconds(600);
    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(30);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
    private DateTimeOffset _retryAt = DateTimeOffset.MinValue;

    public long Hits => Interlocked.Read(ref _hits);
    public long Misses => Interlocked.Read(ref _misses);
    private long _hits;
    private long _misses;

    public static string AccountLimitsKey(Guid accountId) => $"fincore:cache:v1:account:{accountId}:limits";

    private bool Available => redis is not null && _clock() >= _retryAt;

    public async Task<T?> GetOrLoadAsync<T>(string key, TimeSpan ttl, Func<Task<T?>> loader) where T : class
    {
        if (Available)
        {
            try
            {
                var cached = await redis!.GetDatabase().StringGetAsync(key);
                if (cached.HasValue)
                {
                    Interlocked.Increment(ref _hits);
                    Record("hit", key);
                    return JsonSerializer.Deserialize<T>(cached.ToString(), JsonOptions);
                }
            }
            catch (Exception exception) when (exception is RedisException or TimeoutException or JsonException)
            {
                Degrade("get", exception);
            }
        }
        Interlocked.Increment(ref _misses);
        Record("miss", key);
        var value = await loader();
        if (value is not null && Available)
        {
            try
            {
                await redis!.GetDatabase().StringSetAsync(key, JsonSerializer.Serialize(value, JsonOptions), ttl);
            }
            catch (Exception exception) when (exception is RedisException or TimeoutException)
            {
                Degrade("set", exception);
            }
        }
        return value;
    }

    public async Task InvalidateAsync(params string[] keys)
    {
        if (redis is null || keys.Length == 0) return;
        try
        {
            await redis.GetDatabase().KeyDeleteAsync(keys.Select(key => (RedisKey)key).ToArray());
        }
        catch (Exception exception) when (exception is RedisException or TimeoutException)
        {
            // The entry stays stale until its TTL expires; log loudly so it is visible.
            Log.Error(exception, "Cache invalidation failed for {Keys}", keys);
        }
    }

    // The key family rather than the key itself, so label values stay bounded.
    private static void Record(string result, string key) =>
        FinCoreTelemetry.CacheRequestsTotal.Add(1, new KeyValuePair<string, object?>("result", result),
            new KeyValuePair<string, object?>("cache", key.EndsWith(":limits") ? "account-limits" : key[(key.LastIndexOf(':') + 1)..]));

    private void Degrade(string operation, Exception exception)
    {
        _retryAt = _clock() + RetryDelay;
        Log.Warning("Cache unavailable during {Operation} ({ErrorType}); using the database for {Seconds}s",
            operation, exception.GetType().Name, RetryDelay.TotalSeconds);
    }
}

/// <summary>Cached fraud rule; same JSON shape as the Python API (camelCase).</summary>
public sealed record CachedFraudRule(string Code, string Description, long? ThresholdMinor, int Weight, bool Enabled);

/// <summary>Configured caps only. Running totals are always recomputed from the database.</summary>
public sealed record CachedAccountLimits(long PerTransactionMinor, long DailyMinor, long WeeklyMinor, long MonthlyMinor);

/// <summary>Cached reads of payment configuration. A null cache reads straight from the database.</summary>
public static class PaymentConfiguration
{
    public static async Task<IReadOnlyList<CachedFraudRule>> FraudRulesAsync(FinCoreDbContext db, FinCoreCache? cache,
        CancellationToken cancellationToken)
    {
        Task<List<CachedFraudRule>?> Load() => db.FraudRules.AsNoTracking().OrderBy(rule => rule.Code)
            .Select(rule => new CachedFraudRule(rule.Code, rule.Description, rule.ThresholdMinor, rule.Weight, rule.Enabled))
            .ToListAsync(cancellationToken)!;
        return cache is null
            ? (await Load())!
            : await cache.GetOrLoadAsync(FinCoreCache.FraudRulesKey, FinCoreCache.FraudRulesTtl, Load) ?? [];
    }

    /// <summary>
    /// Limits are written once when the account opens; anything that changes them later must call
    /// <see cref="FinCoreCache.InvalidateAsync"/> with <see cref="FinCoreCache.AccountLimitsKey"/> after committing.
    /// </summary>
    public static async Task<CachedAccountLimits?> AccountLimitsAsync(FinCoreDbContext db, FinCoreCache? cache,
        Guid accountId, CancellationToken cancellationToken)
    {
        Task<CachedAccountLimits?> Load() => db.AccountLimits.AsNoTracking().Where(item => item.AccountId == accountId)
            .Select(item => new CachedAccountLimits(item.PerTransactionMinor, item.DailyMinor, item.WeeklyMinor, item.MonthlyMinor))
            .SingleOrDefaultAsync(cancellationToken);
        // A missing row is not cached (the loader returns null), so a later limit row is seen at once.
        return cache is null
            ? await Load()
            : await cache.GetOrLoadAsync(FinCoreCache.AccountLimitsKey(accountId), FinCoreCache.AccountLimitsTtl, Load);
    }
}
