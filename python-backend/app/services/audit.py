"""Append-only audit trail persistence."""

from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models import AuditLog


async def record_audit(
    session: AsyncSession,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    request: Request | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
) -> None:
    correlation_id = (
        getattr(getattr(request, "state", None), "trace_id", None) if request is not None else None
    )
    ip_address = None
    if request is not None and request.client is not None:
        ip_address = request.client.host
    session.add(AuditLog(
        actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        ip_address=ip_address, correlation_id=correlation_id,
        old_value=old_value, new_value=new_value,
    ))
