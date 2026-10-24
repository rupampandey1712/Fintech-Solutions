using System.Collections.Concurrent;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using StackExchange.Redis;

namespace FinCore.Api;

public sealed record RateLimitOptions(bool Enabled, int LoginPerMinute, int RegisterPerMinute, int PaymentPerMinute,
    int DefaultPerMinute, Func<DateTimeOffset> Clock, IRateLimitStore? SharedStore = null,
    int LoginPerAccountPerMinute = 10, int AgentPerMinute = 5);

/// <summary>Shared counters for rate-limit windows (Redis in deployments).</summary>
public interface IRateLimitStore
{
    /// <summary>Increments the window counter, setting its expiry on first use, and returns the new count.</summary>
    Task<long> IncrementAsync(string key, TimeSpan expiry);
}

public sealed class RedisRateLimitStore(IConnectionMultiplexer redis) : IRateLimitStore
{
    // INCR and EXPIRE in one round trip, atomically. The Python API runs the same script on the
    // same keys, so both implementations share one budget per principal.
    private const string IncrementScript = """
        local count = redis.call('INCR', KEYS[1])
        if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
        return count
        """;

    public async Task<long> IncrementAsync(string key, TimeSpan expiry) =>
        (long)await redis.GetDatabase().ScriptEvaluateAsync(IncrementScript, [key], [(long)expiry.TotalSeconds]);

    /// <summary>Accepts "redis://host:port/db" (the shared REDIS_URL format) or a StackExchange.Redis string.</summary>
    public static ConfigurationOptions ParseOptions(string value)
    {
        ConfigurationOptions options;
        if (Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme is "redis" or "rediss")
        {
            options = new ConfigurationOptions { Ssl = uri.Scheme == "rediss" };
            options.EndPoints.Add(uri.Host, uri.IsDefaultPort || uri.Port < 0 ? 6379 : uri.Port);
            if (int.TryParse(uri.AbsolutePath.Trim('/'), out var database)) options.DefaultDatabase = database;
            if (!string.IsNullOrEmpty(uri.UserInfo))
            {
                var parts = uri.UserInfo.Split(':', 2);
                options.Password = Uri.UnescapeDataString(parts.Length == 2 ? parts[1] : parts[0]);
            }
        }
        else
        {
            options = ConfigurationOptions.Parse(value);
        }
        // Never block startup or a request on Redis: fail fast and let the limiter fall back.
        options.AbortOnConnectFail = false;
        options.ConnectTimeout = 250;
        options.SyncTimeout = 250;
        options.AsyncTimeout = 250;
        return options;
    }
}

// Fixed one-minute windows per (bucket, principal). With a shared store (Redis) every replica
// shares the counters; if the store is unreachable the limiter falls back to in-process counters
// and retries the store after a back-off, so an outage degrades limits instead of failing requests.
// One instance serves the middleware and handlers that need a budget the middleware cannot key
// (login attempts per account).
public sealed class RateLimiter(RateLimitOptions options, ILogger logger)
{
    public const int WindowSeconds = 60;
    private static readonly TimeSpan StoreRetryDelay = TimeSpan.FromSeconds(30);
    private sealed record Window(long StartEpochSeconds, int Count);

    private readonly ConcurrentDictionary<string, Window> _windows = new();
    private DateTimeOffset _storeRetryAt = DateTimeOffset.MinValue;

    public RateLimitOptions Options => options;

    /// <summary>
    /// The per-account login principal. Hashed so Redis keys never carry an email address; the
    /// Python API derives the same value, so both implementations share one budget per account.
    /// </summary>
    public static string AccountPrincipal(string normalizedEmail) =>
        "account:" + Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(normalizedEmail)))[..32];

    public async Task<bool> TryAcquireAsync(string bucket, string principal, int limit)
    {
        var now = options.Clock();
        var windowStart = now.ToUnixTimeSeconds() / WindowSeconds * WindowSeconds;
        if (options.SharedStore is { } store && now >= _storeRetryAt)
        {
            try
            {
                var key = $"fincore:ratelimit:{bucket}:{principal}:{windowStart}";
                return await store.IncrementAsync(key, TimeSpan.FromSeconds(WindowSeconds + 5)) <= limit;
            }
            catch (Exception exception) when (exception is RedisException or TimeoutException or ObjectDisposedException)
            {
                _storeRetryAt = now + StoreRetryDelay;
                logger.LogWarning("Rate-limit store unavailable ({ErrorType}); using in-process counters for {Seconds}s",
                    exception.GetType().Name, StoreRetryDelay.TotalSeconds);
            }
        }
        return AcquireLocal($"{bucket}:{principal}", windowStart, limit);
    }

    private bool AcquireLocal(string key, long windowStart, int limit)
    {
        int count;
        lock (_windows)
        {
            var current = _windows.GetOrAdd(key, _ => new Window(windowStart, 0));
            if (current.StartEpochSeconds != windowStart) current = new Window(windowStart, 0);
            _windows[key] = current with { Count = current.Count + 1 };
            count = current.Count + 1;
            // Drop expired windows so one-off principals (IPs, customers) do not accumulate forever.
            if (_windows.Count > 100_000)
            {
                foreach (var stale in _windows.Where(pair => pair.Value.StartEpochSeconds != windowStart).ToList())
                    _windows.TryRemove(stale.Key, out _);
            }
        }
        return count <= limit;
    }
}

