using FinCore.Application;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreateBeneficiaryRequest(Guid DestinationAccountId, string Label);

[Route("api/v1/beneficiaries")]
[Authorize]
[Tags("beneficiaries")]
public sealed class BeneficiariesController(IBeneficiaryService beneficiaries) : FinCoreControllerBase
{
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken cancellationToken) =>
        Ok(new { items = await beneficiaries.ListAsync(CustomerId, cancellationToken), nextCursor = (string?)null });

    [HttpPost]
    [ProducesResponseType<BeneficiaryView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Create(CreateBeneficiaryRequest request, CancellationToken cancellationToken)
    {
        var beneficiary = await beneficiaries.AddAsync(CustomerId, request.DestinationAccountId, request.Label,
            cancellationToken);
        return StatusCode(StatusCodes.Status201Created, beneficiary);
    }

    // BeneficiaryException (unknown beneficiary, wrong state) becomes a problem in DomainExceptionFilter.
    [HttpPost("{beneficiaryId:guid}/verify")]
    public async Task<IActionResult> Verify(Guid beneficiaryId, CancellationToken cancellationToken) =>
        Ok(await beneficiaries.VerifyAsync(CustomerId, beneficiaryId, cancellationToken));

    [HttpPost("{beneficiaryId:guid}/activate")]
    public async Task<IActionResult> Activate(Guid beneficiaryId, CancellationToken cancellationToken) =>
        Ok(await beneficiaries.ActivateAsync(CustomerId, beneficiaryId, cancellationToken));

    [HttpPost("{beneficiaryId:guid}/disable")]
    public async Task<IActionResult> Disable(Guid beneficiaryId, CancellationToken cancellationToken) =>
        Ok(await beneficiaries.DisableAsync(CustomerId, beneficiaryId, cancellationToken));
}
