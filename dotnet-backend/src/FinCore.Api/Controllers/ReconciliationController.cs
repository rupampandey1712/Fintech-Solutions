using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

[Route("api/v1/reconciliation")]
[Authorize(Policy = Policies.Finance)]
[Tags("reconciliation")]
public sealed class ReconciliationController(FinCoreDbContext db, IAuthorizationService authorization)
    : FinCoreControllerBase
{
    [HttpPost("run")]
    [ProducesResponseType<ReconciliationRunResponse>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Run(CancellationToken cancellationToken) =>
        Ok(await ReconciliationService.ExecuteRunAsync(db, cancellationToken));

    [HttpGet("runs")]
    public async Task<IActionResult> ListRuns([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        if (!await IsAsync(Policies.Finance, cancellationToken)) return Forbid();
        var pageSize = Math.Clamp(limit ?? 50, 1, 200);
        var items = await db.ReconciliationRuns.AsNoTracking()
            .OrderByDescending(run => run.StartedAt).ThenByDescending(run => run.Id)
            .Take(pageSize)
            .Select(run => new ReconciliationRunView(run.Id, run.StartedAt, run.FinishedAt, run.Matched,
                run.AmountMismatch, run.StatusMismatch, run.MissingExternal, run.MissingInternal))
            .ToListAsync(cancellationToken);
        return Ok(new { items });
    }

    [HttpGet("runs/{runId:guid}")]
    [ProducesResponseType<ReconciliationRunDetail>(StatusCodes.Status200OK)]
    public async Task<IActionResult> GetRun(Guid runId, CancellationToken cancellationToken)
    {
        if (!await IsAsync(Policies.Finance, cancellationToken)) return Forbid();
        var run = await db.ReconciliationRuns.AsNoTracking().SingleOrDefaultAsync(item => item.Id == runId,
            cancellationToken);
        if (run is null)
            return ProblemResult(404, "not_found", "Resource not found", "Reconciliation run not found");
        var records = await db.ReconciliationRecords.AsNoTracking().Where(record => record.RunId == run.Id)
            .OrderBy(record => record.CreatedAt).ThenBy(record => record.PaymentId).ToListAsync(cancellationToken);
        return Ok(new ReconciliationRunDetail(run.Id, run.StartedAt, run.FinishedAt, run.Matched,
            run.AmountMismatch, run.StatusMismatch, run.MissingExternal, run.MissingInternal,
            records.Select(ToView).ToArray()));
    }

    private async Task<bool> IsAsync(string policy, CancellationToken cancellationToken) =>
        (await authorization.AuthorizeAsync(User, policy)).Succeeded;

    private static ReconciliationRecordView ToView(ReconciliationRecordRow record) =>
        new(record.PaymentId, record.Status, record.InternalStatus, record.ExternalStatus,
            record.InternalAmountMinor, record.ExternalAmountMinor, record.ExternalReference);
}
