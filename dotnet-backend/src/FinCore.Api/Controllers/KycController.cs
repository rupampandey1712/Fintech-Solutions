using System.ComponentModel.DataAnnotations;
using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreateKycRequest([Required, MinLength(1), MaxLength(120)] string FullName,
    [Required, RegularExpression("^[A-Z]{2}$")] string Country);
public sealed record AddKycDocumentRequest([Required] string DocumentType, [Required] string ContentBase64);
public sealed record KycReviewRequest(bool Approve, string? Reason);

[Route("api/v1/kyc")]
[Authorize]
[Tags("kyc")]
public sealed class KycController(IKycService kyc) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<KycApplicationView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> Create(CreateKycRequest request, CancellationToken cancellationToken)
    {
        var application = await kyc.CreateAsync(CustomerId, request.FullName, request.Country, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, application);
    }

    [HttpPost("{applicationId:guid}/documents")]
    [ProducesResponseType<KycDocumentView>(StatusCodes.Status201Created)]
    public async Task<IActionResult> AddDocument(Guid applicationId, AddKycDocumentRequest request,
        CancellationToken cancellationToken)
    {
        var document = await kyc.AddDocumentAsync(CustomerId, applicationId, request.DocumentType,
            request.ContentBase64, cancellationToken);
        return StatusCode(StatusCodes.Status201Created, document);
    }

    [HttpPost("{applicationId:guid}/submit")]
    [ProducesResponseType<KycApplicationView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Submit(Guid applicationId, CancellationToken cancellationToken) =>
        Ok(await kyc.SubmitAsync(CustomerId, applicationId, cancellationToken));

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken cancellationToken) =>
        Ok(new { items = await kyc.ListAsync(CustomerId, cancellationToken), nextCursor = (string?)null });

    [HttpGet("{applicationId:guid}")]
    [ProducesResponseType<KycApplicationView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Get(Guid applicationId, CancellationToken cancellationToken)
    {
        var application = await kyc.GetAsync(CustomerId, applicationId, cancellationToken);
        return application is null
            ? ProblemResult(404, "not_found", "Resource not found", "KYC application not found")
            : Ok(application);
    }

    [HttpPost("{applicationId:guid}/review")]
    [Authorize(Policy = Policies.FraudReview)]
    [ProducesResponseType<KycApplicationView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Review(Guid applicationId, KycReviewRequest request,
        CancellationToken cancellationToken)
    {
        var application = await kyc.ReviewAsync(applicationId, CustomerId, request.Approve, request.Reason,
            cancellationToken);
        return application is null
            ? ProblemResult(404, "not_found", "Resource not found", "KYC application not found")
            : Ok(application);
    }
}
