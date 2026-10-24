using FinCore.Api;
using FinCore.Infrastructure;
using FluentAssertions;
using StackExchange.Redis;
using Xunit;

namespace FinCore.Tests;

public sealed class RequiresRedisFactAttribute : FactAttribute
{
    public RequiresRedisFactAttribute()
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("FINCORE_TEST_REDIS_URL")))
            Skip = "FINCORE_TEST_REDIS_URL is not set.";
    }
}

public class CacheTests
{
    private static IConnectionMultiplexer Connect(string? url = null) =>
        ConnectionMultiplexer.Connect(RedisRateLimitStore.ParseOptions(url ?? Environment.GetEnvironmentVariable("FINCORE_TEST_REDIS_URL")!));

    private sealed class Loader<T>(T? value) where T : class
    {
        public int Calls { get; private set; }
        public T? Value { get; set; } = value;

        public Task<T?> LoadAsync()
        {
            Calls++;
            return Task.FromResult(Value);
        }
    }

    private sealed record Sample(int V);

    [Fact]
    public async Task Without_redis_every_read_goes_to_the_loader()
    {
        var cache = new FinCoreCache(null);
        var loader = new Loader<Sample>(new Sample(1));
        await cache.GetOrLoadAsync("k", TimeSpan.FromSeconds(5), loader.LoadAsync);
        await cache.GetOrLoadAsync("k", TimeSpan.FromSeconds(5), loader.LoadAsync);
        loader.Calls.Should().Be(2);
        await cache.InvalidateAsync("k"); // no-op, must not throw
    }

    [RequiresRedisFact]
    public async Task Cache_aside_loads_once_and_invalidation_forces_a_reload()
    {
        using var redis = Connect();
        var cache = new FinCoreCache(redis);
        var key = $"fincore:test:{Guid.NewGuid():N}";
        var loader = new Loader<Sample>(new Sample(1));

        (await cache.GetOrLoadAsync(key, TimeSpan.FromSeconds(30), loader.LoadAsync))!.V.Should().Be(1);
        (await cache.GetOrLoadAsync(key, TimeSpan.FromSeconds(30), loader.LoadAsync))!.V.Should().Be(1);
        loader.Calls.Should().Be(1);
        (await redis.GetDatabase().KeyTimeToLiveAsync(key)).Should().NotBeNull();

        loader.Value = new Sample(2);
        await cache.InvalidateAsync(key);
        (await cache.GetOrLoadAsync(key, TimeSpan.FromSeconds(30), loader.LoadAsync))!.V.Should().Be(2);
    }

    [RequiresRedisFact]
    public async Task Missing_values_are_not_cached()
    {
        using var redis = Connect();
        var key = $"fincore:test:{Guid.NewGuid():N}";
        await new FinCoreCache(redis).GetOrLoadAsync(key, TimeSpan.FromSeconds(30), new Loader<Sample>(null).LoadAsync);
        (await redis.GetDatabase().KeyExistsAsync(key)).Should().BeFalse();
    }

    [Fact]
    public async Task Unreachable_redis_falls_back_to_the_loader()
    {
        // Nothing listens on this port; AbortOnConnectFail=false means Connect returns at once.
        using var redis = Connect("redis://127.0.0.1:1/0");
        var cache = new FinCoreCache(redis);
        var loader = new Loader<Sample>(new Sample(7));
        for (var i = 0; i < 3; i++)
            (await cache.GetOrLoadAsync("k", TimeSpan.FromSeconds(5), loader.LoadAsync))!.V.Should().Be(7);
        loader.Calls.Should().Be(3);
        await cache.InvalidateAsync("k"); // logged, not thrown
    }

    [RequiresRedisFact]
    public async Task Reads_fraud_rules_cached_by_the_python_api()
    {
        using var redis = Connect();
        // Exactly what python-backend/app/services/configuration.py stores (json.dumps of the list).
        const string pythonJson =
            """[{"code": "large_amount", "description": "Large", "thresholdMinor": 5000000, "weight": 30, "enabled": true}, {"code": "velocity_burst", "description": "Burst", "thresholdMinor": null, "weight": 25, "enabled": false}]""";
        await redis.GetDatabase().StringSetAsync(FinCoreCache.FraudRulesKey, pythonJson, TimeSpan.FromSeconds(30));
        try
        {
            var rules = await PaymentConfiguration.FraudRulesAsync(null!, new FinCoreCache(redis), default);
            rules.Should().BeEquivalentTo(new[]
            {
                new CachedFraudRule("large_amount", "Large", 5_000_000, 30, true),
                new CachedFraudRule("velocity_burst", "Burst", null, 25, false)
            });
        }
        finally
        {
            await redis.GetDatabase().KeyDeleteAsync(FinCoreCache.FraudRulesKey);
        }
    }
}
