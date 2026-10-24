"""The provider hop: payments are submitted to the simulator, and its record is the external
system of record that reconciliation compares against (spec sections 20-21)."""

import uuid as uuidlib

from sqlalchemy import select

from app.infrastructure.models import (
    JournalEntry,
    JournalLine,
    OutboxEvent,
    Payment,
    ProviderTransaction,
    ReconciliationRecord,
)
from app.worker import default_handlers, dispatch_batch, inline_delivery
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, get_balance, register_and_login
from tests.test_holds_and_treasury import _make_staff, _review_rule


async def _paid(client, session, amount=25_000, funding=100_000, reference=None):
    """Alice pays Bob `amount` through the provider; returns the payment response and rows."""
    alice = await register_and_login(client, f"pv-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"pv2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_acc = await create_account(client, alice["headers"])
    bob_acc = await create_account(client, bob["headers"])
    await fund_account(session, alice_acc["id"], funding)
    beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_acc["id"], "label": "bob",
    }, headers=alice["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary}/verify", headers=alice["headers"])
    response = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_acc["id"], "beneficiaryId": beneficiary,
        "amountMinor": amount, "currency": "USD", "reference": reference,
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert response.status_code == 201, response.text
    payment_id = uuidlib.UUID(response.json()["id"])
    row = await session.get(Payment, payment_id)
    provider_row = await session.scalar(
        select(ProviderTransaction).where(ProviderTransaction.payment_id == payment_id)
    )
    return alice, bob, alice_acc, bob_acc, response.json(), row, provider_row


async def _recon(client, session, staff):
    headers = {**staff["headers"]}
    run = (await client.post("/api/v1/reconciliation/run", headers=headers)).json()
    return run, (await session.scalars(select(ReconciliationRecord).where(
        ReconciliationRecord.run_id == uuidlib.UUID(run["runId"]),
    ))).all()


async def _staff(client, session, *roles):
    staff = await register_and_login(client, f"ops-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, staff["customer_id"], *roles)
    return staff


@requires_database
async def test_successful_payment_carries_the_provider_record(client, session):
    alice, bob, alice_acc, bob_acc, payment, row, provider_row = await _paid(client, session)
    assert payment["status"] == "completed"
    assert payment["providerReference"] == f"provider:{payment['id']}"
    assert payment["providerStatus"] == "completed"
    assert provider_row is not None and provider_row.status == "completed"
    assert provider_row.amount_minor == 25_000
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 75_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 25_000

    events = set((await session.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == row.id,
    ))).all())
    # The full lifecycle of one transfer, as the event catalogue (spec section 17) names it.
    assert {
        "payment.initiated.v1", "payment.processed.v1", "payment.completed.v1",
        "ledger.posted.v1", "transaction.created.v1", "reconciliation.required.v1",
    } <= events


@requires_database
async def test_provider_failure_leaves_the_ledger_untouched(client, session):
    alice, bob, alice_acc, bob_acc, payment, row, provider_row = await _paid(
        client, session, reference="invoice force-provider-failure #42",
    )
    assert payment["status"] == "failed"
    assert payment["providerStatus"] == "failure"
    assert provider_row.status == "failed"
    # No money moved: the debit and credit never posted.
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0
    events = set((await session.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == row.id,
    ))).all())
    assert "payment.failed.v1" in events
    assert "payment.completed.v1" not in events
    # Nothing posted for this payment: no journal carries its reference.
    assert await session.scalar(
        select(JournalLine.journal_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalEntry.business_reference == f"payment:{payment['id']}")
    ) is None


@requires_database
async def test_provider_timeout_holds_funds_until_reconciliation_settles_it(client, session):
    staff = await _staff(client, session, "finance_user")
    alice, bob, alice_acc, bob_acc, payment, row, provider_row = await _paid(
        client, session, reference="urgent force-provider-timeout",
    )
    # The provider completed the transfer but the response was lost: our side cannot say
    # completed, so the funds wait in the holds ledger where they cannot be spent twice.
    assert payment["status"] == "processing"
    assert provider_row.status == "completed"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 75_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0

    run, records = await _recon(client, session, staff)
    assert run["statusMismatch"] >= 1
    mismatched = [record for record in records if record.status == "status_mismatch"]
    target = next(record for record in mismatched
                  if str(record.payment_id) == payment["id"])
    assert target.internal_status == "processing" and target.external_status == "completed"

    # The run settled the payment from the provider's record: released to the beneficiary.
    await session.refresh(row)
    assert row.status == "completed"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 75_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 25_000
    events = set((await session.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == row.id,
    ))).all())
    assert "payment.completed.v1" in events

    # A second run finds the two sides in agreement and moves nothing.
    _, records = await _recon(client, session, staff)
    again = next(record for record in records if str(record.payment_id) == payment["id"])
    assert again.status == "matched"
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 25_000


