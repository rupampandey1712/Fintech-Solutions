"""Rate limiter: window counting, the Redis path, and degradation when Redis is down."""

import os
from types import SimpleNamespace

import pytest

from app.api.ratelimit import (
    RateLimiter,
    RedisRateLimiter,
    account_principal,
    allow_account_login,
    classify_bucket,
)

REDIS_URL = os.environ.get("FINCORE_TEST_REDIS_URL", "")


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class BrokenRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def eval(self, *args):
        self.calls += 1
        raise ConnectionError("redis down")


def test_buckets():
    assert classify_bucket("POST", "/api/v1/auth/login") == "login"
    assert classify_bucket("POST", "/api/v1/payments") == "payment"
    assert classify_bucket("GET", "/api/v1/payments") == "default"
    assert classify_bucket("GET", "/health") == "exempt"


async def test_in_process_window_resets():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    assert [await limiter.acquire("login", "ip:1", 2) for _ in range(3)] == [True, True, False]
    clock.now += 60
    assert await limiter.acquire("login", "ip:1", 2)


def _login_request(limiter, limit: int, enabled: bool = True):
    settings = SimpleNamespace(rate_limit_enabled=enabled,
                               rate_limit_login_per_account_per_minute=limit)
    state = SimpleNamespace(rate_limiter=limiter, rate_limit_settings=lambda: settings)
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_login_budget_is_per_account_whatever_the_ip():
    clock = FakeClock()
    request = _login_request(RateLimiter(clock=clock), limit=2)
    # The request's IP plays no part: a guessing run spread over many IPs shares one budget.
    attempts = [await allow_account_login(request, "alice@example.com") for _ in range(3)]
    assert attempts == [True, True, False]
    assert await allow_account_login(request, "bob@example.com")  # other accounts unaffected
    clock.now += 60
    assert await allow_account_login(request, "alice@example.com")


async def test_login_budget_is_off_when_rate_limiting_is_disabled():
    request = _login_request(RateLimiter(clock=FakeClock()), limit=1, enabled=False)
    assert all([await allow_account_login(request, "alice@example.com") for _ in range(5)])


def test_account_principal_hides_the_email_and_matches_dotnet():
    # The .NET API derives the same principal (RateLimitTests), so Redis budgets are shared.
    assert account_principal("alice@example.com") == "account:ff8d9819fc0e12bf0d24892e45987e24"


async def test_falls_back_and_backs_off_when_redis_is_down():
    clock = FakeClock()
    redis = BrokenRedis()
    limiter = RedisRateLimiter(redis, RateLimiter(clock=clock), clock=clock)
    results = [await limiter.acquire("login", "ip:1", 2) for _ in range(3)]
    assert results == [True, True, False]  # still limited, by the in-process fallback
    assert redis.calls == 1  # one failure, then Redis is skipped during the back-off


@pytest.mark.skipif(not REDIS_URL, reason="FINCORE_TEST_REDIS_URL is not set")
async def test_redis_counters_are_shared_between_limiters():
    from uuid import uuid4

    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL)
    principal = f"ip:{uuid4().hex}"
    first = RedisRateLimiter(client, RateLimiter())
    second = RedisRateLimiter(client, RateLimiter())  # e.g. another replica
    assert await first.acquire("login", principal, 2)
    assert await second.acquire("login", principal, 2)
    assert not await first.acquire("login", principal, 2)
    await client.aclose()
