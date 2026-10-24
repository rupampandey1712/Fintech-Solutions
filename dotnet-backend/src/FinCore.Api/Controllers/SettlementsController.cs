using System.Text;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreateBatchRequest(string BusinessDate, string Currency);

/// <summary>Settlement batches (spec section 58). Staff: admin, finance_user, operations_user.</summary>
[Route("api/v1/admin/settlement-batches")]
[Authorize(Policy = Policies.Finance)]
[Tags("settlement")]
public sealed class SettlementsController(SettlementsService settlements) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<SettlementBatchDetailView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Create(CreateBatchRequest request, CancellationToken cancellationToken)
    {
        var batch = await settlements.CreateAsync(CustomerId, request.BusinessDate, request.Currency,
            cancellationToken);
        return StatusCode(StatusCodes.Status201Created, batch);
    }

    [HttpPost("{batchId:guid}/process")]
    [ProducesResponseType<SettlementBatchDetailView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Process(Guid batchId, CancellationToken cancellationToken) =>
        Ok(await settlements.ProcessAsync(CustomerId, batchId, cancellationToken));

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var items = await settlements.ListAsync(Math.Clamp(limit ?? 50, 1, 200), cancellationToken);
        return Ok(new { items });
    }

    [HttpGet("{batchId:guid}")]
    [ProducesResponseType<SettlementBatchDetailView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Get(Guid batchId, CancellationToken cancellationToken)
    {
        var batch = await settlements.GetAsync(batchId, cancellationToken);
        return batch is null
            ? ProblemResult(404, "not_found", "Resource not found", "Settlement batch not found")
            : Ok(batch);
    }

    [HttpGet("{batchId:guid}/file")]
    public async Task<IActionResult> File(Guid batchId, CancellationToken cancellationToken)
    {
        var batch = await settlements.GetAsync(batchId, cancellationToken);
        var file = batch is not null ? await settlements.GetFileAsync(batchId, cancellationToken) : null;
        if (file is null)
            return ProblemResult(409, "settlement_file_not_available",
                "The settlement file is not available before the batch completes.");
        var name = $"settlement-{batch!.BusinessDate.Replace("-", "")}-{batchId.ToString("D")[..8]}.csv";
        Response.ContentType = "text/csv; charset=utf-8";
        Response.Headers["Content-Disposition"] = $"attachment; filename=\"{name}\"";
        return Content(file, "text/csv", Encoding.UTF8);
    }
}