// Applies the per-(bucket, principal) budgets to every API request. Principal is the
// authenticated customer when present, otherwise the remote IP.
public sealed class RateLimitMiddleware(RequestDelegate next, RateLimitOptions options, ILogger<RateLimitMiddleware> logger,
    RateLimiter? limiter = null)
{
    private const int WindowSeconds = RateLimiter.WindowSeconds;
    private readonly RateLimiter _limiter = limiter ?? new RateLimiter(options, logger);

    public async Task InvokeAsync(HttpContext context)
    {
        if (options.Enabled)
        {
            var (bucket, limit) = Resolve(context);
            if (limit > 0 && !await _limiter.TryAcquireAsync(bucket, PrincipalKey(context), limit))
            {
                await WriteTooManyRequestsAsync(context);
                return;
            }
        }
        await next(context);
    }

    // Bucket names and principal prefixes match the Python API so shared Redis keys line up.
    private (string Bucket, int Limit) Resolve(HttpContext context)
    {
        var path = context.Request.Path.Value ?? string.Empty;
        var isPost = HttpMethods.IsPost(context.Request.Method);
        if (isPost && path.Equals("/api/v1/auth/login", StringComparison.OrdinalIgnoreCase))
            return ("login", options.LoginPerMinute);
        if (isPost && path.Equals("/api/v1/auth/register", StringComparison.OrdinalIgnoreCase))
            return ("register", options.RegisterPerMinute);
        if (isPost && path.Equals("/api/v1/payments", StringComparison.OrdinalIgnoreCase))
            return ("payment", options.PaymentPerMinute);
        // Each investigation calls a paid model API several times, so it gets its own small budget.
        if (isPost && path.StartsWith("/api/v1/fraud/cases/", StringComparison.OrdinalIgnoreCase)
            && path.EndsWith("/investigate", StringComparison.OrdinalIgnoreCase))
            return ("agent", options.AgentPerMinute);
        if (path.StartsWith("/api/", StringComparison.OrdinalIgnoreCase))
            return ("default", options.DefaultPerMinute);
        return ("none", 0);
    }

    private static string PrincipalKey(HttpContext context)
    {
        if (context.User.Identity?.IsAuthenticated == true)
        {
            var subject = context.User.FindFirstValue(ClaimTypes.NameIdentifier)
                ?? context.User.FindFirstValue("sub");
            if (!string.IsNullOrEmpty(subject)) return $"customer:{subject}";
        }
        // Kestrel reports IPv4 clients on dual-stack sockets as "::ffff:a.b.c.d"; Python sees
        // "a.b.c.d". Normalise so both implementations count the same client under one key.
        var address = context.Connection.RemoteIpAddress;
        if (address is { IsIPv4MappedToIPv6: true }) address = address.MapToIPv4();
        return $"ip:{address?.ToString() ?? "anonymous"}";
    }

    private static async Task WriteTooManyRequestsAsync(HttpContext context)
    {
        context.Response.StatusCode = StatusCodes.Status429TooManyRequests;
        context.Response.ContentType = "application/problem+json";
        context.Response.Headers.RetryAfter = WindowSeconds.ToString();
        var problem = new
        {
            type = "https://fincore.local/problems/rate_limited",
            title = "Too many requests",
            status = 429,
            code = "rate_limited",
            detail = "Rate limit exceeded. Retry later.",
            traceId = context.TraceIdentifier
        };
        await context.Response.WriteAsync(JsonSerializer.Serialize(problem, new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase
        }));
    }
}
