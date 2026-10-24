"""Atomic customer-to-customer transfers with idempotency, fraud/AML checks, and pagination."""

import base64
import functools
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.domain import aml as aml_domain
from app.domain import fraud as fraud_domain
from app.domain.payment_state import PaymentStatus, transition
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    AmlAlert,
    Beneficiary,
    Customer,
    CustomerAccount,
    FraudCase,
    IdempotencyRecord,
    JournalLine,
    LedgerAccount,
    OutboxEvent,
    Payment,
)
from app.infrastructure.telemetry import (
    fraud_detected_total,
    payment_processing_duration,
    payments_failed_total,
    payments_success_total,
    payments_total,
    tracer,
)
from app.services.audit import record_audit
from app.services.configuration import load_account_limits, load_fraud_rules
from app.services.ledger_postings import (
    PAYMENT_HOLDS,
    lock_accounts,
    post_transfer,
    system_ledger,
)
from app.services.payment_holds import completed_event
from app.services.provider import MODE_TIMEOUT, PaymentProviderSimulator

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

provider = PaymentProviderSimulator()


class CreatePaymentRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    source_account_id: UUID
    beneficiary_id: UUID
    amount_minor: int = Field(gt=0, le=9_223_372_036_854_775_807)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reference: str | None = Field(default=None, max_length=140)


class RiskView(BaseModel):
    score: int = 0
    level: str = "low"
    decision: str = "allow"
    reasons: list[str] = []


class PaymentResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    status: str
    amount_minor: int
    currency: str
    created_at: datetime
    reference: str | None = None
    provider_reference: str | None = None
    provider_status: str | None = None
    risk: RiskView = RiskView()


class PaymentPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[PaymentResponse]
    next_cursor: str | None = None


def create_payment_request_hash(request_payload: dict) -> str:
    # Hash a normalized representation shared by Python and .NET. Decimal-free
    # primitives, lowercase UUIDs, explicit nullable reference, lexicographic keys.
    return hashlib.sha256(json.dumps(
        request_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()


def _payment_event(event_type: str, payment: Payment, **extra: Any) -> OutboxEvent:
    """A domain event for a payment, with the payload shape shared across implementations."""
    payload: dict[str, Any] = {
        "paymentId": str(payment.id), "customerId": str(payment.customer_id),
        "sourceAccountId": str(payment.source_account_id),
        "beneficiaryId": str(payment.beneficiary_id),
        "amountMinor": payment.amount_minor, "currency": payment.currency,
        "status": payment.status,
        "createdAt": (payment.created_at or datetime.now(UTC)).isoformat(),
    }
    payload.update(extra)
    return OutboxEvent(
        id=uuid4(), aggregate_id=payment.id, event_type=event_type, schema_version=1,
        payload=payload,
    )


def _response(payment: Payment) -> PaymentResponse:
    return PaymentResponse(
        id=payment.id, status=payment.status, amount_minor=payment.amount_minor,
        currency=payment.currency, created_at=payment.created_at or datetime.now(UTC),
        reference=payment.reference,
        provider_reference=payment.provider_reference,
        provider_status=payment.provider_status,
        risk=RiskView(
            score=payment.risk_score or 0, level=payment.risk_level or "low",
            decision=payment.risk_decision or "allow", reasons=list(payment.risk_reasons or []),
        ),
    )


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.b64decode(cursor.encode()).decode()
        created_at_text, id_text = raw.split("|", 1)
        return datetime.fromisoformat(created_at_text), UUID(id_text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from exc


def _encode_cursor(payment: Payment) -> str:
    raw = f"{(payment.created_at or datetime.now(UTC)).isoformat()}|{payment.id}"
    return base64.b64encode(raw.encode()).decode()


async def _build_fraud_context(session: AsyncSession, customer: Customer, source, beneficiary,
                               amount_minor: int, now: datetime) -> dict:
    payments_last_10_min = int(await session.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.customer_id == customer.id,
            Payment.created_at >= now - timedelta(minutes=10),
        )
    ) or 0)
    payments_last_hour = int(await session.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.customer_id == customer.id,
            Payment.created_at >= now - timedelta(hours=1),
        )
    ) or 0)
    failed_last_hour = int(await session.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.customer_id == customer.id,
            Payment.status == "failed",
            Payment.created_at >= now - timedelta(hours=1),
        )
    ) or 0)
    # Feeds the AML "multiple beneficiaries" rule: many distinct recipients in one day is a
    # structuring pattern even when every single payment is small.
    distinct_beneficiaries_24h = int(await session.scalar(
        select(func.count(func.distinct(Payment.beneficiary_id))).where(
            Payment.customer_id == customer.id,
            Payment.created_at >= now - timedelta(hours=24),
        )
    ) or 0)
    return {
        "amount_minor": amount_minor,
        "beneficiary_age_seconds": (
            int((now - beneficiary.created_at).total_seconds()) if beneficiary.created_at else 0
        ),
        "account_age_seconds": (
            int((now - source.created_at).total_seconds()) if source.created_at else 0
        ),
        "payments_last_10_min": payments_last_10_min,
        "payments_last_hour": payments_last_hour,
        "failed_last_hour": failed_last_hour,
        "distinct_beneficiaries_24h": distinct_beneficiaries_24h,
    }


