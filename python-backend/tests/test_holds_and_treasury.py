"""Payments held for fraud review, their release/refund, and treasury deposits."""

import uuid as uuidlib

from sqlalchemy import delete, select

from app.infrastructure.models import Customer, FraudCase, FraudRule, OutboxEvent
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, get_balance, register_and_login


async def _make_staff(session, customer_id: str, *roles: str) -> None:
    customer = await session.get(Customer, uuidlib.UUID(customer_id))
    customer.roles = ["customer", *roles]
    await session.commit()


async def _review_rule(session) -> None:
    # Weight 50 lands in the review band (40-70): flagged, not blocked.
    await session.execute(delete(FraudRule).where(FraudRule.code == "large_amount"))
    session.add(FraudRule(code="large_amount", description="test", threshold_minor=100, weight=50))
    await session.commit()


async def _held_payment(client, session):
    alice = await register_and_login(client, f"hold-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"hold2-{uuidlib.uuid4().hex[:8]}@example.com")
    analyst = await register_and_login(client, f"analyst-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, analyst["customer_id"], "fraud_analyst")
    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 100_000)
    beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_account["id"], "label": "bob",
    }, headers=alice["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary}/verify", headers=alice["headers"])
    await _review_rule(session)

    response = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"], "beneficiaryId": beneficiary,
        "amountMinor": 30_000, "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert response.status_code == 201, response.text
    payment = response.json()
    case_id = await session.scalar(
        select(FraudCase.id).where(FraudCase.payment_id == uuidlib.UUID(payment["id"])),
    )
    return alice, bob, analyst, alice_account, bob_account, payment, case_id


async def _payment(client, user, payment_id: str) -> dict:
    response = await client.get(f"/api/v1/payments/{payment_id}", headers=user["headers"])
    return response.json()


async def _events(session, payment_id: str) -> set[str]:
    rows = await session.scalars(
        select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == uuidlib.UUID(payment_id)),
    )
    return set(rows.all())


@requires_database
async def test_flagged_payment_is_held_until_released(client, session):
    alice, bob, analyst, alice_acc, bob_acc, payment, case_id = await _held_payment(client, session)
    assert payment["status"] == "pending"
    assert payment["risk"]["decision"] == "review"
    # Funds left Alice (no double spend) but have not reached Bob.
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 70_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0
    assert "payment.completed.v1" not in await _events(session, payment["id"])

    reviewed = await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                                 json={"resolution": "false_positive"},
                                 headers=analyst["headers"])
    assert reviewed.status_code == 200, reviewed.text

    status = await _payment(client, alice, payment["id"])
    assert status["status"] == "completed"
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 30_000
    assert "payment.completed.v1" in await _events(session, payment["id"])

    again = await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                              json={"resolution": "closed"}, headers=analyst["headers"])
    assert again.status_code == 409


@requires_database
async def test_confirmed_fraud_refunds_the_held_payment(client, session):
    alice, bob, analyst, alice_acc, bob_acc, payment, case_id = await _held_payment(client, session)

    reviewed = await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                                 json={"resolution": "closed"}, headers=analyst["headers"])
    assert reviewed.status_code == 200, reviewed.text

    status = await _payment(client, alice, payment["id"])
    assert status["status"] == "rejected"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0
    assert "payment.rejected.v1" in await _events(session, payment["id"])


@requires_database
async def test_escalation_keeps_the_payment_held(client, session):
    alice, _, analyst, alice_acc, _, payment, case_id = await _held_payment(client, session)
    reviewed = await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                                 json={"resolution": "escalated"}, headers=analyst["headers"])
    assert reviewed.status_code == 200
    status = await _payment(client, alice, payment["id"])
    assert status["status"] == "pending"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 70_000


@requires_database
async def test_finance_user_deposits_idempotently(client, session):
    customer = await register_and_login(client, f"dep-{uuidlib.uuid4().hex[:8]}@example.com")
    finance = await register_and_login(client, f"fin-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, finance["customer_id"], "finance_user")
    account = await create_account(client, customer["headers"])
    key = uuidlib.uuid4().hex
    body = {"amountMinor": 250_000, "reference": "opening balance"}
    path = f"/api/v1/admin/accounts/{account['id']}/deposits"

    denied = await client.post(path, json=body,
                               headers={**customer["headers"], "Idempotency-Key": key})
    assert denied.status_code == 403

    staff = {**finance["headers"], "Idempotency-Key": key}
    first = await client.post(path, json=body, headers=staff)
    assert first.status_code == 201, first.text
    assert first.json()["balanceMinor"] == 250_000
    replay = await client.post(path, json=body, headers=staff)
    assert replay.status_code == 201
    assert replay.json()["journalId"] == first.json()["journalId"]
    conflict = await client.post(path, json={**body, "amountMinor": 1}, headers=staff)
    assert conflict.status_code == 409

    assert await get_balance(client, customer["headers"], account["id"]) == 250_000
    listed = await client.get(f"/api/v1/admin/customers/{customer['customer_id']}/accounts",
                              headers=finance["headers"])
    assert listed.status_code == 200
    assert listed.json()["items"][0]["balanceMinor"] == 250_000
    event = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.event_type == "account.funded.v1",
        OutboxEvent.aggregate_id == uuidlib.UUID(account["id"]),
    ))
    assert event is not None and event.payload["amountMinor"] == 250_000
