"""Authenticated customer self-service routes."""

from fastapi import APIRouter, Depends

from app.api.auth import CustomerResponse, get_current_customer
from app.infrastructure.models import Customer

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.get("/me", response_model=CustomerResponse)
async def get_me(customer: Customer = Depends(get_current_customer)) -> CustomerResponse:
    return CustomerResponse(id=customer.id, email=customer.email_normalized, status=customer.status)
