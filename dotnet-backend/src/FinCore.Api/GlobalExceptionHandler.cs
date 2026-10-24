using Microsoft.AspNetCore.Diagnostics;
using System.Text.Json;

namespace FinCore.Api;

public sealed class GlobalExceptionHandler(ILogger<GlobalExceptionHandler> logger) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        // Kestrel's body cap (MaxRequestBodySize) surfaces while the body is read, as this
        // exception: the client's mistake, answered with the same problem the Python API sends.
        if (exception is BadHttpRequestException { StatusCode: StatusCodes.Status413PayloadTooLarge })
        {
            httpContext.Response.StatusCode = StatusCodes.Status413PayloadTooLarge;
            httpContext.Response.ContentType = "application/problem+json";
            await httpContext.Response.WriteAsync(JsonSerializer.Serialize(new
            {
                type = "https://fincore.local/problems/request_too_large",
                title = "Payload too large",
                status = 413,
                code = "request_too_large",
                detail = "Request body is too large.",
                traceId = httpContext.TraceIdentifier
            }), cancellationToken);
            return true;
        }
        logger.LogError("Unhandled API exception. TraceId={TraceId}, ErrorType={ErrorType}",
            httpContext.TraceIdentifier, exception.GetType().Name);
        httpContext.Response.StatusCode = StatusCodes.Status500InternalServerError;
        httpContext.Response.ContentType = "application/problem+json";
        var problem = new
        {
            type = "https://fincore.local/problems/internal-error",
            title = "Internal server error",
            status = 500,
            code = "internal_error",
            detail = "The request could not be completed.",
            traceId = httpContext.TraceIdentifier
        };
        await httpContext.Response.WriteAsync(JsonSerializer.Serialize(problem), cancellationToken);
        return true;
    }
}
