"""Deterministic external payment provider simulator (architecture spec, sections 21 and 85).

Payments are submitted to the provider exactly once, and the provider writes its own record
(``ProviderTransaction``) as the external system of record. Reconciliation therefore compares
two independent data sets instead of reading the provider's answer back out of the payment.

Outcomes are driven deterministically by markers in the payment reference, so tests and the
seeder can select a scenario without randomness:

    "force-provider-failure"   -> the provider declines the transfer
    "force-provider-timeout"   -> the provider processes the payment but the response is lost
    "force-provider-duplicate" -> a duplicate submission is detected and coalesced
    "force-provider-delay"     -> the provider is slow but completes
    "force-provider-partial"   -> the provider fails part-way through the transfer
    anything else              -> success

A timeout is the interesting distributed-systems case: the money moved at the provider but our
side never saw the answer. The payment stays ``processing`` with its funds held, reconciliation
reports the status mismatch and then settles it from the provider's record — exactly why
reconciliation exists.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models import Payment, ProviderTransaction

MARKER_FAILURE = "force-provider-failure"
MARKER_TIMEOUT = "force-provider-timeout"
MARKER_DUPLICATE = "force-provider-duplicate"
MARKER_DELAY = "force-provider-delay"
MARKER_PARTIAL = "force-provider-partial"

MODE_SUCCESS = "success"
MODE_FAILURE = "failure"
MODE_TIMEOUT = "timeout"
MODE_DUPLICATE = "duplicate"
MODE_DELAYED = "delayed"
MODE_PARTIAL = "partial_failure"

# The status the provider's own record carries for each simulation mode.
_PROVIDER_STATUS = {
    MODE_SUCCESS: "completed",
    MODE_FAILURE: "failed",
    # The provider completed the transfer; our side never saw the response.
    MODE_TIMEOUT: "completed",
    MODE_DUPLICATE: "completed",
    MODE_DELAYED: "completed",
    MODE_PARTIAL: "failed",
}


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """What the provider told us (or failed to tell us) about a submitted payment."""

    mode: str
    accepted: bool  # False: the payment did not go through; the transfer must not post.
    provider_reference: str
    provider_status: str


def mode_for_reference(reference: str | None) -> str:
    """Map a reference marker to a simulation mode (deterministic, no randomness)."""
    text = reference or ""
    if MARKER_FAILURE in text:
        return MODE_FAILURE
    if MARKER_TIMEOUT in text:
        return MODE_TIMEOUT
    if MARKER_DUPLICATE in text:
        return MODE_DUPLICATE
    if MARKER_DELAY in text:
        return MODE_DELAYED
    if MARKER_PARTIAL in text:
        return MODE_PARTIAL
    return MODE_SUCCESS


class PaymentProviderSimulator:
    """In-process stand-in for a real payment provider (spec section 85: mock properly)."""

    async def process_payment(self, session: AsyncSession, payment: Payment) -> ProviderResult:
        """Submit a payment to the provider and record the outcome in the provider's ledger."""
        mode = mode_for_reference(payment.reference)
        accepted = mode not in {MODE_FAILURE, MODE_PARTIAL}
        provider_status = _PROVIDER_STATUS[mode]
        provider_amount = payment.amount_minor
        # Legacy reconciliation markers keep their meaning through the provider's own record:
        # the transfer completes, but the provider's ledger disagrees with ours, which is what
        # reconciliation exists to detect.
        text = payment.reference or ""
        if mode == MODE_SUCCESS:
            if "force-status-mismatch" in text:
                provider_status = "failed"
            elif "force-amount-mismatch" in text:
                provider_amount = payment.amount_minor * 2
        reference = f"provider:{payment.id}"
        # The provider is idempotent on the payment reference: one payment, one provider row,
        # even if a retried request reaches the provider twice (duplicate detection).
        existing = await session.scalar(
            select(ProviderTransaction).where(ProviderTransaction.payment_id == payment.id)
        )
        if existing is None:
            session.add(ProviderTransaction(
                payment_id=payment.id, reference=reference, status=provider_status,
                amount_minor=provider_amount, currency=payment.currency, mode=mode,
                processed_at=datetime.now(UTC),
            ))
        return ProviderResult(
            mode=mode, accepted=accepted, provider_reference=reference,
            provider_status=provider_status if accepted else mode,
        )
