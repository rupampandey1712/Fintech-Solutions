"""Concurrent same-account transfers: exactly one wins, the other is declined."""

import asyncio
import uuid as uuidlib

from sqlalchemy import select

from app.infrastructure.models import JournalEntry, JournalLine, LedgerAccount
from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, get_balance, register_and_login


@requires_database
async def test_concurrent_transfers_do_not_overspend(client, session):
    alice = await register_and_login(client, f"race-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"race2-{uuidlib.uuid4().hex[:8]}@example.com")
    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 100_000)

    beneficiary = await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_account["id"], "label": "race target",
    }, headers=alice["headers"])
    beneficiary_id = beneficiary.json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary_id}/verify", headers=alice["headers"])

    async def transfer(key: str):
        return await client.post("/api/v1/payments", json={
            "sourceAccountId": alice_account["id"],
            "beneficiaryId": beneficiary_id,
            "amountMinor": 80_000,
            "currency": "USD",
        }, headers={**alice["headers"], "Idempotency-Key": key})

    results = await asyncio.gather(transfer(uuidlib.uuid4().hex), transfer(uuidlib.uuid4().hex))
    status_codes = sorted(result.status_code for result in results)
    assert status_codes == [201, 422], [result.text for result in results]
    assert await get_balance(client, alice["headers"], alice_account["id"]) == 20_000
    assert await get_balance(client, bob["headers"], bob_account["id"]) == 80_000

    ledger = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.account_id == alice_account["id"]),
    )
    balance = 0
    lines = (await session.execute(
        select(JournalLine).join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalLine.ledger_account_id == ledger.id)
    )).scalars().all()
    for line in lines:
        balance += line.amount_minor if line.side == "credit" else -line.amount_minor
    assert balance == 20_000
