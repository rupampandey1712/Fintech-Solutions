"""Fraud case inspection and reviewer workflow."""

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.fraud_investigator import ModelFactory, gemini_model, investigate
from app.api.auth import Customer, effective_roles, get_current_customer
from app.config.settings import get_settings
from app.infrastructure.database import get_session
from app.infrastructure.models import FraudCase
from app.services.audit import record_audit
from app.services.investigation_facts import CaseFacts
from app.services.payment_holds import settle_held_payment

router = APIRouter(prefix="/api/v1/fraud", tags=["fraud"])

FINAL_CASE_STATUSES = {"closed", "false_positive"}
CASE_STATUSES = {"open", "investigating", "escalated", "closed", "false_positive"}


class FraudCaseView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    payment_id: UUID | None
    customer_id: UUID
    score: int
    level: str
    status: str
    reasons: list[str] = []
    resolution: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class FraudCasePage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[FraudCaseView]
    next_cursor: str | None = None


class ReviewCaseRequest(BaseModel):
    resolution: Literal["closed", "false_positive", "escalated"]
    note: str | None = Field(default=None, max_length=200)


def _view(case: FraudCase) -> FraudCaseView:
    return FraudCaseView(
        id=case.id, payment_id=case.payment_id, customer_id=case.customer_id, score=case.score,
        level=case.level, status=case.status, reasons=list(case.reasons or []),
        resolution=case.resolution, reviewed_at=case.reviewed_at, created_at=case.created_at,
    )


def _is_reviewer(customer: Customer) -> bool:
    roles = effective_roles(customer)
    return "admin" in roles or "fraud_analyst" in roles


@router.get("/cases", response_model=FraudCasePage)
async def list_cases(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> FraudCasePage:
    if status_filter is not None and status_filter not in CASE_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid fraud case status filter")
    query = select(FraudCase)
    if _is_reviewer(customer):
        if status_filter is not None:
            query = query.where(FraudCase.status == status_filter)
    else:
        query = query.where(FraudCase.customer_id == customer.id)
        if status_filter is not None:
            query = query.where(FraudCase.status == status_filter)
    rows = (await session.scalars(
        query.order_by(FraudCase.created_at.desc(), FraudCase.id.desc()).limit(limit)
    )).all()
    return FraudCasePage(items=[_view(row) for row in rows])


@router.post("/cases/{case_id}/review", response_model=FraudCaseView)
async def review_case(
    case_id: UUID,
    request: ReviewCaseRequest,
    http_request: Request,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> FraudCaseView:
    if not _is_reviewer(customer):
        raise HTTPException(status_code=403, detail="forbidden")
    # Locked so concurrent reviews of one case serialize; the second sees a final status.
    case = await session.scalar(
        select(FraudCase).where(FraudCase.id == case_id).with_for_update(),
    )
    if case is None:
        raise HTTPException(status_code=404, detail="Fraud case not found")
    if case.status in FINAL_CASE_STATUSES:
        raise HTTPException(status_code=409, detail="Fraud case is already resolved")
    previous = case.status
    case.status = request.resolution
    case.resolution = request.note
    case.reviewed_by = customer.id
    case.reviewed_at = datetime.now(UTC)
    # Release or refund the held payment in the same transaction as the decision.
    payment_status = await settle_held_payment(session, case.payment_id, request.resolution)
    await record_audit(session, customer.id, "fraud.case.reviewed", "fraud_case", case.id,
                       request=http_request, old_value={"status": previous},
                       new_value={"status": case.status, "paymentStatus": payment_status})
    await session.commit()
    return _view(case)


# --------------------------------------------------------------------------------------------
# Fraud investigator agent: gathers evidence and drafts a recommendation. It never decides; the
# reviewer acts on its draft through review_case above.

class InvestigationStepView(BaseModel):
    tool: str
    label: str


class InvestigationView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    case_id: UUID
    recommendation: Literal["false_positive", "closed", "escalated"]
    confidence: Literal["low", "medium", "high"]
    summary: str
    key_findings: list[str]
    suggested_note: str
    steps: list[InvestigationStepView]
    model: str
    generated_at: datetime


class AgentStatusView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    enabled: bool
    provider: str
    model: str


def get_investigator_model() -> ModelFactory | None:
    """The chat model factory, or None when no API key is configured. Overridden in tests."""
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        return None
    return lambda: gemini_model(api_key, settings.agent_model, settings.agent_timeout_seconds)


@router.get("/agent/status", response_model=AgentStatusView)
async def agent_status(
    customer: Customer = Depends(get_current_customer),
    model_factory: ModelFactory | None = Depends(get_investigator_model),
) -> AgentStatusView:
    settings = get_settings()
    return AgentStatusView(enabled=model_factory is not None and _is_reviewer(customer),
                           provider="gemini", model=settings.agent_model)


@router.post("/cases/{case_id}/investigate", response_model=InvestigationView)
async def investigate_case(
    case_id: UUID,
    http_request: Request,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    model_factory: ModelFactory | None = Depends(get_investigator_model),
) -> InvestigationView:
    if not _is_reviewer(customer):
        raise HTTPException(status_code=403, detail="forbidden")
    if model_factory is None:
        raise HTTPException(status_code=503, detail="agent_not_configured")
    case = await session.get(FraudCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Fraud case not found")
    if case.status in FINAL_CASE_STATUSES:
        raise HTTPException(status_code=409, detail="Fraud case is already resolved")
    settings = get_settings()
    try:
        result = await asyncio.wait_for(
            investigate(model_factory(), CaseFacts(session, case), settings.agent_max_steps),
            timeout=settings.agent_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="agent_timeout") from exc
    except HTTPException:
        raise
    except Exception as exc:  # provider outage, quota, malformed model output
        raise HTTPException(status_code=502, detail="agent_failed") from exc
    if result.report is None:
        # The model ran out of turns, or stopped without submitting a report.
        raise HTTPException(status_code=502, detail="agent_incomplete")
    report = result.report
    # Recorded so every AI-assisted review leaves a trace of what the agent saw and suggested.
    await record_audit(session, customer.id, "fraud.case.investigated", "fraud_case", case.id,
                       request=http_request,
                       new_value={"recommendation": report.recommendation,
                                  "confidence": report.confidence,
                                  "tools": [step.tool for step in result.steps],
                                  "model": settings.agent_model, "turns": result.model_turns})
    await session.commit()
    return InvestigationView(
        case_id=case.id, recommendation=report.recommendation, confidence=report.confidence,
        summary=report.summary, key_findings=report.key_findings,
        suggested_note=report.suggested_note,
        steps=[InvestigationStepView(tool=step.tool, label=step.label) for step in result.steps],
        model=settings.agent_model, generated_at=datetime.now(UTC),
    )
