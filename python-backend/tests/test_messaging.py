"""Outbox delivery, inbox de-duplication, and the events a payment emits."""

import asyncio
import uuid as uuidlib

from sqlalchemy import func, select

from app.infrastructure.models import Notification, OutboxDeadLetter, OutboxEvent
from app.services.messaging import apply_once
from app.worker import default_handlers, dispatch_batch, inline_delivery
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, register_and_login


async def _notification_count(session, customer_id) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(Notification)
        .where(Notification.customer_id == customer_id)
    ))


async def _verified_beneficiary(client, headers, destination_account_id) -> str:
    created = await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": destination_account_id, "label": "target",
    }, headers=headers)
    assert created.status_code == 201, created.text
    beneficiary_id = created.json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary_id}/verify", headers=headers)
    return beneficiary_id


@requires_database
async def test_redelivered_message_is_applied_once(client, session):
    user = await register_and_login(client, f"inbox-{uuidlib.uuid4().hex[:8]}@example.com")
    customer_id = uuidlib.UUID(user["customer_id"])
    payload = {"customerId": str(customer_id), "kycApplicationId": str(uuidlib.uuid4())}
    message_id = str(uuidlib.uuid4())

    first = await apply_once(session, "notifications", message_id, "kyc.approved.v1", payload,
                             default_handlers())
    second = await apply_once(session, "notifications", message_id, "kyc.approved.v1", payload,
                              default_handlers())

    assert (first, second) == (True, False)
    # One notification per channel — in_app, email, sms, push — not one per redelivery.
    assert await _notification_count(session, customer_id) == 4


@requires_database
async def test_failing_event_does_not_block_the_rest_of_the_batch(client, session):
    user = await register_and_login(client, f"batch-{uuidlib.uuid4().hex[:8]}@example.com")
    customer_id = uuidlib.UUID(user["customer_id"])
    bad = OutboxEvent(aggregate_id=uuidlib.uuid4(), event_type="kyc.approved.v1",
                      payload={"customerId": str(uuidlib.uuid4())})  # no such customer: FK fails
    good = OutboxEvent(aggregate_id=uuidlib.uuid4(), event_type="kyc.approved.v1",
                       payload={"customerId": str(customer_id)})
    session.add(bad)
    await session.flush()
    session.add(good)
    await session.commit()

    deliver = inline_delivery(default_handlers())
    await dispatch_batch(session, deliver, batch_size=10, max_attempts=1)

    await session.refresh(bad)
    await session.refresh(good)
    assert good.published_at is not None
    assert await _notification_count(session, customer_id) == 4
    dead = await session.scalar(
        select(OutboxDeadLetter).where(OutboxDeadLetter.outbox_event_id == bad.id),
    )
    assert dead is not None


@requires_database
async def test_payment_emits_completed_event(client, session):
    alice = await register_and_login(client, f"evt-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"evt2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 50_000)
    beneficiary_id = await _verified_beneficiary(client, alice["headers"], bob_account["id"])

    response = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"], "beneficiaryId": beneficiary_id,
        "amountMinor": 1_000, "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert response.status_code == 201, response.text

    event = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.event_type == "payment.completed.v1",
        OutboxEvent.aggregate_id == uuidlib.UUID(response.json()["id"]),
    ))
    assert event is not None
    assert event.payload["customerId"] == alice["customer_id"]
    assert event.payload["amountMinor"] == 1_000


@requires_database
async def test_opposite_direction_transfers_do_not_deadlock(client, session):
    alice = await register_and_login(client, f"dl-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"dl2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 50_000)
    await fund_account(session, bob_account["id"], 50_000)
    to_bob = await _verified_beneficiary(client, alice["headers"], bob_account["id"])
    to_alice = await _verified_beneficiary(client, bob["headers"], alice_account["id"])

    async def pay(user, source, beneficiary):
        return await client.post("/api/v1/payments", json={
            "sourceAccountId": source, "beneficiaryId": beneficiary,
            "amountMinor": 1_000, "currency": "USD",
        }, headers={**user["headers"], "Idempotency-Key": uuidlib.uuid4().hex})

    results = await asyncio.gather(*(
        pay(alice, alice_account["id"], to_bob) if i % 2 == 0
        else pay(bob, bob_account["id"], to_alice)
        for i in range(6)
    ))
    assert [r.status_code for r in results] == [201] * 6, [r.text for r in results]


@requires_database
async def test_own_account_cannot_be_a_beneficiary(client):
    alice = await register_and_login(client, f"self-{uuidlib.uuid4().hex[:8]}@example.com")
    account = await create_account(client, alice["headers"])

    response = await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": account["id"], "label": "me",
    }, headers=alice["headers"])
    assert response.status_code == 422
