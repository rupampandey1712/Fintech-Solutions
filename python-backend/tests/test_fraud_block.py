"""Fraud rules configured in the database block payments above the threshold."""

import uuid as uuidlib

from sqlalchemy import delete, select

from app.infrastructure.models import FraudRule
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, register_and_login


@requires_database
async def test_blocked_payment_leaves_no_row(client, session):
    alice = await register_and_login(client, f"fraud-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"fraud2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 500_000)

    beneficiary = await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_account["id"], "label": "fraud target",
    }, headers=alice["headers"])
    beneficiary_id = beneficiary.json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary_id}/verify", headers=alice["headers"])

    # Rules are seeded by the app lifespan, which the ASGI test transport does not run, so the
    # test owns its rule. Weight 80 alone crosses the block threshold (score > 70).
    await session.execute(delete(FraudRule).where(FraudRule.code == "large_amount"))
    session.add(FraudRule(code="large_amount", description="test threshold",
                          threshold_minor=100, weight=80))
    await session.commit()

    response = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"],
        "beneficiaryId": beneficiary_id,
        "amountMinor": 400_000,
        "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Payment blocked by fraud rules"

    from app.infrastructure.models import Payment

    count = await session.scalar(
        select(Payment).where(Payment.source_account_id == alice_account["id"],
                              Payment.amount_minor == 400_000)
    )
    assert count is None

    retry = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"],
        "beneficiaryId": beneficiary_id,
        "amountMinor": 50,
        "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert retry.status_code == 201, retry.text
    assert retry.json()["risk"]["decision"] == "allow"
