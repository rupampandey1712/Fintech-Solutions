namespace FinCore.Api;

public sealed class CorrelationIdMiddleware(RequestDelegate next)
{
    public async Task InvokeAsync(HttpContext context)
    {
        var correlationId = context.Request.Headers.TryGetValue("X-Correlation-ID", out var values)
                && values.Count > 0 && !string.IsNullOrWhiteSpace(values[0])
            ? values[0]!
            : Guid.NewGuid().ToString();
        context.Items["CorrelationId"] = correlationId;
        context.Response.OnStarting(() =>
        {
            context.Response.Headers["X-Correlation-ID"] = correlationId;
            return Task.CompletedTask;
        });
        await next(context);
    }
}
