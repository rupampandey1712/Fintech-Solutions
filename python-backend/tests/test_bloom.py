"""Bloom filter and the common-password check built on it."""

import uuid as uuidlib

import pytest

from app.domain.bloom import BloomFilter
from app.services.password_policy import BUNDLED_LIST, is_common, load_entries
from tests.conftest import requires_database


def test_positions_match_the_dotnet_implementation():
    # FinCore.Tests.BloomFilterTests asserts the same vector, so both APIs set the same bits.
    assert list(BloomFilter(1000, 4).positions("password1234")) == [336, 727, 118, 509]


def test_sizing_follows_the_optimal_formulas():
    bloom = BloomFilter.for_capacity(1_000_000, 0.001)
    # About 14.4 bits per item and 10 hashes at 0.1%: roughly 1.8 MB for a million passwords.
    assert bloom.bit_count == 14_377_588
    assert bloom.hash_count == 10


def test_there_are_no_false_negatives():
    items = [f"item-{n}" for n in range(5_000)]
    bloom = BloomFilter.of(items, 0.01)
    assert all(item in bloom for item in items)


def test_false_positive_rate_stays_near_the_target():
    bloom = BloomFilter.of([f"member-{n}" for n in range(10_000)], 0.01)
    probes = 50_000
    false_positives = sum(f"stranger-{n}" in bloom for n in range(probes))
    assert false_positives / probes < 0.02  # target 1%, with room for statistical noise


def test_invalid_parameters_are_refused():
    with pytest.raises(ValueError):
        BloomFilter(0, 1)
    with pytest.raises(ValueError):
        BloomFilter.for_capacity(10, 1.5)


def test_common_passwords_are_caught_case_insensitively():
    assert is_common("password1234")
    assert is_common("Password1234")
    assert is_common("  QWERTYUIOP123 ")
    assert not is_common("SuperSecurePass123")
    assert not is_common("correct-horse-battery-staple-9")


def test_every_bundled_entry_is_caught():
    assert all(is_common(entry) for entry in load_entries(BUNDLED_LIST))


@requires_database
async def test_registration_refuses_a_common_password(client):
    response = await client.post("/api/v1/auth/register", json={
        "email": f"bloom-{uuidlib.uuid4().hex[:8]}@example.com", "password": "Password1234",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "password_too_common"
