import hashlib
import json

from app.api.payments import create_payment_request_hash

PAYLOAD = {
    "amount_minor": 50000,
    "beneficiary_id": "0b9e6b6e-8f0a-4a1e-9c2d-3f4a5b6c7d8e",
    "currency": "USD",
    "reference": None,
    "source_account_id": "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f",
}


def test_request_hash_is_sha256_of_compact_sorted_json():
    # Golden canonicalization contract shared with the .NET implementation:
    # lowercase ASCII, lexicographic key order, no whitespace, explicit null reference.
    canonical = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert create_payment_request_hash(PAYLOAD) == expected


def test_request_hash_is_hex_digest():
    digest = create_payment_request_hash(PAYLOAD)
    assert len(digest) == 64
    int(digest, 16)


def test_request_hash_changes_with_content():
    changed = dict(PAYLOAD, amount_minor=50001)
    assert create_payment_request_hash(changed) != create_payment_request_hash(PAYLOAD)
