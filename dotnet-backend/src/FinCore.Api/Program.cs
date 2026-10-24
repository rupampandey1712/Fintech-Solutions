using FinCore.Infrastructure;
using FinCore.Application;
using FinCore.Api;
using FinCore.Api.Controllers;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Npgsql;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using Serilog;
using System.Text;

var builder = WebApplication.CreateBuilder(args);
// Kestrel stamps "Server: Kestrel" on every response unless this is disabled — a technology
// fingerprint (docs/SECURITY_HEADERS.md). The Python side runs uvicorn with --no-server-header.
// The body cap matches the Python API's: the largest legitimate request is a KYC upload, a 5 MiB
// document being about 6.7 MiB once base64-encoded inside JSON (Kestrel's default is 30 MB).
builder.WebHost.ConfigureKestrel(options =>
{
    options.AddServerHeader = false;
    options.Limits.MaxRequestBodySize =
        builder.Configuration.GetValue<long?>("MAX_REQUEST_BODY_BYTES") ?? 8 * 1024 * 1024;
});
// OTLP export (traces, metrics, logs) is on only when OTEL_EXPORTER_OTLP_ENDPOINT is set, so tests and
// Docker-free runs export nothing. Console logs carry the trace id for correlation either way.
var otlpEndpoint = builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"];
var serviceName = builder.Configuration["OTEL_SERVICE_NAME"] ?? "fincore-dotnet-api";
builder.Host.UseSerilog((context, configuration) =>
{
    configuration
        .ReadFrom.Configuration(context.Configuration)
        .Enrich.FromLogContext()
        .WriteTo.Console(outputTemplate: "[{Timestamp:HH:mm:ss} {Level:u3}] {Message:lj} {TraceId}{NewLine}{Exception}");
    if (!string.IsNullOrWhiteSpace(otlpEndpoint))
        configuration.WriteTo.OpenTelemetry(sink =>
        {
            sink.Endpoint = otlpEndpoint;
            sink.ResourceAttributes = new Dictionary<string, object> { ["service.name"] = serviceName };
        });
});

var connectionString = builder.Configuration.GetConnectionString("FinCore")
    ?? builder.Configuration["DATABASE_URL"]
    ?? "Host=localhost;Port=5432;Database=fincore;Username=fincore;Password=fincore";
builder.Services.AddDbContext<FinCoreDbContext>(options => options.UseNpgsql(connectionString));
builder.Services.AddScoped<ICustomerIdentityRepository, CustomerIdentityRepository>();
builder.Services.AddScoped<FinCore.Application.IPasswordHasher, CustomerPasswordHasher>();
builder.Services.AddScoped<CustomerPasswordHasher>();
builder.Services.AddScoped<IAccessTokenIssuer, JwtAccessTokenIssuer>();
builder.Services.AddScoped<IRefreshTokenService, RefreshTokenService>();
// Built once: loading the list and filling the Bloom filter happens at startup, not per request.
builder.Services.AddSingleton<ICommonPasswordCheck>(services => CommonPasswordList.Load(
    builder.Configuration, services.GetRequiredService<ILoggerFactory>().CreateLogger<CommonPasswordList>()));
builder.Services.AddScoped<AuthenticationService>();
// Fraud investigator agent (Agent Framework + Gemini). Off unless GEMINI_API_KEY is set; the agent
// only reads data and drafts a recommendation, a reviewer makes the decision.
var agentTimeout = TimeSpan.FromSeconds(builder.Configuration.GetValue<double?>("AGENT_TIMEOUT_SECONDS") ?? 90);
builder.Services.AddSingleton<IInvestigatorChatClientFactory>(new GeminiChatClientFactory(
    builder.Configuration["GEMINI_API_KEY"], builder.Configuration["AGENT_MODEL"] ?? "gemini-2.5-flash", agentTimeout));
builder.Services.AddSingleton(new FinCore.Api.Controllers.InvestigatorSettings(
    builder.Configuration.GetValue<int?>("AGENT_MAX_STEPS") ?? 8, agentTimeout));
builder.Services.AddScoped<IAccountService, AccountService>();
builder.Services.AddScoped<IPaymentService, PaymentService>();
builder.Services.AddScoped<IKycService, KycService>();
// One Redis connection for the rate limiter and the configuration cache. It connects lazily and
// never blocks: a Redis outage degrades both to database/in-process behaviour instead of failing.
var redisUrl = builder.Configuration["REDIS_URL"];
var redis = string.IsNullOrWhiteSpace(redisUrl)
    ? null
    : StackExchange.Redis.ConnectionMultiplexer.Connect(RedisRateLimitStore.ParseOptions(redisUrl));
