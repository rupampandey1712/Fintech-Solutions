"""Cache-aside: hits and misses, invalidation after writes, and Redis outages."""

import json
import os
import uuid as uuidlib

import pytest
from sqlalchemy import delete

from app.infrastructure.cache import FRAUD_RULES_KEY, Cache, account_limits_key, set_cache
from app.infrastructure.models import FraudRule
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, register_and_login
from tests.test_holds_and_treasury import _make_staff
from tests.test_messaging import _verified_beneficiary

REDIS_URL = os.environ.get("FINCORE_TEST_REDIS_URL", "")
requires_redis = pytest.mark.skipif(not REDIS_URL, reason="FINCORE_TEST_REDIS_URL is not set")


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class DownRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, key):
        self.calls += 1
        raise ConnectionError("down")

    async def set(self, key, value, ex=None):
        self.calls += 1
        raise ConnectionError("down")

    async def delete(self, *keys):
        self.calls += 1
        raise ConnectionError("down")


class Loader:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.value


async def test_cache_aside_loads_once_then_serves_from_redis():
    redis = FakeRedis()
    cache = Cache(redis)
    loader = Loader([{"code": "large_amount"}])
    assert await cache.get_or_load("k", 60, loader) == [{"code": "large_amount"}]
    assert await cache.get_or_load("k", 60, loader) == [{"code": "large_amount"}]
    assert loader.calls == 1 and (cache.hits, cache.misses) == (1, 1)
    assert redis.ttls["k"] == 60


async def test_invalidation_forces_a_reload():
    cache = Cache(FakeRedis())
    loader = Loader({"v": 1})
    await cache.get_or_load("k", 60, loader)
    loader.value = {"v": 2}
    await cache.invalidate("k")
    assert await cache.get_or_load("k", 60, loader) == {"v": 2}


async def test_missing_rows_are_not_cached():
    redis = FakeRedis()
    await Cache(redis).get_or_load("k", 60, Loader(None))
    assert "k" not in redis.store


async def test_redis_outage_falls_back_to_the_database_and_backs_off():
    clock = [0.0]
    redis = DownRedis()
    cache = Cache(redis, clock=lambda: clock[0])
    loader = Loader({"v": 1})
    for _ in range(3):
        assert await cache.get_or_load("k", 60, loader) == {"v": 1}
    assert loader.calls == 3
    assert redis.calls == 1  # one failed GET, then Redis is skipped during the back-off
    clock[0] = 31.0
    await cache.get_or_load("k", 60, loader)
    # Retried after the back-off: the GET fails again, so the SET is skipped too.
    assert redis.calls == 2


async def test_failed_invalidation_does_not_raise():
    await Cache(DownRedis()).invalidate("k")  # logged; the TTL bounds the staleness


# ---------------------------------------------------------------------------------------------
# Against real Redis and PostgreSQL


@pytest.fixture
async def redis_cache():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL)
    await client.flushdb()
    cache = Cache(client)
    set_cache(cache)
    yield client, cache
    set_cache(None)
    await client.aclose()


async def _pay(client, user, source, beneficiary, amount):
    return await client.post("/api/v1/payments", json={
        "sourceAccountId": source, "beneficiaryId": beneficiary, "amountMinor": amount,
        "currency": "USD",
    }, headers={**user["headers"], "Idempotency-Key": uuidlib.uuid4().hex})


@requires_database
@requires_redis
async def test_payments_read_cached_rules_and_limits_and_rule_edits_invalidate(
    client, session, redis_cache,
):
    redis, cache = redis_cache
    await session.execute(delete(FraudRule))
    session.add(FraudRule(code="large_amount", description="test", threshold_minor=1_000_000,
                          weight=30))
    await session.commit()
    alice = await register_and_login(client, f"c-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"c2-{uuidlib.uuid4().hex[:8]}@example.com")
    analyst = await register_and_login(client, f"an-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, analyst["customer_id"], "fraud_analyst")
    alice_acc = await create_account(client, alice["headers"])
    bob_acc = await create_account(client, bob["headers"])
    await fund_account(session, alice_acc["id"], 100_000)
    beneficiary = await _verified_beneficiary(client, alice["headers"], bob_acc["id"])

    first = await _pay(client, alice, alice_acc["id"], beneficiary, 1_000)
    assert first.status_code == 201
    cached_rules = json.loads(await redis.get(FRAUD_RULES_KEY))
    assert cached_rules == [{"code": "large_amount", "description": "test",
                             "thresholdMinor": 1_000_000, "weight": 30, "enabled": True}]
    limits_key = account_limits_key(uuidlib.UUID(alice_acc["id"]))
    assert json.loads(await redis.get(limits_key))["perTransactionMinor"] > 0

    hits_before = cache.hits
    await _pay(client, alice, alice_acc["id"], beneficiary, 1_000)
    assert cache.hits == hits_before + 2  # rules and limits both served from Redis

    # The admin edit commits, then deletes the key; the very next payment sees the new rule.
    patched = await client.patch("/api/v1/admin/fraud-rules/large_amount",
                                 json={"thresholdMinor": 100, "weight": 80},
                                 headers=analyst["headers"])
    assert patched.status_code == 200, patched.text
    assert await redis.get(FRAUD_RULES_KEY) is None
    blocked = await _pay(client, alice, alice_acc["id"], beneficiary, 1_000)
    assert blocked.status_code == 422
    assert blocked.json()["detail"] == "Payment blocked by fraud rules"


@requires_database
@requires_redis
async def test_balances_are_never_cached(client, session, redis_cache):
    redis, _ = redis_cache
    user = await register_and_login(client, f"b-{uuidlib.uuid4().hex[:8]}@example.com")
    account = await create_account(client, user["headers"])
    await fund_account(session, account["id"], 5_000)
    await client.get(f"/api/v1/accounts/{account['id']}", headers=user["headers"])
    await client.get("/api/v1/customers/me", headers=user["headers"])
    keys = [key.decode() for key in await redis.keys("*")]
    assert not [key for key in keys if "balance" in key or "customer" in key], keys
