"""Admin-only customer, fraud rule, account, audit, user, and dead-letter management."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CustomerResponse, require_roles
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    AuditLog,
    Customer,
    CustomerAccount,
    FraudRule,
    OutboxDeadLetter,
    OutboxEvent,
)
from app.services.audit import record_audit
from app.services.configuration import invalidate_fraud_rules

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Roles a compliance officer or finance user can hold, kept in sync with the seed and docs.
KNOWN_ROLES = ("customer", "admin", "operations_user", "fraud_analyst", "compliance_officer",
               "finance_user")


class AdminCustomerView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    email: str  # output: stored value, not re-validated
    status: str
    roles: list[str]
    created_at: datetime


class AdminCustomerPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[AdminCustomerView]


class UpdateCustomerStatusRequest(BaseModel):
    status: Literal["pending", "active", "suspended", "blocked", "closed"]


class FraudRuleView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    code: str
    description: str
    threshold_minor: int | None
    weight: int
    enabled: bool


class FraudRulePage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[FraudRuleView]


class UpdateFraudRuleRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    enabled: bool | None = None
    weight: int | None = Field(default=None, ge=0, le=1000)
    threshold_minor: int | None = Field(default=None, ge=0)


class AuditLogView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    ip_address: str | None
    correlation_id: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[AuditLogView]


class UpdateAccountStatusRequest(BaseModel):
    status: Literal["active", "frozen", "suspended", "closed"]


class AccountStatusResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    status: str


@router.get("/customers", response_model=AdminCustomerPage)
async def list_customers(
    # Read-only, so finance users can find the customer whose account they are funding.
    customer: Customer = Depends(require_roles("admin", "finance_user")),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=500),
) -> AdminCustomerPage:
    rows = list(
        (await session.scalars(
            select(Customer).order_by(Customer.created_at.desc()).limit(limit)
        )).all()
    )
    return AdminCustomerPage(items=[AdminCustomerView(
        id=row.id, email=row.email_normalized, status=row.status,
        roles=row.roles or ["customer"], created_at=row.created_at,
    ) for row in rows])


@router.patch("/customers/{customer_id}/status", response_model=CustomerResponse)
async def update_customer_status(
    customer_id: UUID,
    request: UpdateCustomerStatusRequest,
    http_request: Request,
    admin: Customer = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> CustomerResponse:
    target = await session.get(Customer, customer_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    old_status = target.status
    target.status = request.status
    await record_audit(session, admin.id, "admin.customer_status_changed", "customer", target.id,
                       request=http_request, old_value={"status": old_status},
                       new_value={"status": target.status})
    if request.status in {"blocked", "suspended", "closed"} and old_status not in {
        "blocked", "suspended", "closed",
    }:
        # Spec section 22: an AccountBlocked notification. Emitted as an event so the
        # notification service, not the admin endpoint, owns channel fan-out.
        session.add(OutboxEvent(
            id=uuid4(), aggregate_id=target.id, event_type="customer.blocked.v1",
            schema_version=1,
            payload={"customerId": str(target.id), "status": request.status,
                     "previousStatus": old_status},
        ))
    await session.commit()
    return CustomerResponse(id=target.id, email=target.email_normalized, status=target.status)


@router.get("/fraud-rules", response_model=FraudRulePage)
async def list_fraud_rules(
    customer: Customer = Depends(require_roles("admin", "fraud_analyst")),
    session: AsyncSession = Depends(get_session),
) -> FraudRulePage:
    rows = (await session.scalars(select(FraudRule).order_by(FraudRule.code))).all()
    return FraudRulePage(items=[FraudRuleView(
        id=row.id, code=row.code, description=row.description, threshold_minor=row.threshold_minor,
        weight=row.weight, enabled=row.enabled,
    ) for row in rows])


@router.patch("/fraud-rules/{code}", response_model=FraudRuleView)
async def update_fraud_rule(
    code: str,
    request: UpdateFraudRuleRequest,
    http_request: Request,
    admin: Customer = Depends(require_roles("admin", "fraud_analyst")),
    session: AsyncSession = Depends(get_session),
) -> FraudRuleView:
    rule = await session.scalar(select(FraudRule).where(FraudRule.code == code))
    if rule is None:
        raise HTTPException(status_code=404, detail="Fraud rule not found")
    old = {"enabled": rule.enabled, "weight": rule.weight, "thresholdMinor": rule.threshold_minor}
    if request.enabled is not None:
        rule.enabled = request.enabled
    if request.weight is not None:
        rule.weight = request.weight
    if request.threshold_minor is not None:
        rule.threshold_minor = request.threshold_minor
    await record_audit(session, admin.id, "admin.fraud_rule_updated", "fraud_rule", rule.id,
                       request=http_request, old_value=old,
                       new_value={"enabled": rule.enabled, "weight": rule.weight,
                                  "thresholdMinor": rule.threshold_minor})
    await session.commit()
    # After the commit, so a concurrent reader cannot re-cache the old rules (see cache.py).
    await invalidate_fraud_rules()
    return FraudRuleView(
        id=rule.id, code=rule.code, description=rule.description,
        threshold_minor=rule.threshold_minor, weight=rule.weight, enabled=rule.enabled,
    )


@router.get("/audit-logs", response_model=AuditLogPage)
async def list_audit_logs(
    customer: Customer = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> AuditLogPage:
    rows = (await session.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    )).all()
    return AuditLogPage(items=[AuditLogView(
        id=row.id, actor_id=row.actor_id, action=row.action, entity_type=row.entity_type,
        entity_id=row.entity_id, ip_address=row.ip_address, correlation_id=row.correlation_id,
        created_at=row.created_at,
    ) for row in rows])


@router.patch("/accounts/{account_id}/status", response_model=AccountStatusResponse)
async def update_account_status(
    account_id: UUID,
    request: UpdateAccountStatusRequest,
    http_request: Request,
    admin: Customer = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> AccountStatusResponse:
    account = await session.get(CustomerAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    old_status = account.status
    account.status = request.status
    await record_audit(session, admin.id, "admin.account_status_changed", "account", account.id,
                       request=http_request, old_value={"status": old_status},
                       new_value={"status": account.status})
    await session.commit()
    return AccountStatusResponse(id=account.id, status=account.status)


# --------------------------------------------------------------------------------------------
# User management (spec section 25: Admin — "Manage users")

class AdminUserView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    email: str
    status: str
    roles: list[str]
    created_at: datetime


class AdminUserPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[AdminUserView]


class UpdateUserRolesRequest(BaseModel):
    roles: list[Literal[
        "customer", "admin", "operations_user", "fraud_analyst", "compliance_officer",
        "finance_user",
    ]] = Field(min_length=1, max_length=6)


@router.get("/users", response_model=AdminUserPage)
async def list_users(
    customer: Customer = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> AdminUserPage:
    rows = (await session.scalars(
        select(Customer).order_by(Customer.created_at.desc()).limit(limit)
    )).all()
    return AdminUserPage(items=[AdminUserView(
        id=row.id, email=row.email_normalized, status=row.status,
        roles=row.roles or ["customer"], created_at=row.created_at,
    ) for row in rows])


@router.patch("/users/{user_id}/roles", response_model=AdminUserView)
async def update_user_roles(
    user_id: UUID,
    request: UpdateUserRolesRequest,
    http_request: Request,
    admin: Customer = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> AdminUserView:
    """Change a user's roles. An admin cannot drop their own admin role (lockout guard)."""
    target = await session.get(Customer, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    requested = sorted(set(request.roles))
    if target.id == admin.id and "admin" not in requested:
        raise HTTPException(status_code=422, detail="cannot_remove_own_admin_role")
    old_roles = sorted(target.roles or ["customer"])
    if requested != old_roles:
        target.roles = requested
        await record_audit(session, admin.id, "admin.user_roles_changed", "user", target.id,
                           request=http_request, old_value={"roles": old_roles},
                           new_value={"roles": requested})
        await session.commit()
    return AdminUserView(
        id=target.id, email=target.email_normalized, status=target.status,
        roles=target.roles or ["customer"], created_at=target.created_at,
    )


# --------------------------------------------------------------------------------------------
# Outbox dead letters (spec sections 18-19: retry with backoff, then manual replay)

class DeadLetterView(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    outbox_event_id: UUID
    event_type: str
    failure_reason: str
    replayed: bool
    created_at: datetime


class DeadLetterPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[DeadLetterView]


class DeadLetterReplayResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    dead_letter_id: UUID
    new_outbox_event_id: UUID


@router.get("/outbox/dead-letters", response_model=DeadLetterPage)
async def list_dead_letters(
    customer: Customer = Depends(require_roles("admin", "operations_user")),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> DeadLetterPage:
    rows = (await session.scalars(
        select(OutboxDeadLetter).order_by(OutboxDeadLetter.created_at.desc()).limit(limit)
    )).all()
    return DeadLetterPage(items=[DeadLetterView(
        id=row.id, outbox_event_id=row.outbox_event_id, event_type=row.event_type,
        failure_reason=row.failure_reason, replayed=row.replayed_at is not None,
        created_at=row.created_at,
    ) for row in rows])


@router.post("/outbox/dead-letters/{dead_letter_id}/replay",
             response_model=DeadLetterReplayResponse)
async def replay_dead_letter(
    dead_letter_id: UUID,
    http_request: Request,
    admin: Customer = Depends(require_roles("admin", "operations_user")),
    session: AsyncSession = Depends(get_session),
) -> DeadLetterReplayResponse:
    """Re-queue a dead-lettered event as a fresh outbox row.

    The original dead letter is kept (never deleted) and marked, so an operator can always see
    what was replayed, when, and why it failed in the first place.
    """
    try:
        dead_letter = await session.get(OutboxDeadLetter, dead_letter_id)
        if dead_letter is None:
            raise HTTPException(status_code=404, detail="Dead letter not found")
        if dead_letter.replayed_at is not None:
            raise HTTPException(status_code=409, detail="dead_letter_already_replayed")
        event = OutboxEvent(
            id=uuid4(), aggregate_id=dead_letter.outbox_event_id,
            event_type=dead_letter.event_type, schema_version=1,
            payload=dead_letter.payload or {},
        )
        session.add(event)
        dead_letter.replayed_at = datetime.now(UTC)
        await record_audit(session, admin.id, "admin.dead_letter_replayed", "outbox_dead_letter",
                           dead_letter.id, request=http_request,
                           new_value={"eventType": dead_letter.event_type,
                                      "newOutboxEventId": str(event.id)})
        await session.commit()
        return DeadLetterReplayResponse(dead_letter_id=dead_letter.id, new_outbox_event_id=event.id)
    except Exception:
        await session.rollback()
        raise
