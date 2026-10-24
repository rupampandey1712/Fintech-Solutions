"""KYC application lifecycle with document storage and sanctions screening."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, effective_roles, get_current_customer
from app.config.settings import get_settings
from app.domain.sanctions import POTENTIAL_MATCH, screen_name
from app.infrastructure.database import get_session
from app.infrastructure.models import KycApplication, KycDocument, OutboxEvent
from app.services.audit import record_audit
from app.services.kyc_document_store import DocumentTooLargeError, save_document

router = APIRouter(prefix="/api/v1/kyc", tags=["kyc"])


class CreateKycRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    full_name: str = Field(min_length=1, max_length=120)
    country: str = Field(pattern=r"^[A-Z]{2}$")


class KycDocumentRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    document_type: str = Field(pattern=r"^(passport|national_id|proof_of_address)$")
    content_base64: str = Field(min_length=1)


class KycDocumentResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    document_type: str
    blob_path: str
    status: str
    uploaded_at: datetime


class KycApplicationResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    status: str
    full_name: str
    country: str
    rejection_reason: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    created_at: datetime
    documents: list[KycDocumentResponse] = []


class KycApplicationPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[KycApplicationResponse]
    next_cursor: str | None = None


class ReviewKycRequest(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=300)


def _document_view(document: KycDocument) -> KycDocumentResponse:
    return KycDocumentResponse(
        id=document.id, document_type=document.document_type, blob_path=document.blob_path,
        status=document.status, uploaded_at=document.uploaded_at or datetime.now(UTC),
    )


def _application_view(
    application: KycApplication, documents: list[KycDocument],
) -> KycApplicationResponse:
    return KycApplicationResponse(
        id=application.id, status=application.status, full_name=application.full_name,
        country=application.country, rejection_reason=application.rejection_reason,
        submitted_at=application.submitted_at, decided_at=application.decided_at,
        created_at=application.created_at,
        documents=[_document_view(doc) for doc in documents],
    )


async def _approve_and_activate(
    session: AsyncSession, application: KycApplication, now: datetime,
) -> None:
    """Mark the application approved, activate the customer, and emit the approval event."""
    application.status = "approved"
    application.decided_at = now
    customer = await session.get(Customer, application.customer_id)
    customer.status = "active"
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=application.id, event_type="kyc.approved.v1", schema_version=1,
        payload={
            "customerId": str(customer.id), "kycApplicationId": str(application.id),
            "decidedAt": now.isoformat(),
        },
    ))


@router.post("", response_model=KycApplicationResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    request: CreateKycRequest,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycApplicationResponse:
    application = KycApplication(
        customer_id=customer.id, full_name=request.full_name.strip(), country=request.country,
    )
    session.add(application)
    await session.commit()
    await session.refresh(application)
    return _application_view(application, [])


@router.post("/{application_id}/documents", response_model=KycDocumentResponse,
             status_code=status.HTTP_201_CREATED)
async def add_document(
    application_id: UUID,
    request: KycDocumentRequest,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycDocumentResponse:
    application = await session.scalar(select(KycApplication).where(
        KycApplication.id == application_id, KycApplication.customer_id == customer.id,
    ))
    if application is None:
        raise HTTPException(status_code=404, detail="KYC application not found")
    if application.status not in {"pending", "under_review"}:
        raise HTTPException(status_code=409, detail="KYC application no longer accepts documents")
    try:
        blob_path = await save_document(request.content_base64)
    except DocumentTooLargeError as exc:
        raise HTTPException(status_code=422, detail="document_too_large") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid base64 document content") from exc
    except AzureError as exc:
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from exc
    document = KycDocument(
        kyc_application_id=application.id, document_type=request.document_type,
        blob_path=blob_path,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return _document_view(document)


@router.post("/{application_id}/submit", response_model=KycApplicationResponse)
async def submit_application(
    application_id: UUID,
    http_request: Request,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycApplicationResponse:
    application = await session.scalar(select(KycApplication).where(
        KycApplication.id == application_id, KycApplication.customer_id == customer.id,
    ))
    if application is None:
        raise HTTPException(status_code=404, detail="KYC application not found")
    if application.status != "pending":
        raise HTTPException(status_code=409, detail="KYC application has already been submitted")
    settings = get_settings()
    documents = list((await session.scalars(
        select(KycDocument).where(KycDocument.kyc_application_id == application.id)
    )).all())
    if not documents:
        raise HTTPException(status_code=422, detail="kyc_documents_required")
    application.submitted_at = datetime.now(UTC)
    # Spec section 17: KycSubmitted announces the review hand-off (the compliance reviewer's
    # queue subscribes to it), independent of the screening decision below.
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=application.id, event_type="kyc.submitted.v1", schema_version=1,
        payload={"customerId": str(customer.id), "kycApplicationId": str(application.id),
                 "fullName": application.full_name, "country": application.country,
                 "documentCount": len(documents)},
    ))
    if screen_name(application.full_name, settings.sanctions_names) == POTENTIAL_MATCH:
        application.status = "rejected"
        application.rejection_reason = "Sanctions screening potential match"
        application.decided_at = datetime.now(UTC)
    else:
        await _approve_and_activate(
            session, application, application.decided_at or datetime.now(UTC),
        )
        await record_audit(session, customer.id, "kyc.approved", "kyc_application", application.id,
                           request=http_request)
    await session.commit()
    await session.refresh(application)
    return _application_view(application, documents)


@router.get("", response_model=KycApplicationPage)
async def list_applications(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycApplicationPage:
    applications = list((await session.scalars(
        select(KycApplication).where(KycApplication.customer_id == customer.id)
        .order_by(KycApplication.created_at.desc())
    )).all())
    items = []
    for application in applications:
        documents = list((await session.scalars(
            select(KycDocument).where(KycDocument.kyc_application_id == application.id)
            .order_by(KycDocument.uploaded_at.asc())
        )).all())
        items.append(_application_view(application, documents))
    return KycApplicationPage(items=items, next_cursor=None)


@router.get("/{application_id}", response_model=KycApplicationResponse)
async def get_application(
    application_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycApplicationResponse:
    application = await session.scalar(select(KycApplication).where(
        KycApplication.id == application_id, KycApplication.customer_id == customer.id,
    ))
    if application is None:
        raise HTTPException(status_code=404, detail="KYC application not found")
    documents = list((await session.scalars(
        select(KycDocument).where(KycDocument.kyc_application_id == application.id)
        .order_by(KycDocument.uploaded_at.asc())
    )).all())
    return _application_view(application, documents)


@router.post("/{application_id}/review", response_model=KycApplicationResponse)
async def review_application(
    application_id: UUID,
    request: ReviewKycRequest,
    http_request: Request,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> KycApplicationResponse:
    roles = effective_roles(customer)
    is_reviewer = "admin" in roles or "fraud_analyst" in roles
    application = await session.scalar(select(KycApplication).where(
        KycApplication.id == application_id,
    ))
    if application is None:
        raise HTTPException(status_code=404, detail="KYC application not found")
    if not is_reviewer:
        raise HTTPException(status_code=403, detail="forbidden")
    if application.status not in {"pending", "under_review"}:
        raise HTTPException(status_code=409, detail="KYC application is not awaiting review")
    now = datetime.now(UTC)
    application.decided_at = now
    application.decided_by = customer.id
    if request.approve:
        await _approve_and_activate(session, application, now)
        action = "kyc.approved"
    else:
        application.status = "rejected"
        application.rejection_reason = request.reason or "Rejected during review"
        action = "kyc.rejected"
    await record_audit(session, customer.id, action, "kyc_application", application.id,
                       request=http_request)
    await session.commit()
    documents = list((await session.scalars(
        select(KycDocument).where(KycDocument.kyc_application_id == application.id)
    )).all())
    return _application_view(application, documents)
