"""Chargeback disputes, settlement batches, dead-letter replay, user management, AML review,
and the CSV report exports."""

import uuid as uuidlib

from sqlalchemy import func, select

from app.infrastructure.models import (
    JournalEntry,
    Notification,
    OutboxDeadLetter,
    OutboxEvent,
)
from app.worker import default_handlers, dispatch_batch, inline_delivery
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, get_balance, register_and_login
from tests.test_holds_and_treasury import _make_staff
from tests.test_refunds_reversals import _ledger_is_balanced


def _key(user) -> dict:
    return {**user["headers"], "Idempotency-Key": uuidlib.uuid4().hex}


async def _completed_payment(client, session, amount=25_000, funding=100_000):
    """Alice pays Bob; a finance user and a fraud analyst exist for staff operations."""
    alice = await register_and_login(client, f"cb-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"cb2-{uuidlib.uuid4().hex[:8]}@example.com")
    staff = await register_and_login(client, f"fin-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "finance_user")
    analyst = await register_and_login(client, f"ana-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, analyst["customer_id"], "fraud_analyst")
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
    }, headers=_key(alice))).json()
    assert payment["status"] == "completed", payment
    return alice, bob, staff, analyst, alice_acc, bob_acc, payment


async def _dispute(client, session, amount=25_000):
    alice, bob, staff, analyst, alice_acc, bob_acc, payment = await _completed_payment(
        client, session, amount=amount)
    opened = await client.post(f"/api/v1/payments/{payment['id']}/dispute", json={
        "reason": "I did not authorise this transfer",
    }, headers=alice["headers"])
    assert opened.status_code == 201, opened.text
    dispute = opened.json()
    return alice, bob, staff, analyst, alice_acc, bob_acc, payment, dispute


# --------------------------------------------------------------------------------------------
# Chargebacks (spec section 61)

@requires_database
async def test_dispute_lifecycle_evidence_and_decision(client, session):
    alice, bob, staff, analyst, alice_acc, bob_acc, payment, dispute = await _dispute(
        client, session)

    # One dispute per payment.
    second = await client.post(f"/api/v1/payments/{payment['id']}/dispute", json={
        "reason": "again",
    }, headers=alice["headers"])
    assert second.status_code == 409 and second.json()["code"] == "payment_already_disputed"

    # Investigation: finance collects evidence, which moves the dispute into review.
    evidence = await client.post(f"/api/v1/admin/disputes/{dispute['id']}/evidence", json={
        "note": "Cardholder statement obtained",
    }, headers=staff["headers"])
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["status"] == "under_review"
    assert evidence.json()["evidence"][0]["note"] == "Cardholder statement obtained"

    # Decision lost: the chargeback is upheld and the funds return to the payer.
    decided = await client.post(f"/api/v1/admin/disputes/{dispute['id']}/decision", json={
        "outcome": "lost",
    }, headers=analyst["headers"])
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "lost"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0

    original = await session.scalar(select(JournalEntry).where(
        JournalEntry.business_reference == f"payment:{payment['id']}"))
    mirror = await session.scalar(select(JournalEntry).where(
        JournalEntry.reversal_of_id == original.id))
    assert mirror is not None  # mirrored, never deleted
    status = (await client.get(f"/api/v1/payments/{payment['id']}",
                               headers=alice["headers"])).json()["status"]
    assert status == "reversed"
    events = set((await session.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == uuidlib.UUID(dispute["id"]),
    ))).all())
    assert {"payment.dispute.opened.v1", "payment.dispute.decided.v1"} <= events
    assert await _ledger_is_balanced(session)


@requires_database
async def test_dispute_won_decision_leaves_money_alone(client, session):
    alice, bob, staff, analyst, alice_acc, bob_acc, payment, dispute = await _dispute(
        client, session)
    decided = await client.post(f"/api/v1/admin/disputes/{dispute['id']}/decision", json={
        "outcome": "won", "resolution": "Merchant provided delivery proof",
    }, headers=analyst["headers"])
    assert decided.status_code == 200, decided.text
    assert decided.json()["resolution"] == "Merchant provided delivery proof"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 75_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 25_000
    assert await _ledger_is_balanced(session)


@requires_database
async def test_losing_a_dispute_on_a_reversed_payment_is_rejected(client, session):
    alice, bob, staff, analyst, alice_acc, bob_acc, payment, dispute = await _dispute(
        client, session)
    reversed_ = await client.post(f"/api/v1/payments/{payment['id']}/reversal", json={
        "reason": "operational correction first",
    }, headers=_key(staff))
    assert reversed_.status_code == 200, reversed_.text
    decided = await client.post(f"/api/v1/admin/disputes/{dispute['id']}/decision", json={
        "outcome": "lost",
    }, headers=analyst["headers"])
    assert decided.status_code == 409 and decided.json()["code"] == "payment_not_reversible"