def _reason_code(detail: object) -> str:
    """Low-cardinality metric label from an exception detail ("Daily limit" -> daily_limit)."""
    text = re.sub(r"[^a-z0-9]+", "_", str(detail).lower()).strip("_")
    return text[:48] or "unknown"


def _observed(endpoint):
    """Span, counters and duration for payment creation (architecture spec, section 31)."""

    @functools.wraps(endpoint)  # FastAPI reads the wrapped signature for its dependencies
    async def wrapper(*args, **kwargs):
        started = time.perf_counter()
        outcome = "error"
        with tracer.start_as_current_span("payment.create") as span:
            try:
                response = await endpoint(*args, **kwargs)
                outcome = "held" if response.status == "pending" else response.status
                span.set_attributes({"fincore.payment.id": str(response.id),
                                     "fincore.payment.status": response.status,
                                     "fincore.risk.decision": response.risk.decision,
                                     "fincore.risk.score": response.risk.score})
                payments_success_total.add(1, {"outcome": outcome})
                return response
            except HTTPException as exc:
                # A refusal (limits, funds, fraud block) is the system working: an outcome, not
                # a span error.
                outcome = "refused"
                reason = _reason_code(exc.detail)
                span.set_attribute("fincore.refusal_reason", reason)
                payments_failed_total.add(1, {"reason": reason, "status_code": exc.status_code})
                raise
            finally:
                payments_total.add(1, {"outcome": outcome})
                payment_processing_duration.record(time.perf_counter() - started,
                                                   {"outcome": outcome})

    return wrapper


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
@_observed
async def create_payment(
    request: CreatePaymentRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> PaymentResponse:
    if any(character.isspace() for character in idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key cannot contain whitespace")
    request_payload = {
        "amount_minor": request.amount_minor,
        "beneficiary_id": str(request.beneficiary_id),
        "currency": request.currency,
        "reference": request.reference,
        "source_account_id": str(request.source_account_id),
    }
    request_hash = create_payment_request_hash(request_payload)
    operation = "payment:create"

    # Claim the key inside the same transaction as all payment effects. The unique
    # scope serializes concurrent retries without trusting process-local locks.
    try:
        claim = await session.execute(
            insert(IdempotencyRecord)
            .values(
                id=uuid4(), customer_id=customer.id, operation=operation,
                key=idempotency_key, request_hash=request_hash,
            )
            .on_conflict_do_nothing(index_elements=["customer_id", "operation", "key"])
            .returning(IdempotencyRecord.id)
        )
    except Exception:
        await session.rollback()
        raise
    claimed_id = claim.scalar_one_or_none()
    if claimed_id is None:
        existing = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.customer_id == customer.id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == idempotency_key,
        ))
        if existing is None:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Idempotency request is being processed")
        if existing.request_hash != request_hash:
            await session.rollback()
            raise HTTPException(status_code=409, detail="idempotency_key_reused")
        if existing.response_json is None:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Idempotency request is being processed")
        stored = existing.response_json
        replay_payload = dict(stored)
        replay_payload.setdefault("risk", RiskView().model_dump(by_alias=True))
        await session.commit()
        return PaymentResponse.model_validate(replay_payload)

    # If a business validation fails, discard the idempotency claim too. This
    # leaves a corrected retry able to reuse its key while a committed transfer
    # can only be replayed from its stored response.
    try:
        source = await session.scalar(
            select(CustomerAccount).where(
                CustomerAccount.id == request.source_account_id,
                CustomerAccount.customer_id == customer.id,
            )
        )
        if source is None:
            raise HTTPException(status_code=404, detail="Source account not found")
        if source.status != "active":
            raise HTTPException(status_code=409, detail="Source account is not active")
        if source.currency != request.currency:
            raise HTTPException(
                status_code=422, detail="Payment currency must match source account",
            )

        beneficiary = await session.scalar(
            select(Beneficiary).where(
                Beneficiary.id == request.beneficiary_id,
                Beneficiary.customer_id == customer.id,
            )
        )
        if beneficiary is None:
            raise HTTPException(status_code=404, detail="Beneficiary not found")
        if beneficiary.status != "active":
            raise HTTPException(status_code=409, detail="Beneficiary is not active")
        destination = await session.scalar(
            select(CustomerAccount).where(
                CustomerAccount.id == beneficiary.destination_account_id,
                CustomerAccount.status == "active",
            )
        )
        if destination is None:
            raise HTTPException(status_code=409, detail="Beneficiary destination is unavailable")
        if destination.currency != request.currency:
            raise HTTPException(
                status_code=422, detail="Cross-currency transfers are not supported",
            )
        if destination.id == source.id:
            raise HTTPException(
                status_code=422, detail="Source and destination accounts must differ",
            )

        # Configured caps come from the cache; the running totals compared with them below are
        # always recomputed from the database under the account locks.
        limits = await load_account_limits(session, source.id)
        if limits is None:
            raise HTTPException(status_code=409, detail="Source account has no configured limits")
        if request.amount_minor > limits["perTransactionMinor"]:
            raise HTTPException(status_code=422, detail="Per-transaction limit exceeded")

        now = datetime.now(UTC)
        ledger_accounts = list((await session.scalars(
            select(LedgerAccount).where(LedgerAccount.account_id.in_([source.id, destination.id]))
        )).all())
        by_account = {item.account_id: item for item in ledger_accounts}
        source_ledger = by_account.get(source.id)
        destination_ledger = by_account.get(destination.id)
        if source_ledger is None or destination_ledger is None:
            raise HTTPException(status_code=409, detail="Account ledger is not configured")

        # These advisory transaction locks serialize balance/limit checks across different
        # idempotency keys without storing a mutable balance. They are the only locks taken
        # on the accounts, always in UUID order, so opposite-direction transfers cannot
        # deadlock each other.
        await lock_accounts(session, source.id, destination.id)
        # Recompute after acquiring the account lock so concurrent transfers cannot overspend.
        balance_result = await session.execute(
            select(func.coalesce(func.sum(case(
                (JournalLine.side == "credit", JournalLine.amount_minor),
                else_=-JournalLine.amount_minor,
            )), 0))
            .where(JournalLine.ledger_account_id == source_ledger.id)
        )
        balance = int(balance_result.scalar_one())
        if balance < request.amount_minor:
            raise HTTPException(status_code=422, detail="Insufficient available balance")

        # Recheck window totals under the same account lock.
        current_totals = await session.execute(
            select(
                func.coalesce(
                    func.sum(Payment.amount_minor).filter(
                        Payment.created_at >= now - timedelta(days=1),
                    ), 0,
                ),
                func.coalesce(
                    func.sum(Payment.amount_minor).filter(
                        Payment.created_at >= now - timedelta(days=7),
                    ), 0,
                ),
                func.coalesce(
                    func.sum(Payment.amount_minor).filter(
                        Payment.created_at >= now - timedelta(days=30),
                    ), 0,
                ),
            ).where(
                Payment.source_account_id == source.id,
                Payment.status.in_(["pending", "processing", "completed"]),
            )
        )
        daily, weekly, monthly = (int(value) for value in current_totals.one())
        if daily + request.amount_minor > limits["dailyMinor"]:
            raise HTTPException(status_code=422, detail="Daily transfer limit exceeded")
        if weekly + request.amount_minor > limits["weeklyMinor"]:
            raise HTTPException(status_code=422, detail="Weekly transfer limit exceeded")
        if monthly + request.amount_minor > limits["monthlyMinor"]:
            raise HTTPException(status_code=422, detail="Monthly transfer limit exceeded")

        rules = [rule for rule in await load_fraud_rules(session) if rule["enabled"]]
        fraud_context = await _build_fraud_context(
            session, customer, source, beneficiary, request.amount_minor, now,
        )
        evaluation = fraud_domain.evaluate(
            [
                fraud_domain.FraudRuleConfig(
                    code=rule["code"], weight=rule["weight"],
                    threshold_minor=rule["thresholdMinor"],
                )
                for rule in rules
            ],
            fraud_context,
        )
        if evaluation.decision == fraud_domain.DECISION_BLOCK:
            fraud_detected_total.add(1, {"decision": "block"})
            # Roll back the idempotency claim with the rest of the attempt, but the
            # blocked-payment audit must survive the rollback, so it uses its own session
            # bound to the same engine as the request. Read the id first: rollback expires
            # every loaded instance and an async session cannot lazy-load it back.
            customer_id = customer.id
            await session.rollback()
            # AsyncSession.bind is the AsyncEngine; get_bind() would return its sync Engine.
            async with AsyncSession(session.bind, expire_on_commit=False) as audit_session:
                await record_audit(
                    audit_session, customer_id, "payment.blocked", "payment", None,
                    request=http_request, new_value={"reasons": list(evaluation.reasons)},
                )
                await audit_session.commit()
            raise HTTPException(status_code=422, detail="Payment blocked by fraud rules")

        # A payment that needs fraud review is held: the funds leave the source account for
        # the payment-holds clearing ledger, so they cannot be spent twice, and only reach the
        # beneficiary when a reviewer releases them (app.services.payment_holds). Direct
        # payments are submitted to the provider simulator, which records its own outcome;
        # money reaches the beneficiary only once the provider accepts the transfer (a timeout
        # holds it instead, until reconciliation settles the payment).
        held = evaluation.decision == fraud_domain.DECISION_REVIEW
        payment = Payment(
            id=uuid4(), customer_id=customer.id, source_account_id=source.id,
            beneficiary_id=beneficiary.id, amount_minor=request.amount_minor,
            currency=request.currency, status=PaymentStatus.INITIATED.value,
            reference=request.reference,
            risk_score=evaluation.score, risk_level=evaluation.level,
            risk_decision=evaluation.decision, risk_reasons=list(evaluation.reasons),
        )
        session.add(payment)
        await session.flush()
        session.add(_payment_event("payment.initiated.v1", payment))
        payment.status = transition(PaymentStatus.INITIATED, PaymentStatus.PENDING).value

        if held:
            fraud_detected_total.add(1, {"decision": "review"})
            credit_ledger_id = (
                await system_ledger(session, PAYMENT_HOLDS, request.currency)
            ).id
            await post_transfer(session, f"payment:{payment.id}", request.currency,
                                source_ledger.id, credit_ledger_id, request.amount_minor)
            payment.status = PaymentStatus.PENDING.value
            session.add(FraudCase(
                customer_id=customer.id, payment_id=payment.id, score=evaluation.score,
                level=evaluation.level, status="open", reasons=list(evaluation.reasons),
            ))
            session.add(OutboxEvent(
                id=uuid4(), aggregate_id=payment.id,
                event_type="payment.flagged.v1", schema_version=1,
                payload={
                    "paymentId": str(payment.id), "customerId": str(customer.id),
                    "score": evaluation.score, "level": evaluation.level,
                },
            ))
            session.add(OutboxEvent(
                id=uuid4(), aggregate_id=payment.id,
                event_type="fraud.case.opened.v1", schema_version=1,
                payload={
                    "paymentId": str(payment.id), "customerId": str(customer.id),
                    "score": evaluation.score, "level": evaluation.level,
                },
            ))
            await record_audit(session, customer.id, "payment.flagged", "payment", payment.id,
                               request=http_request,
                               new_value={"score": evaluation.score, "level": evaluation.level})
        else:
            # The provider hop happens before any internal posting: a declined or partially
            # failed transfer must never move money. The state machine walks
            # PENDING -> PROCESSING -> COMPLETED/FAILED; a timeout leaves the payment in
            # "processing" because the outcome is genuinely unknown (the provider may have
            # completed it). Its funds are reserved in the payment-holds ledger so they cannot
            # be spent again, and reconciliation settles them from the provider's record.
            payment.status = transition(PaymentStatus.PENDING, PaymentStatus.PROCESSING).value
            result = await provider.process_payment(session, payment)
            payment.provider_reference = result.provider_reference
            payment.provider_status = result.provider_status
            session.add(_payment_event(
                "payment.processed.v1", payment,
                providerMode=result.mode, providerStatus=result.provider_status,
                accepted=result.accepted,
            ))
            if not result.accepted:
                payment.status = transition(
                    PaymentStatus.PROCESSING, PaymentStatus.FAILED,
                ).value
                session.add(_payment_event(
                    "payment.failed.v1", payment, providerMode=result.mode,
                ))
            elif result.mode == MODE_TIMEOUT:
                holds_ledger_id = (
                    await system_ledger(session, PAYMENT_HOLDS, request.currency)
                ).id
                await post_transfer(session, f"payment:{payment.id}", request.currency,
                                    source_ledger.id, holds_ledger_id, request.amount_minor)
            else:
                payment.status = transition(
                    PaymentStatus.PROCESSING, PaymentStatus.COMPLETED,
                ).value
                await post_transfer(session, f"payment:{payment.id}", request.currency,
                                    source_ledger.id, destination_ledger.id,
                                    request.amount_minor)
                session.add(_payment_event(
                    "ledger.posted.v1", payment,
                    journalReference=f"payment:{payment.id}",
                ))
                session.add(_payment_event(
                    "transaction.created.v1", payment,
                    journalReference=f"payment:{payment.id}",
                ))
                session.add(completed_event(payment))
                # Ask the operations side to reconcile this transfer against the provider.
                session.add(_payment_event("reconciliation.required.v1", payment))

        aml_codes = aml_domain.evaluate_aml(fraud_context)
        for code in aml_codes:
            session.add(AmlAlert(
                customer_id=customer.id, payment_id=payment.id, rule_code=code, status="open",
                details={
                    "amountMinor": request.amount_minor,
                    "paymentsLastHour": fraud_context["payments_last_hour"],
                    "paymentsLast10Min": fraud_context["payments_last_10_min"],
                    "distinctBeneficiaries24h": fraud_context["distinct_beneficiaries_24h"],
                },
            ))
            session.add(OutboxEvent(
                id=uuid4(), aggregate_id=payment.id,
                event_type="aml.alert.opened.v1", schema_version=1,
                payload={"customerId": str(customer.id), "paymentId": str(payment.id),
                         "ruleCode": code},
            ))

        if payment.status == PaymentStatus.FAILED.value:
            # The provider declined the transfer: an outcome worth its own audit trail, even
            # though nothing was posted and the payment cannot be found in the ledger.
            await record_audit(
                session, customer.id, "payment.failed", "payment", payment.id,
                request=http_request,
                new_value={"providerMode": result.mode,
                           "providerStatus": result.provider_status},
            )

        # Use one response representation for the persisted replay and immediate return.
        response = PaymentResponse(
            id=payment.id, status=payment.status, amount_minor=payment.amount_minor,
            currency=payment.currency, created_at=payment.created_at or now,
            reference=payment.reference,
            provider_reference=payment.provider_reference,
            provider_status=payment.provider_status,
            risk=RiskView(score=evaluation.score, level=evaluation.level,
                          decision=evaluation.decision, reasons=list(evaluation.reasons)),
        )
        record = await session.get(IdempotencyRecord, claimed_id)
        if record is None:
            raise RuntimeError("Idempotency claim disappeared inside the transaction")
        # Store the full transport body using camelCase keys so replays are byte-identical
        # to the original response, including risk fields.
        record.response_json = response.model_dump(mode="json", by_alias=True)
        await record_audit(session, customer.id, "payment.created", "payment", payment.id,
                           request=http_request, new_value={"amountMinor": request.amount_minor})
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        raise


@router.get("", response_model=PaymentPage)
async def list_payments(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> PaymentPage:
    query = select(Payment).where(Payment.customer_id == customer.id)
    if status_filter is not None:
        query = query.where(Payment.status == status_filter)
    if cursor is not None:
        created_at, payment_id = _decode_cursor(cursor)
        query = query.where(
            (Payment.created_at < created_at)
            | ((Payment.created_at == created_at) & (Payment.id < payment_id))
        )
    rows = list((await session.scalars(
        query.order_by(Payment.created_at.desc(), Payment.id.desc()).limit(limit + 1)
    )).all())
    next_cursor = _encode_cursor(rows[-1]) if len(rows) > limit else None
    return PaymentPage(items=[_response(row) for row in rows[:limit]], next_cursor=next_cursor)


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> PaymentResponse:
    payment = await session.scalar(select(Payment).where(
        Payment.id == payment_id, Payment.customer_id == customer.id,
    ))
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return _response(payment)