builder.Services.AddSingleton(new FinCoreCache(redis));
builder.Services.AddScoped<TreasuryService>();
builder.Services.AddScoped<CorrectionsService>();
builder.Services.AddScoped<DisputesService>();
builder.Services.AddScoped<SettlementsService>();
builder.Services.AddSingleton<KycDocumentStore>();
builder.Services.AddHttpContextAccessor();
var beneficiaryCoolingSeconds = builder.Configuration.GetValue<int>("BENEFICIARY_COOLING_SECONDS", 86_400);
builder.Services.AddScoped<IBeneficiaryService>(provider => new BeneficiaryService(
    provider.GetRequiredService<FinCoreDbContext>(), TimeSpan.FromSeconds(beneficiaryCoolingSeconds)));
builder.Services.AddSingleton(new AccountLimitDefaults(
    builder.Configuration.GetValue<long>("DEFAULT_LIMIT_PER_TRANSACTION_MINOR", 10_000_000),
    builder.Configuration.GetValue<long>("DEFAULT_LIMIT_DAILY_MINOR", 50_000_000),
    builder.Configuration.GetValue<long>("DEFAULT_LIMIT_WEEKLY_MINOR", 100_000_000),
    builder.Configuration.GetValue<long>("DEFAULT_LIMIT_MONTHLY_MINOR", 200_000_000)));
// Outbox dispatcher plus, when MESSAGE_PROVIDER=servicebus, the topic publisher and subscription consumer.
builder.Services.AddFinCoreMessaging(builder.Configuration);
var jwtKey = builder.Configuration["JWT_SIGNING_KEY"] ?? "development-only-change-this-key";
if (builder.Environment.IsProduction() && Encoding.UTF8.GetByteCount(jwtKey) < 32)
    throw new InvalidOperationException("JWT_SIGNING_KEY must contain at least 32 bytes in production.");
var jwtSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwtKey));
builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme).AddJwtBearer(options =>
{
    options.MapInboundClaims = true;
    options.TokenValidationParameters = new TokenValidationParameters
    {
        ValidateIssuerSigningKey = true,
        IssuerSigningKey = jwtSigningKey,
        ValidateIssuer = true,
        ValidIssuer = builder.Configuration["JWT_ISSUER"] ?? "fincore",
        ValidateAudience = true,
        ValidAudience = builder.Configuration["JWT_AUDIENCE"] ?? "fincore-client",
        ValidateLifetime = true,
        ClockSkew = TimeSpan.Zero
    };
    options.Events = new JwtBearerEvents
    {
        OnChallenge = async context =>
        {
            context.HandleResponse();
            await WriteAuthProblem(context.HttpContext, 401, "unauthorized", "Authentication required");
        },
        OnForbidden = async context =>
            await WriteAuthProblem(context.HttpContext, 403, "forbidden", "Access denied")
    };
});
builder.Services.AddAuthorization(Policies.Configure);
builder.Services.AddScoped<IAuthorizationHandler, DatabaseRoleHandler>();
// Controllers replace the minimal API groups: the domain-exception filter turns the typed
// application exceptions into contract problems, and the model-validation factory keeps 422
// (not [ApiController]'s default 400) so responses match the Python implementation.
builder.Services.AddControllers(options => options.Filters.Add<DomainExceptionFilter>())
    .ConfigureApiBehaviorOptions(options =>
        options.InvalidModelStateResponseFactory = ProblemResponses.InvalidModelState);