@requires_database
async def test_dispute_decisions_need_an_analyst_or_admin(client, session):
    alice, bob, staff, analyst, _, _, payment, dispute = await _dispute(client, session)
    decided = await client.post(f"/api/v1/admin/disputes/{dispute['id']}/decision", json={
        "outcome": "won",
    }, headers=staff["headers"])
    assert decided.status_code == 403  # finance users may collect evidence, not decide
    listed = await client.get("/api/v1/admin/disputes", headers=staff["headers"])
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1


# --------------------------------------------------------------------------------------------
# Settlement batches (spec section 58)

@requires_database
async def test_settlement_batch_lifecycle_and_file(client, session):
    alice, bob, staff, analyst, alice_acc, bob_acc, payment = await _completed_payment(
        client, session)
    second_beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_acc["id"], "label": "bob2",
    }, headers=alice["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{second_beneficiary}/verify",
                      headers=alice["headers"])
    (await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_acc["id"], "beneficiaryId": second_beneficiary,
        "amountMinor": 5_000, "currency": "USD",
    }, headers=_key(alice))).raise_for_status()
    from datetime import UTC, datetime

    business_date = datetime.now(UTC).strftime("%Y-%m-%d")
    created = await client.post("/api/v1/admin/settlement-batches", json={
        "businessDate": business_date, "currency": "USD",
    }, headers=staff["headers"])
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["status"] == "pending"
    assert batch["paymentCount"] == 2
    assert batch["totalMinor"] == payment["amountMinor"] + 5_000
    assert batch["netMinor"] == batch["totalMinor"] - batch["feeMinor"]

    processed = await client.post(f"/api/v1/admin/settlement-batches/{batch['id']}/process",
                                  headers=staff["headers"])
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "completed"

    again = await client.post(f"/api/v1/admin/settlement-batches/{batch['id']}/process",
                              headers=staff["headers"])
    assert again.status_code == 409 and again.json()["code"] == "settlement_batch_not_pending"

    # A settled payment can never be settled twice (unique batch item).
    duplicate = await client.post("/api/v1/admin/settlement-batches", json={
        "businessDate": business_date, "currency": "USD",
    }, headers=staff["headers"])
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "no_settleable_payments_for_date"

    file_response = await client.get(
        f"/api/v1/admin/settlement-batches/{batch['id']}/file", headers=staff["headers"])
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("text/csv")
    lines = file_response.text.strip().splitlines()
    assert lines[0].startswith("batch_id,business_date,currency,payment_id")
    assert len(lines) == 3  # header + two payments


@requires_database
async def test_settlement_requires_finance_roles(client, session):
    alice, bob, staff, analyst, _, _, payment = await _completed_payment(client, session)
    denied = await client.post("/api/v1/admin/settlement-batches", json={
        "businessDate": "2026-01-01", "currency": "USD",
    }, headers=alice["headers"])
    assert denied.status_code == 403


# --------------------------------------------------------------------------------------------
# Outbox dead letters (spec section 19: manual replay)

