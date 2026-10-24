"""Cached reads of payment configuration: fraud rules and account limits (cache-aside).

The cached JSON shapes are shared with the .NET API (same keys), so keep field names stable.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.cache import (
    ACCOUNT_LIMITS_TTL_SECONDS,
    FRAUD_RULES_KEY,
    FRAUD_RULES_TTL_SECONDS,
    account_limits_key,
    get_cache,
)
from app.infrastructure.models import AccountLimit, FraudRule


async def load_fraud_rules(session: AsyncSession) -> list[dict[str, Any]]:
    """All fraud rules as [{code, description, thresholdMinor, weight, enabled}], by code."""

    async def from_database() -> list[dict[str, Any]]:
        rows = (await session.scalars(select(FraudRule).order_by(FraudRule.code))).all()
        return [{"code": rule.code, "description": rule.description,
                 "thresholdMinor": rule.threshold_minor, "weight": rule.weight,
                 "enabled": rule.enabled} for rule in rows]

    return await get_cache().get_or_load(FRAUD_RULES_KEY, FRAUD_RULES_TTL_SECONDS, from_database)


async def invalidate_fraud_rules() -> None:
    await get_cache().invalidate(FRAUD_RULES_KEY)


async def load_account_limits(session: AsyncSession, account_id: UUID) -> dict[str, int] | None:
    """Limits as {perTransactionMinor, dailyMinor, weeklyMinor, monthlyMinor}, or None.

    Limits are written once when the account opens; anything that changes them later must call
    invalidate_account_limits after committing. Only the configured caps are cached: the
    running daily/weekly/monthly totals they are compared with always come from the database.
    """

    async def from_database() -> dict[str, int] | None:
        limits = await session.scalar(
            select(AccountLimit).where(AccountLimit.account_id == account_id),
        )
        if limits is None:
            return None  # not cached, so a limit row created later is picked up immediately
        return {"perTransactionMinor": limits.per_transaction_minor,
                "dailyMinor": limits.daily_minor, "weeklyMinor": limits.weekly_minor,
                "monthlyMinor": limits.monthly_minor}

    return await get_cache().get_or_load(account_limits_key(account_id),
                                         ACCOUNT_LIMITS_TTL_SECONDS, from_database)


async def invalidate_account_limits(account_id: UUID) -> None:
    await get_cache().invalidate(account_limits_key(account_id))