builder.Services.AddExceptionHandler<GlobalExceptionHandler>();
builder.Services.AddProblemDetails();
builder.Services.AddCors(options => options.AddDefaultPolicy(policy => policy
    .WithOrigins((builder.Configuration["CORS_ORIGINS"] ?? "http://localhost:5173")
        .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
    .AllowAnyHeader().AllowAnyMethod()));
builder.Services.AddOpenApi();
var telemetry = builder.Services.AddOpenTelemetry()
    .ConfigureResource(resource => resource.AddService(serviceName, serviceNamespace: "fincore"))
    .WithMetrics(metrics => metrics
        .AddMeter(FinCoreTelemetry.Name)
        .AddAspNetCoreInstrumentation()
        .AddRuntimeInstrumentation())
    .WithTracing(tracing => tracing
        .AddSource(FinCoreTelemetry.Name)
        .AddAspNetCoreInstrumentation(options => options.Filter = context =>
            context.Request.Path != "/health" && context.Request.Path != "/ready")
        .AddHttpClientInstrumentation()
        .AddNpgsql());
if (!string.IsNullOrWhiteSpace(otlpEndpoint))
    telemetry.UseOtlpExporter();

var rateLimitOptions = new RateLimitOptions(
    builder.Configuration.GetValue<bool?>("RATE_LIMIT_ENABLED") ?? true,
    builder.Configuration.GetValue<int?>("RATE_LIMIT_LOGIN_PER_MINUTE") ?? 5,
    builder.Configuration.GetValue<int?>("RATE_LIMIT_REGISTER_PER_MINUTE") ?? 10,
    builder.Configuration.GetValue<int?>("RATE_LIMIT_PAYMENT_PER_MINUTE") ?? 10,
    builder.Configuration.GetValue<int?>("RATE_LIMIT_DEFAULT_PER_MINUTE") ?? 100,
    () => DateTimeOffset.UtcNow,
    SharedStore: redis is null ? null : new RedisRateLimitStore(redis),
    LoginPerAccountPerMinute: builder.Configuration.GetValue<int?>("RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_MINUTE") ?? 10,
    AgentPerMinute: builder.Configuration.GetValue<int?>("RATE_LIMIT_AGENT_PER_MINUTE") ?? 5);
builder.Services.AddSingleton(services =>
    new RateLimiter(rateLimitOptions, services.GetRequiredService<ILogger<RateLimiter>>()));

var app = builder.Build();
// Outermost of all: the security headers ride on every response, whatever produced it
// (action result, exception handler, authentication challenge, rate-limit refusal).
app.UseMiddleware<SecurityHeadersMiddleware>();
app.UseMiddleware<CorrelationIdMiddleware>();
app.UseCors();
app.UseExceptionHandler();
app.UseSerilogRequestLogging();
app.UseAuthentication();
// After authentication so authenticated callers are limited per customer rather than per IP.
app.UseMiddleware<RateLimitMiddleware>(rateLimitOptions, app.Services.GetRequiredService<RateLimiter>());
app.UseAuthorization();
// Served at the same path as the Python implementation, and only outside production — the
// OpenAPI document is a development tool, not a production surface.
if (app.Environment.IsDevelopment()) app.MapOpenApi("/openapi.json");

app.MapGet("/health", () => Results.Ok(new { status = "ok" })).WithTags("health");
// API metadata, byte-identical to the Python implementation's GET /api/v1: language-neutral,
// so it does not reveal which stack answered (docs/SECURITY_HEADERS.md).
app.MapGet("/api/v1", () => Results.Ok(new
{
    name = "FinCore API",
    version = "0.1.0",
    contract = "/openapi.json"
})).WithTags("meta");
app.MapGet("/ready", async (HttpContext context, FinCoreDbContext db, CancellationToken cancellationToken) =>
{
    try
    {
        var connected = await db.Database.CanConnectAsync(cancellationToken);
        return connected
            ? Results.Ok(new { status = "ready" })
            : ReadinessProblem(context);
    }
    catch (Exception exception) when (exception is Npgsql.NpgsqlException or InvalidOperationException)
    {
        return ReadinessProblem(context);
    }
}).WithTags("health");
// All HTTP endpoints are MVC controllers (src/FinCore.Api/Controllers); MapControllers adds
// them to the same routing table the minimal API groups used, so every middleware above —
// correlation, CORS, exception handling, request logging, JWT auth, rate limiting — is unchanged.
app.MapControllers();
// Unmatched routes answer with the same problem+json 404 the Python implementation produces
// (its routing 404s flow through the exception handlers), instead of an empty body.
app.MapFallback((HttpContext context) => Results.Problem(
    type: "https://fincore.local/problems/not_found",
    statusCode: StatusCodes.Status404NotFound,
    title: "Resource not found",
    detail: "Not Found",
    extensions: new Dictionary<string, object?>
    {
        ["code"] = "not_found",
        ["traceId"] = context.TraceIdentifier
    }));

await using (var seedScope = app.Services.CreateAsyncScope())
{
    await StartupSeeder.SeedAsync(app.Configuration,
        seedScope.ServiceProvider.GetRequiredService<FinCoreDbContext>(),
        seedScope.ServiceProvider.GetRequiredService<CustomerPasswordHasher>());
}

app.Run();

static Task WriteAuthProblem(HttpContext context, int status, string code, string title)
{
    context.Response.StatusCode = status;
    context.Response.ContentType = "application/problem+json";
    return context.Response.WriteAsJsonAsync(new
    {
        type = $"https://fincore.local/problems/{code}",
        title,
        status,
        code,
        detail = title,
        traceId = context.TraceIdentifier
    });
}

static IResult ReadinessProblem(HttpContext context) => Results.Problem(
    type: "https://fincore.local/problems/dependency_unavailable",
    statusCode: StatusCodes.Status503ServiceUnavailable,
    title: "Required dependency unavailable",
    detail: "The database is unavailable.",
    extensions: new Dictionary<string, object?>
    {
        ["code"] = "dependency_unavailable",
        ["traceId"] = context.TraceIdentifier
    });

public partial class Program { }
