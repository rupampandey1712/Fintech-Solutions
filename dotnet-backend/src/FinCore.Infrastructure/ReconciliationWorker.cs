using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;

namespace FinCore.Infrastructure;

/// <summary>
/// Periodic reconciliation pass (spec section 49: background reconciliation worker). Off by
/// default; RECONCILIATION_SCHEDULE_ENABLED=true turns it on for deployments that want the
/// finance dashboard filled in without an analyst pressing "Run". Shares the exact
/// classification with the manual API endpoint.
/// </summary>
public sealed class ReconciliationWorker(IServiceScopeFactory scopeFactory, IConfiguration configuration)
    : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!(configuration.GetValue<bool?>("RECONCILIATION_SCHEDULE_ENABLED") ?? false))
            return;
        var interval = TimeSpan.FromSeconds(configuration.GetValue<int?>("RECONCILIATION_INTERVAL_SECONDS") ?? 3600);
        Log.Information("Scheduled reconciliation worker started (interval {Interval})", interval);
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await using var scope = scopeFactory.CreateAsyncScope();
                var db = scope.ServiceProvider.GetRequiredService<FinCoreDbContext>();
                var run = await ReconciliationService.ExecuteRunAsync(db, stoppingToken);
                Log.Information("Reconciliation run {RunId}: matched {Matched}, amount {Amount}, status {Status}, "
                    + "missing external {External}, missing internal {Internal}",
                    run.RunId, run.Matched, run.AmountMismatch, run.StatusMismatch,
                    run.MissingExternal, run.MissingInternal);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                Log.Warning(exception, "Scheduled reconciliation tick failed");
            }
            try
            {
                await Task.Delay(interval, stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }
}
