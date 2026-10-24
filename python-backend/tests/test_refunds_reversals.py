"""Refunds (partial, repeated, idempotent) and reversals (mirror postings, never deletions)."""

import asyncio
import uuid as uuidlib

from sqlalchemy import case, func, select

from app.infrastructure.models import JournalEntry, JournalLine, OutboxEvent
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, get_balance, register_and_login
from tests.test_holds_and_treasury import _held_payment, _make_staff
from tests.test_messaging import _verified_beneficiary


async def _ledger_is_balanced(session) -> bool:
    net = await session.scalar(select(func.coalesce(func.sum(case(
        (JournalLine.side == "debit", JournalLine.amount_minor), else_=-JournalLine.amount_minor,
    )), 0)))
    return int(net) == 0


async def _paid(client, session, amount=50_000, funding=100_000):
    """Alice pays Bob `amount`; returns users, accounts, payment and a finance user."""
    alice = await register_and_login(client, f"rf-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"rf2-{uuidlib.uuid4().hex[:8]}@example.com")
    staff = await register_and_login(client, f"ops-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "finance_user")
    alice_acc = await create_account(client, alice["headers"])
    bob_acc = await create_account(client, bob["headers"])
    await fund_account(session, alice_acc["id"], funding)
    beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_acc["id"], "label": "bob",
    }, headers=alice["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary}/verify", headers=alice["headers"])
    payment = (await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_acc["id"], "beneficiaryId": beneficiary,
        "amountMinor": amount, "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})).json()
    assert payment["status"] == "completed", payment
    return alice, bob, staff, alice_acc, bob_acc, payment


def _key(user) -> dict:
    return {**user["headers"], "Idempotency-Key": uuidlib.uuid4().hex}


@requires_database
async def test_partial_and_repeated_refunds_up_to_the_original_amount(client, session):
    alice, bob, staff, alice_acc, bob_acc, payment = await _paid(client, session)
    path = f"/api/v1/payments/{payment['id']}/refund"

    first = await client.post(path, json={"amountMinor": 20_000, "reason": "damaged"},
                              headers=_key(staff))
    assert first.status_code == 201, first.text
    assert (first.json()["refundedTotalMinor"], first.json()["remainingMinor"]) == (20_000, 30_000)
    second = await client.post(path, json={"amountMinor": 30_000}, headers=_key(staff))
    assert second.status_code == 201
    assert second.json()["remainingMinor"] == 0
    over = await client.post(path, json={"amountMinor": 1}, headers=_key(staff))
    assert over.status_code == 422 and over.json()["code"] == "refund_exceeds_payment"

    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0
    listing = (await client.get(f"/api/v1/payments/{payment['id']}/refunds",
                                headers=alice["headers"])).json()
    assert [item["amountMinor"] for item in listing["items"]] == [20_000, 30_000]
    events = (await session.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == uuidlib.UUID(payment["id"])))).all()
    assert events.count("payment.refunded.v1") == 2
    assert await _ledger_is_balanced(session)


@requires_database
async def test_refund_is_idempotent(client, session):
    alice, _, staff, alice_acc, _, payment = await _paid(client, session)
    headers = _key(staff)
    path = f"/api/v1/payments/{payment['id']}/refund"
    first = await client.post(path, json={"amountMinor": 10_000}, headers=headers)
    replay = await client.post(path, json={"amountMinor": 10_000}, headers=headers)
    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    reused = await client.post(path, json={"amountMinor": 5_000}, headers=headers)
    assert reused.status_code == 409 and reused.json()["code"] == "idempotency_key_reused"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 60_000


@requires_database
async def test_refund_rules(client, session):
    alice, bob, staff, _, bob_acc, payment = await _paid(client, session)
    path = f"/api/v1/payments/{payment['id']}/refund"
    denied = await client.post(path, json={"amountMinor": 1}, headers=_key(alice))
    assert denied.status_code == 403  # customers cannot pull money back themselves

    # Bob spends most of what he received, so he cannot fund the refund.
    carol = await register_and_login(client, f"carol-{uuidlib.uuid4().hex[:8]}@example.com")
    carol_acc = await create_account(client, carol["headers"])
    to_carol = await _verified_beneficiary(client, bob["headers"], carol_acc["id"])
    spent = await client.post("/api/v1/payments", json={
        "sourceAccountId": bob_acc["id"], "beneficiaryId": to_carol,
        "amountMinor": 45_000, "currency": "USD",
    }, headers=_key(bob))
    assert spent.status_code == 201, spent.text
    short = await client.post(path, json={"amountMinor": 10_000}, headers=_key(staff))
    assert short.status_code == 422 and short.json()["code"] == "recipient_insufficient_funds"


@requires_database
async def test_held_payment_cannot_be_refunded_or_reversed(client, session):
    _, _, analyst, _, _, payment, _ = await _held_payment(client, session)
    await _make_staff(session, analyst["customer_id"], "fraud_analyst", "finance_user")
    refund = await client.post(f"/api/v1/payments/{payment['id']}/refund",
                               json={"amountMinor": 1}, headers=_key(analyst))
    assert refund.status_code == 409 and refund.json()["code"] == "payment_not_refundable"
    reversal = await client.post(f"/api/v1/payments/{payment['id']}/reversal",
                                 json={"reason": "test"}, headers=_key(analyst))
    assert reversal.status_code == 409


