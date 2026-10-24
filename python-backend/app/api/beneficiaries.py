"""Beneficiary enrollment, verification simulation, and activation cooling period."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_customer
from app.config.settings import get_settings
from app.infrastructure.database import get_session
from app.infrastructure.models import Beneficiary, Customer, CustomerAccount

router = APIRouter(prefix="/api/v1/beneficiaries", tags=["beneficiaries"])


class CreateBeneficiaryRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    destination_account_id: UUID
    label: str = Field(min_length=1, max_length=80)


class BeneficiaryResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    destination_account_id: UUID
    label: str
    status: str
    verified_at: datetime | None
    cooling_until: datetime | None
    created_at: datetime


class BeneficiaryPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[BeneficiaryResponse]
    next_cursor: str | None = None


def _view(beneficiary: Beneficiary) -> BeneficiaryResponse:
    return BeneficiaryResponse(
        id=beneficiary.id,
        destination_account_id=beneficiary.destination_account_id,
        label=beneficiary.label,
        status=beneficiary.status,
        verified_at=beneficiary.verified_at,
        cooling_until=beneficiary.cooling_until,
        created_at=beneficiary.created_at,
    )


@router.get("", response_model=BeneficiaryPage)
async def list_beneficiaries(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> BeneficiaryPage:
    rows = (await session.scalars(
        select(Beneficiary).where(Beneficiary.customer_id == customer.id)
        .order_by(Beneficiary.created_at.desc()).limit(100)
    )).all()
    return BeneficiaryPage(items=[_view(row) for row in rows])


@router.post("", response_model=BeneficiaryResponse, status_code=status.HTTP_201_CREATED)
async def create_beneficiary(
    request: CreateBeneficiaryRequest,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> BeneficiaryResponse:
    destination = await session.get(CustomerAccount, request.destination_account_id)
    if destination is None or destination.status != "active":
        raise HTTPException(status_code=404, detail="Destination account not found")
    if destination.customer_id == customer.id:
        raise HTTPException(status_code=422, detail="A beneficiary must belong to another customer")
    beneficiary = Beneficiary(
        customer_id=customer.id,
        destination_account_id=destination.id,
        label=request.label.strip(),
    )
    session.add(beneficiary)
    await session.commit()
    await session.refresh(beneficiary)
    return _view(beneficiary)


@router.post("/{beneficiary_id}/verify", response_model=BeneficiaryResponse)
async def verify_beneficiary(
    beneficiary_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> BeneficiaryResponse:
    beneficiary = await session.scalar(
        select(Beneficiary).where(
            Beneficiary.id == beneficiary_id, Beneficiary.customer_id == customer.id,
        )
    )
    if beneficiary is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found")
    if beneficiary.status != "pending_verification":
        raise HTTPException(status_code=409, detail="Beneficiary is not awaiting verification")
    now = datetime.now(UTC)
    beneficiary.verified_at = now
    beneficiary.cooling_until = now + timedelta(seconds=get_settings().beneficiary_cooling_seconds)
    beneficiary.status = "active" if beneficiary.cooling_until <= now else "cooling"
    await session.commit()
    await session.refresh(beneficiary)
    return _view(beneficiary)


@router.post("/{beneficiary_id}/activate", response_model=BeneficiaryResponse)
async def activate_beneficiary(
    beneficiary_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> BeneficiaryResponse:
    beneficiary = await session.scalar(
        select(Beneficiary).where(
            Beneficiary.id == beneficiary_id, Beneficiary.customer_id == customer.id,
        )
    )
    if beneficiary is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found")
    if beneficiary.status == "active":
        return _view(beneficiary)
    if beneficiary.status != "cooling" or beneficiary.cooling_until is None:
        raise HTTPException(
            status_code=409, detail="Beneficiary must be verified before activation",
        )
    if beneficiary.cooling_until > datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Beneficiary cooling period has not elapsed")
    beneficiary.status = "active"
    await session.commit()
    await session.refresh(beneficiary)
    return _view(beneficiary)


@router.post("/{beneficiary_id}/disable", response_model=BeneficiaryResponse)
async def disable_beneficiary(
    beneficiary_id: UUID,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> BeneficiaryResponse:
    beneficiary = await session.scalar(
        select(Beneficiary).where(
            Beneficiary.id == beneficiary_id, Beneficiary.customer_id == customer.id,
        )
    )
    if beneficiary is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found")
    beneficiary.status = "disabled"
    await session.commit()
    await session.refresh(beneficiary)
    return _view(beneficiary)
