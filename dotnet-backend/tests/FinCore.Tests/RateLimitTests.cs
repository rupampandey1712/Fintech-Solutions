using System.Security.Claims;
using FinCore.Api;
using FluentAssertions;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging.Abstractions;
using StackExchange.Redis;
using Xunit;

namespace FinCore.Tests;

public class RateLimitTests
{
    private sealed class CountingStore : IRateLimitStore
    {
        private readonly Dictionary<string, long> _counts = new();
        public List<string> Keys { get; } = [];

        public Task<long> IncrementAsync(string key, TimeSpan expiry)
        {
            Keys.Add(key);
            _counts[key] = _counts.GetValueOrDefault(key) + 1;
            return Task.FromResult(_counts[key]);
        }
    }

    private sealed class BrokenStore : IRateLimitStore
    {
        public int Calls { get; private set; }

        public Task<long> IncrementAsync(string key, TimeSpan expiry)
        {
            Calls++;
            throw new RedisException("redis down");
        }
    }

    private static RateLimitMiddleware Middleware(IRateLimitStore? store, Func<DateTimeOffset> clock) =>
        new(_ => Task.CompletedTask, new RateLimitOptions(true, 2, 10, 10, 100, clock, store),
            NullLogger<RateLimitMiddleware>.Instance);

    private static async Task<int> LoginAsync(RateLimitMiddleware middleware)
    {
        var context = new DefaultHttpContext();
        context.Request.Method = "POST";
        context.Request.Path = "/api/v1/auth/login";
        // IPv4-mapped form, as Kestrel reports it on dual-stack sockets; keys must use plain IPv4.
        context.Connection.RemoteIpAddress = System.Net.IPAddress.Parse("::ffff:10.0.0.1");
        context.Response.Body = new MemoryStream();
        await middleware.InvokeAsync(context);
        return context.Response.StatusCode;
    }

    [Fact]
    public async Task Shared_store_counts_with_the_key_scheme_python_uses()
    {
        var store = new CountingStore();
        var middleware = Middleware(store, () => DateTimeOffset.FromUnixTimeSeconds(1_000_020));

        var statuses = new[] { await LoginAsync(middleware), await LoginAsync(middleware), await LoginAsync(middleware) };

        statuses.Should().Equal(200, 200, 429);
        store.Keys[0].Should().Be("fincore:ratelimit:login:ip:10.0.0.1:1000020");
    }

    [Fact]
    public async Task Store_outage_falls_back_to_in_process_limits_and_backs_off()
    {
        var store = new BrokenStore();
        var middleware = Middleware(store, () => DateTimeOffset.FromUnixTimeSeconds(1_000_020));

        var statuses = new[] { await LoginAsync(middleware), await LoginAsync(middleware), await LoginAsync(middleware) };

        statuses.Should().Equal(200, 200, 429);
        store.Calls.Should().Be(1);
    }

    [Fact]
    public void Redis_urls_are_parsed()
    {
        var options = RedisRateLimitStore.ParseOptions("redis://:secret@cache:6380/2");
        options.EndPoints.Single().ToString().Should().Contain("cache").And.Contain("6380");
        options.DefaultDatabase.Should().Be(2);
        options.Password.Should().Be("secret");
        options.AbortOnConnectFail.Should().BeFalse();
    }

    [Fact]
    public async Task Login_budget_is_per_account_and_shares_python_keys()
    {
        var now = DateTimeOffset.FromUnixTimeSeconds(1_000_020);
        var store = new CountingStore();
        var limiter = new RateLimiter(new RateLimitOptions(true, 5, 10, 10, 100, () => now, store,
            LoginPerAccountPerMinute: 2), NullLogger.Instance);
        var alice = RateLimiter.AccountPrincipal("alice@example.com");
        // The Python API derives the same principal (test_rate_limit.py), so budgets are shared.
        alice.Should().Be("account:ff8d9819fc0e12bf0d24892e45987e24");

        var attempts = new List<bool>();
        for (var i = 0; i < 3; i++) attempts.Add(await limiter.TryAcquireAsync("login_account", alice, 2));
        attempts.Should().Equal(true, true, false);
        (await limiter.TryAcquireAsync("login_account", RateLimiter.AccountPrincipal("bob@example.com"), 2))
            .Should().BeTrue("other accounts keep their own budget");
        store.Keys[0].Should().Be($"fincore:ratelimit:login_account:{alice}:1000020");
    }
}