@requires_database
async def test_dead_letter_replay(client, session):
    staff = await register_and_login(client, f"dl-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "operations_user")
    dead = OutboxDeadLetter(
        outbox_event_id=uuidlib.uuid4(), event_type="kyc.approved.v1",
        payload={"customerId": staff["customer_id"]}, failure_reason="simulated outage",
    )
    session.add(dead)
    await session.commit()

    listed = (await client.get("/api/v1/admin/outbox/dead-letters",
                               headers=staff["headers"])).json()["items"]
    assert [item["replayed"] for item in listed] == [False]

    replayed = await client.post(f"/api/v1/admin/outbox/dead-letters/{dead.id}/replay",
                                 headers=staff["headers"])
    assert replayed.status_code == 200, replayed.text
    new_event_id = uuidlib.UUID(replayed.json()["newOutboxEventId"])
    event = await session.get(OutboxEvent, new_event_id)
    assert event is not None and event.payload == {"customerId": staff["customer_id"]}
    assert event.published_at is None  # the dispatcher picks it up as a fresh event

    twice = await client.post(f"/api/v1/admin/outbox/dead-letters/{dead.id}/replay",
                              headers=staff["headers"])
    assert twice.status_code == 409 and twice.json()["code"] == "dead_letter_already_replayed"


# --------------------------------------------------------------------------------------------
# User management (spec section 25: admin manages users)

@requires_database
async def test_admin_manages_user_roles(client, session):
    admin = await register_and_login(client, f"adm-{uuidlib.uuid4().hex[:8]}@example.com")
    analyst = await register_and_login(client, f"ana2-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, admin["customer_id"], "admin")

    users = (await client.get("/api/v1/admin/users", headers=admin["headers"])).json()["items"]
    emails = {user["email"] for user in users}
    assert any(email.startswith("ana2-") for email in emails)

    promoted = await client.patch(f"/api/v1/admin/users/{analyst['customer_id']}/roles", json={
        "roles": ["customer", "fraud_analyst"],
    }, headers=admin["headers"])
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["roles"] == ["customer", "fraud_analyst"]

    # An unknown role is a validation error; removing your own admin role is refused.
    invalid = await client.patch(f"/api/v1/admin/users/{analyst['customer_id']}/roles", json={
        "roles": ["customer", "space_marine"],
    }, headers=admin["headers"])
    assert invalid.status_code == 422
    lockout = await client.patch(f"/api/v1/admin/users/{admin['customer_id']}/roles", json={
        "roles": ["customer"],
    }, headers=admin["headers"])
    assert lockout.status_code == 422 and lockout.json()["code"] == "cannot_remove_own_admin_role"

    # Non-admins cannot manage users.
    forbidden = await client.get("/api/v1/admin/users", headers=analyst["headers"])
    assert forbidden.status_code == 403


# --------------------------------------------------------------------------------------------
# AML alert review (spec section 41)

@requires_database
async def test_aml_alert_status_workflow(client, session):
    reviewer = await register_and_login(client, f"cmp-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, reviewer["customer_id"], "compliance_officer")
    alice, bob, staff, analyst, alice_acc, bob_acc, payment = await _completed_payment(
        client, session, amount=6_000_000, funding=8_000_000)  # unusual_amount threshold: 5M

    alerts = (await client.get("/api/v1/aml/alerts", headers=reviewer["headers"])).json()["items"]
    assert any(alert["ruleCode"] == "unusual_amount" for alert in alerts)

    alert_id = next(alert["id"] for alert in alerts if alert["ruleCode"] == "unusual_amount")
    investigating = await client.patch(f"/api/v1/aml/alerts/{alert_id}/status", json={
        "status": "investigating", "note": "Source of funds requested",
    }, headers=reviewer["headers"])
    assert investigating.status_code == 200, investigating.text
    assert investigating.json()["status"] == "investigating"

    closed = await client.patch(f"/api/v1/aml/alerts/{alert_id}/status", json={
        "status": "false_positive",
    }, headers=reviewer["headers"])
    assert closed.status_code == 200

    # Customers only see their own alerts and cannot review them.
    forbidden = await client.patch(f"/api/v1/aml/alerts/{alert_id}/status", json={
        "status": "closed",
    }, headers=alice["headers"])
    assert forbidden.status_code == 403


# --------------------------------------------------------------------------------------------
# Reports: dashboards and CSV export (spec sections 42 and 62)

@requires_database
async def test_fraud_and_finance_dashboards(client, session):
    staff = await register_and_login(client, f"fin2-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "finance_user")
    await _completed_payment(client, session)
    fraud = (await client.get("/api/v1/reports/fraud", headers=staff["headers"])).json()
    assert "casesByStatus" in fraud and "riskDistribution" in fraud and "topRules" in fraud
    finance = (await client.get("/api/v1/reports/finance", headers=staff["headers"])).json()
    assert finance["settlement"]["batches"] == 0
    assert finance["paymentCount"] >= 1
    assert "matched" in finance["reconciliation"]


@requires_database
async def test_csv_report_exports(client, session):
    staff = await register_and_login(client, f"exp-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], "finance_user")
    await _completed_payment(client, session)

    for report_type in ("daily-transactions", "payment-success", "fraud", "aml",
                        "reconciliation", "ledger", "settlement"):
        response = await client.get(f"/api/v1/reports/{report_type}/export",
                                    headers=staff["headers"])
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["x-report-path"]  # generated file was stored
        lines = response.text.strip().splitlines()
        # Fraud/AML/reconciliation/settlement may legitimately have no rows yet; the reports
        # fed by the payments just created always have data.
        assert len(lines) >= (2 if report_type in {"daily-transactions", "payment-success"}
                              else 1)

    unknown = await client.get("/api/v1/reports/nope/export", headers=staff["headers"])
    assert unknown.status_code == 404


@requires_database
async def test_payment_completion_notifies_dispute_channels(client, session):
    """The dispute events reach the notification service through the outbox handlers."""
    alice, bob, staff, analyst, alice_acc, bob_acc, payment, dispute = await _dispute(
        client, session)
    await dispatch_batch(session, inline_delivery(default_handlers()), batch_size=100,
                         max_attempts=3)
    rows = (await session.scalars(
        select(func.count()).select_from(Notification).where(
            Notification.customer_id == uuidlib.UUID(alice["customer_id"]),
            Notification.event_type == "payment.dispute.opened.v1",
        )
    )).one()
    assert int(rows) == 4  # one per channel: in_app, email, sms, push
