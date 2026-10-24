"""Redis cache-aside for slow-changing configuration (fraud rules, account limits).

Read path: try Redis; on a miss load from PostgreSQL and store the result with a TTL.
Write path: commit to PostgreSQL first, then delete the key, so the next reader reloads it.
Deleting *after* commit matters: deleting before would let a concurrent reader cache the old
row again between the delete and the commit.

The .NET API uses the same keys and JSON shapes, so an invalidation by either implementation
is seen by both. Redis is an optimisation, never a dependency: if it is unreachable, reads fall
through to the database and Redis is retried after a back-off. The TTL bounds staleness if an
invalidation itself fails.

Never cached: balances and anything used to authenticate or authorise a request (customer
status, roles). Those are always read from the database.
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()

FRAUD_RULES_KEY = "fincore:cache:v1:fraud-rules"
FRAUD_RULES_TTL_SECONDS = 60
ACCOUNT_LIMITS_TTL_SECONDS = 600
REDIS_RETRY_SECONDS = 30.0


def _record(result: str, key: str) -> None:
    from app.infrastructure.telemetry import cache_requests_total

    # The key family (fraud-rules, account limits) rather than the key: bounded label values.
    family = "account-limits" if key.endswith(":limits") else key.rsplit(":", 1)[-1]
    cache_requests_total.add(1, {"result": result, "cache": family})


def account_limits_key(account_id: UUID) -> str:
    return f"fincore:cache:v1:account:{account_id}:limits"


class Cache:
    def __init__(self, redis, clock=time.monotonic) -> None:
        self._redis = redis
        self._clock = clock
        self._retry_at = 0.0
        self.hits = 0
        self.misses = 0

    def _available(self) -> bool:
        return self._redis is not None and self._clock() >= self._retry_at

    def _degrade(self, operation: str, exc: Exception) -> None:
        self._retry_at = self._clock() + REDIS_RETRY_SECONDS
        logger.warning("cache_unavailable", operation=operation, error_type=type(exc).__name__,
                       retry_in_seconds=REDIS_RETRY_SECONDS)

    async def get_or_load(self, key: str, ttl_seconds: int,
                          loader: Callable[[], Awaitable[Any]]) -> Any:
        if self._available():
            try:
                cached = await self._redis.get(key)
                if cached is not None:
                    self.hits += 1
                    _record("hit", key)
                    return json.loads(cached)
            except Exception as exc:  # timeouts, refused connections, protocol errors
                self._degrade("get", exc)
        self.misses += 1
        _record("miss", key)
        value = await loader()
        if value is not None and self._available():
            try:
                await self._redis.set(key, json.dumps(value), ex=ttl_seconds)
            except Exception as exc:
                self._degrade("set", exc)
        return value

    async def invalidate(self, *keys: str) -> None:
        if not keys or self._redis is None:
            return
        try:
            await self._redis.delete(*keys)
        except Exception as exc:
            # The entry stays stale until its TTL expires; log loudly so it is visible.
            logger.error("cache_invalidation_failed", keys=list(keys),
                         error_type=type(exc).__name__)


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        url = get_settings().redis_url
        client = None
        if url:
            from redis.asyncio import Redis

            client = Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)
        _cache = Cache(client)
    return _cache


def set_cache(cache: Cache | None) -> None:
    """Replace the process cache (tests)."""
    global _cache
    _cache = cache
