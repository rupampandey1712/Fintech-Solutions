namespace FinCore.Api;

/// <summary>
/// Helmet-equivalent security response headers (docs/SECURITY_HEADERS.md). The exact same set,
/// with the exact same values, is applied by the Python implementation, so an attacker comparing
/// response headers across the two backends learns nothing about which stack answered.
/// Registered first, so the headers ride on every response, whatever produced it (action result,
/// exception handler, authentication challenge, rate-limit refusal).
/// </summary>
public sealed class SecurityHeadersMiddleware(RequestDelegate next)
{
    private static readonly IReadOnlyDictionary<string, string> Headers =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["X-Content-Type-Options"] = "nosniff",
            ["X-Frame-Options"] = "DENY",
            ["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains",
            ["Content-Security-Policy"] =
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                + "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
                + "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            ["Referrer-Policy"] = "no-referrer",
            ["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()",
            ["Cross-Origin-Opener-Policy"] = "same-origin",
            ["Cross-Origin-Resource-Policy"] = "same-origin",
            ["Origin-Agent-Cluster"] = "?1",
            ["X-DNS-Prefetch-Control"] = "off"
        };

    public async Task InvokeAsync(HttpContext context)
    {
        foreach (var (name, value) in Headers)
            context.Response.Headers[name] = value;
        await next(context);
    }
}
