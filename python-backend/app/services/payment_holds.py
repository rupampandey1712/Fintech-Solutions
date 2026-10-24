"""Settlement of payments held for fraud review.

A held payment is ``pending`` and its funds sit in the payment-holds clearing ledger. The fraud
reviewer's decision settles it:

* ``false_positive`` -> release: holds -> beneficiary, payment ``completed``.
* ``closed`` (confirmed fraud) -> refund: holds -> source account, payment ``rejected``.
* ``escalated`` -> no money movement; the payment stays held.

A payment whose provider call timed out is ``processing`` with its funds in the same holds
ledger. Reconciliation settles it from the provider's own record:

* provider ``completed`` -> release: holds -> beneficiary, payment ``completed``.
* provider ``failed``    -> refund: holds -> source account, payment ``failed``.
"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.payment_state import PaymentStatus, transition
from app.infrastructure.models import Beneficiary, LedgerAccount, OutboxEvent, Payment
from app.services.ledger_postings import PAYMENT_HOLDS, post_transfer, system_ledger


def completed_event(payment: Payment) -> OutboxEvent:
    return lifecycle_event("payment.completed.v1", payment)


def lifecycle_event(event_type: str, payment: Payment) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(), aggregate_id=payment.id, event_type=event_type, schema_version=1,
        payload={
            "paymentId": str(payment.id), "customerId": str(payment.customer_id),
            "sourceAccountId": str(payment.source_account_id),
            "beneficiaryId": str(payment.beneficiary_id),
            "amountMinor": payment.amount_minor, "currency": payment.currency,
            "status": payment.status,
            "createdAt": (payment.created_at or datetime.now(UTC)).isoformat(),
        },
    )


async def _ledger_for_account(session: AsyncSession, account_id) -> LedgerAccount:
    ledger = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.account_id == account_id),
    )
    if ledger is None:
        raise HTTPException(status_code=409, detail="Account ledger is not configured")
    return ledger


async def settle_held_payment(session: AsyncSession, payment_id, resolution: str) -> str | None:
    """Apply a review decision to the held payment. Returns its new status, or None if unchanged.

    The payment row is locked so two reviewers cannot both settle it; anything not still
    ``pending`` (already settled, or never held) is left alone.
    """
    if payment_id is None or resolution not in {"false_positive", "closed"}:
        return None
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update(),
    )
    if payment is None or payment.status != PaymentStatus.PENDING:
        return None
    holds = await system_ledger(session, PAYMENT_HOLDS, payment.currency)

    if resolution == "false_positive":
        beneficiary = await session.get(Beneficiary, payment.beneficiary_id)
        if beneficiary is None:
            raise HTTPException(status_code=409, detail="Beneficiary no longer exists")
        destination = await _ledger_for_account(session, beneficiary.destination_account_id)
        await post_transfer(session, f"payment:{payment.id}:release", payment.currency,
                            holds.id, destination.id, payment.amount_minor)
        status = transition(PaymentStatus.PENDING, PaymentStatus.PROCESSING)
        payment.status = transition(status, PaymentStatus.COMPLETED).value
        session.add(completed_event(payment))
        return payment.status

    source = await _ledger_for_account(session, payment.source_account_id)
    await post_transfer(session, f"payment:{payment.id}:refund", payment.currency,
                        holds.id, source.id, payment.amount_minor)
    payment.status = transition(PaymentStatus.PENDING, PaymentStatus.REJECTED).value
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=payment.id, event_type="payment.rejected.v1", schema_version=1,
        payload={
            "paymentId": str(payment.id), "customerId": str(payment.customer_id),
            "amountMinor": payment.amount_minor, "currency": payment.currency,
        },
    ))
    return payment.status


async def settle_unconfirmed_payment(session: AsyncSession, payment_id, provider_status: str,
                                     provider_amount_minor: int) -> str | None:
    """Settle a timed-out payment from the provider's record. Returns its new status, or None.

    Only a ``processing`` payment whose provider record has a final status and the same amount
    is settled; anything else (an amount disagreement, an unknown status) is left for a human,
    since moving money on contradictory evidence would be worse than leaving it held.
    """
    if provider_status not in {"completed", "failed"}:
        return None
    # populate_existing: the caller may already hold this row in the session; re-read it under
    # the lock so a concurrent run that settled it first is seen.
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
        .execution_options(populate_existing=True),
    )
    if (payment is None or payment.status != PaymentStatus.PROCESSING
            or payment.amount_minor != provider_amount_minor):
        return None
    holds = await system_ledger(session, PAYMENT_HOLDS, payment.currency)

    if provider_status == "completed":
        beneficiary = await session.get(Beneficiary, payment.beneficiary_id)
        if beneficiary is None:
            raise HTTPException(status_code=409, detail="Beneficiary no longer exists")
        destination = await _ledger_for_account(session, beneficiary.destination_account_id)
        await post_transfer(session, f"payment:{payment.id}:release", payment.currency,
                            holds.id, destination.id, payment.amount_minor)
        payment.status = transition(PaymentStatus.PROCESSING, PaymentStatus.COMPLETED).value
        payment.provider_status = provider_status
        session.add(completed_event(payment))
        return payment.status

    source = await _ledger_for_account(session, payment.source_account_id)
    await post_transfer(session, f"payment:{payment.id}:refund", payment.currency,
                        holds.id, source.id, payment.amount_minor)
    payment.status = transition(PaymentStatus.PROCESSING, PaymentStatus.FAILED).value
    payment.provider_status = provider_status
    session.add(lifecycle_event("payment.failed.v1", payment))
    return payment.status