@requires_database
async def test_reversal_mirrors_the_original_and_is_final(client, session):
    alice, bob, staff, alice_acc, bob_acc, payment = await _paid(client, session)
    path = f"/api/v1/payments/{payment['id']}/reversal"

    reversed_ = await client.post(path, json={"reason": "posted to the wrong beneficiary"},
                                  headers=_key(staff))
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["status"] == "reversed"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0

    original = await session.scalar(select(JournalEntry).where(
        JournalEntry.business_reference == f"payment:{payment['id']}"))
    assert original is not None  # never deleted
    mirror = await session.scalar(select(JournalEntry).where(
        JournalEntry.reversal_of_id == original.id))
    assert mirror is not None

    again = await client.post(path, json={"reason": "twice"}, headers=_key(staff))
    assert again.status_code == 409
    refund = await client.post(f"/api/v1/payments/{payment['id']}/refund",
                               json={"amountMinor": 1}, headers=_key(staff))
    assert refund.status_code == 409
    status = (await client.get(f"/api/v1/payments/{payment['id']}",
                               headers=alice["headers"])).json()["status"]
    assert status == "reversed"
    assert await _ledger_is_balanced(session)


@requires_database
async def test_refunded_payment_cannot_be_reversed(client, session):
    _, _, staff, _, _, payment = await _paid(client, session)
    await client.post(f"/api/v1/payments/{payment['id']}/refund", json={"amountMinor": 100},
                      headers=_key(staff))
    reversal = await client.post(f"/api/v1/payments/{payment['id']}/reversal",
                                 json={"reason": "late"}, headers=_key(staff))
    assert reversal.status_code == 409 and reversal.json()["code"] == "payment_has_refunds"


@requires_database
async def test_released_held_payment_reverses_both_postings(client, session):
    alice, bob, analyst, alice_acc, bob_acc, payment, case_id = await _held_payment(
        client, session)
    await _make_staff(session, analyst["customer_id"], "fraud_analyst", "finance_user")
    await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                      json={"resolution": "false_positive"}, headers=analyst["headers"])
    reversal = await client.post(f"/api/v1/payments/{payment['id']}/reversal",
                                 json={"reason": "fraud confirmed after release"},
                                 headers=_key(analyst))
    assert reversal.status_code == 200, reversal.text
    assert len(reversal.json()["reversalJournalIds"]) == 2
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0
    assert await _ledger_is_balanced(session)


@requires_database
async def test_deposit_reversal_through_the_journal_endpoint(client, session):
    customer = await register_and_login(client, f"dr-{uuidlib.uuid4().hex[:8]}@example.com")
    staff = await register_and_login(client, f"ops-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "finance_user")
    account = await create_account(client, customer["headers"])
    deposit = (await client.post(f"/api/v1/admin/accounts/{account['id']}/deposits",
                                 json={"amountMinor": 7_500}, headers=_key(staff))).json()

    listed = (await client.get(f"/api/v1/admin/customers/{customer['customer_id']}/deposits",
                               headers=staff["headers"])).json()["items"]
    assert [(d["amountMinor"], d["reversed"]) for d in listed] == [(7_500, False)]
    path = f"/api/v1/admin/journal-entries/{deposit['journalId']}/reversal"
    reversed_ = await client.post(path, json={"reason": "wrong account"}, headers=_key(staff))
    assert reversed_.status_code == 200, reversed_.text
    assert await get_balance(client, customer["headers"], account["id"]) == 0
    again = await client.post(path, json={"reason": "twice"}, headers=_key(staff))
    assert again.status_code == 409 and again.json()["code"] == "already_reversed"
    mirror = f"/api/v1/admin/journal-entries/{reversed_.json()['reversalJournalId']}/reversal"
    undo = await client.post(mirror, json={"reason": "undo"}, headers=_key(staff))
    assert undo.status_code == 409 and undo.json()["code"] == "reversal_not_reversible"
    listed = (await client.get(f"/api/v1/admin/customers/{customer['customer_id']}/deposits",
                               headers=staff["headers"])).json()["items"]
    assert listed[0]["reversed"] is True


@requires_database
async def test_payment_journals_must_use_the_payment_reversal(client, session):
    _, _, staff, _, _, payment = await _paid(client, session)
    journal_id = await session.scalar(select(JournalEntry.id).where(
        JournalEntry.business_reference == f"payment:{payment['id']}"))
    response = await client.post(f"/api/v1/admin/journal-entries/{journal_id}/reversal",
                                 json={"reason": "bypass"}, headers=_key(staff))
    assert response.status_code == 409 and response.json()["code"] == "use_payment_reversal"


@requires_database
async def test_concurrent_refunds_never_exceed_the_payment(client, session):
    _, _, staff, _, _, payment = await _paid(client, session)
    path = f"/api/v1/payments/{payment['id']}/refund"
    results = await asyncio.gather(*(
        client.post(path, json={"amountMinor": 20_000}, headers=_key(staff)) for _ in range(4)
    ))
    codes = sorted(result.status_code for result in results)
    assert codes == [201, 201, 422, 422], [r.text for r in results]
    assert await _ledger_is_balanced(session)
