using FinCore.Application;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FinCore.Api.Controllers;

public sealed record CreatePaymentRequest(Guid SourceAccountId, Guid BeneficiaryId, long AmountMinor,
    string Currency, string? Reference);

[Route("api/v1/payments")]
[Authorize]
[Tags("payments")]
public sealed class PaymentsController(IPaymentService payments) : FinCoreControllerBase
{
    [HttpPost]
    [ProducesResponseType<PaymentView>(StatusCodes.Status201Created)]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status404NotFound, "application/problem+json")]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status409Conflict, "application/problem+json")]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status422UnprocessableEntity, "application/problem+json")]
    public async Task<IActionResult> Create(CreatePaymentRequest request,
        [FromHeader(Name = "Idempotency-Key")] string[]? idempotencyKeys, CancellationToken cancellationToken)
    {
        // Bound as an array so two Idempotency-Key headers are rejected the way the minimal API
        // rejected them (pitfall 9.8): only exactly one, non-blank, is accepted.
        if (idempotencyKeys is not [var key] || string.IsNullOrWhiteSpace(key))
            return ProblemResult(422, "invalid_idempotency_key", "Request validation failed",
                "Idempotency-Key is required.");
        var command = new CreatePaymentCommand(request.SourceAccountId, request.BeneficiaryId, request.AmountMinor,
            request.Currency, request.Reference);
        // PaymentException (limits, funds, idempotency conflicts...) becomes a problem in DomainExceptionFilter.
        var payment = await payments.CreateAsync(CustomerId, command, key, cancellationToken);
        // The contract defines 201 without a Location header, so StatusCode rather than CreatedAtAction.
        return StatusCode(StatusCodes.Status201Created, payment);
    }

    [HttpGet]
    [ProducesResponseType<PaymentPage>(StatusCodes.Status200OK)]
    public async Task<ActionResult<PaymentPage>> List([FromQuery] string? status, [FromQuery] int? limit,
        [FromQuery] string? cursor, CancellationToken cancellationToken) =>
        await payments.ListAsync(CustomerId, status, Math.Clamp(limit ?? 20, 1, 100), cursor, cancellationToken);

    [HttpGet("{paymentId:guid}")]
    [ProducesResponseType<PaymentView>(StatusCodes.Status200OK)]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status404NotFound, "application/problem+json")]
    public async Task<IActionResult> Get(Guid paymentId, CancellationToken cancellationToken)
    {
        var payment = await payments.GetAsync(CustomerId, paymentId, cancellationToken);
        return payment is null
            ? ProblemResult(404, "not_found", "Resource not found", "Payment not found.")
            : Ok(payment);
    }
}