@requires_database
async def test_timed_out_payment_the_provider_failed_is_refunded(client, session):
    staff = await _staff(client, session, "operations_user")
    alice, bob, alice_acc, bob_acc, payment, row, provider_row = await _paid(
        client, session, reference="force-provider-timeout",
    )
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 75_000
    # The provider's later answer: the transfer did not go through.
    provider_row.status = "failed"
    await session.commit()

    await _recon(client, session, staff)
    await session.refresh(row)
    assert row.status == "failed"
    assert await get_balance(client, alice["headers"], alice_acc["id"]) == 100_000
    assert await get_balance(client, bob["headers"], bob_acc["id"]) == 0


@requires_database
async def test_reconciliation_finds_provider_transactions_without_internal_payments(
    client, session,
):
    staff = await _staff(client, session, "operations_user")
    # A provider record whose payment id matches nothing internal: money that left the
    # provider without the platform ever seeing it.
    orphan_id = uuidlib.uuid4()
    session.add(ProviderTransaction(
        payment_id=orphan_id, reference=f"provider:{orphan_id}", status="completed",
        amount_minor=4_000, currency="USD", mode="success",
    ))
    await session.commit()

    run, records = await _recon(client, session, staff)
    assert run["missingInternal"] == 1
    orphan = next(record for record in records if str(record.payment_id) == str(orphan_id))
    assert orphan.status == "missing_internal"
    assert orphan.internal_status == "missing"
    assert orphan.external_amount_minor == 4_000


@requires_database
async def test_released_hold_payment_is_missing_external(client, session):
    staff = await _staff(client, session, "finance_user")
    analyst = await register_and_login(client, f"an-{uuidlib.uuid4().hex[:8]}@example.com")
    await _make_staff(session, analyst["customer_id"], "fraud_analyst")
    alice = await register_and_login(client, f"hx-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"hx2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_acc = await create_account(client, alice["headers"])
    bob_acc = await create_account(client, bob["headers"])
    await fund_account(session, alice_acc["id"], 100_000)
    beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_acc["id"], "label": "bob",
    }, headers=alice["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary}/verify", headers=alice["headers"])
    await _review_rule(session)
    payment = (await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_acc["id"], "beneficiaryId": beneficiary,
        "amountMinor": 30_000, "currency": "USD",
    }, headers={**alice["headers"], "Idempotency-Key": uuidlib.uuid4().hex})).json()
    assert payment["status"] == "pending"  # held for review; the provider was never involved

    # The reviewer releases it: funds move holds -> beneficiary, entirely internally.
    from sqlalchemy import select as sa_select

    from app.infrastructure.models import FraudCase
    case_id = await session.scalar(sa_select(FraudCase.id).where(
        FraudCase.payment_id == uuidlib.UUID(payment["id"]),
    ))
    reviewed = await client.post(f"/api/v1/fraud/cases/{case_id}/review",
                                 json={"resolution": "false_positive"},
                                 headers=analyst["headers"])
    assert reviewed.status_code == 200, reviewed.text

    run, records = await _recon(client, session, staff)
    target = next(record for record in records if str(record.payment_id) == payment["id"])
    # Internal says completed, the provider has no record: the classic missing-external finding.
    assert target.status == "missing_external"
    assert run["missingExternal"] >= 1


@requires_database
async def test_legacy_mismatch_markers_reach_the_provider_record(client, session):
    staff = await _staff(client, session, "finance_user")
    _, _, _, _, payment, _, provider_row = await _paid(
        client, session, reference="force-amount-mismatch",
    )
    assert payment["status"] == "completed"
    assert provider_row.amount_minor == payment["amountMinor"] * 2
    run, records = await _recon(client, session, staff)
    target = next(record for record in records if str(record.payment_id) == payment["id"])
    assert target.status == "amount_mismatch"


@requires_database
async def test_payment_completion_notifies_every_channel(client, session):
    from sqlalchemy import select as sa_select

    from app.infrastructure.models import Notification

    alice, bob, alice_acc, bob_acc, payment, row, provider_row = await _paid(client, session)
    # Drain the outbox inline: the payment.completed handler fans out to all four channels.
    await dispatch_batch(session, inline_delivery(default_handlers()), batch_size=100,
                         max_attempts=3)
    rows = (await session.scalars(
        sa_select(Notification).where(Notification.customer_id == row.customer_id)
    )).all()
    by_channel = {notice.channel: notice.status for notice in rows}
    assert by_channel == {"in_app": "sent", "email": "sent", "sms": "sent", "push": "sent"}


@requires_database
async def test_lifecycle_events_for_registration_and_accounts(client, session):
    user = await register_and_login(client, f"life-{uuidlib.uuid4().hex[:8]}@example.com")
    customer_id = uuidlib.UUID(user["customer_id"])
    created = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == customer_id, OutboxEvent.event_type == "customer.created.v1",
    ))
    assert created is not None and created.payload["email"].startswith("life-")

    account = await create_account(client, user["headers"])
    event = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == uuidlib.UUID(account["id"]),
        OutboxEvent.event_type == "account.created.v1",
    ))
    assert event is not None and event.payload["currency"] == "USD"
