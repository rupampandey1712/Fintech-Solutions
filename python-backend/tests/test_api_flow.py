"""End-to-end registration through payment flow against a real database."""

import uuid as uuidlib

from sqlalchemy import select

from app.infrastructure.models import JournalEntry, JournalLine, LedgerAccount
from tests.conftest import requires_database


async def register_and_login(client, email: str) -> dict:
    response = await client.post("/api/v1/auth/register", json={
        "email": email, "password": "SuperSecurePass123",
    })
    assert response.status_code == 201, response.text
    login = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "SuperSecurePass123",
    })
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["refreshToken"]
    return {
        "customer_id": response.json()["id"],
        "headers": {"Authorization": f"Bearer {tokens['accessToken']}"},
    }

async def create_account(client, headers, currency: str = "USD") -> dict:
    response = await client.post("/api/v1/accounts", json={
        "accountType": "wallet", "currency": currency,
    }, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def fund_account(session, account_id, amount_minor: int, currency: str = "USD") -> None:
    ledger = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.account_id == account_id),
    )
    assert ledger is not None
    treasury = LedgerAccount(code=f"treasury:{uuidlib.uuid4().hex}", currency=currency)
    session.add(treasury)
    await session.flush()
    journal = JournalEntry(business_reference=f"funding:{uuidlib.uuid4().hex}", currency=currency)
    session.add(journal)
    await session.flush()
    session.add_all([
        JournalLine(journal_id=journal.id, ledger_account_id=treasury.id, side="debit",
                    amount_minor=amount_minor),
        JournalLine(journal_id=journal.id, ledger_account_id=ledger.id, side="credit",
                    amount_minor=amount_minor),
    ])
    await session.commit()


async def get_balance(client, headers, account_id) -> int:
    response = await client.get(f"/api/v1/accounts/{account_id}", headers=headers)
    assert response.status_code == 200
    return response.json()["ledgerBalanceMinor"]


@requires_database
async def test_full_payment_flow(client, session):
    alice = await register_and_login(client, f"alice-{uuidlib.uuid4().hex[:8]}@example.com")
    bob = await register_and_login(client, f"bob-{uuidlib.uuid4().hex[:8]}@example.com")

    alice_account = await create_account(client, alice["headers"])
    bob_account = await create_account(client, bob["headers"])
    await fund_account(session, alice_account["id"], 1_000_000)

    assert await get_balance(client, alice["headers"], alice_account["id"]) == 1_000_000

    beneficiary_response = await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": bob_account["id"], "label": "Bob wallet",
    }, headers=alice["headers"])
    assert beneficiary_response.status_code == 201, beneficiary_response.text
    beneficiary_id = beneficiary_response.json()["id"]
    verified = await client.post(
        f"/api/v1/beneficiaries/{beneficiary_id}/verify", headers=alice["headers"],
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "active"

    payment_key = uuidlib.uuid4().hex
    payment_response = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"],
        "beneficiaryId": beneficiary_id,
        "amountMinor": 250_000,
        "currency": "USD",
        "reference": "rent share",
    }, headers={**alice["headers"], "Idempotency-Key": payment_key})
    assert payment_response.status_code == 201, payment_response.text
    payment = payment_response.json()
    assert payment["status"] == "completed"
    assert payment["reference"] == "rent share"
    assert payment["risk"]["decision"] in {"allow", "review", "block"}

    assert await get_balance(client, alice["headers"], alice_account["id"]) == 750_000
    assert await get_balance(client, bob["headers"], bob_account["id"]) == 250_000

    replay = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"],
        "beneficiaryId": beneficiary_id,
        "amountMinor": 250_000,
        "currency": "USD",
        "reference": "rent share",
    }, headers={**alice["headers"], "Idempotency-Key": payment_key})
    # A replay returns the original response, status included (the contract only defines 201).
    assert replay.status_code == 201
    assert replay.json()["id"] == payment["id"]
    assert await get_balance(client, alice["headers"], alice_account["id"]) == 750_000

    conflict = await client.post("/api/v1/payments", json={
        "sourceAccountId": alice_account["id"],
        "beneficiaryId": beneficiary_id,
        "amountMinor": 999,
        "currency": "USD",
        "reference": "different body",
    }, headers={**alice["headers"], "Idempotency-Key": payment_key})
    assert conflict.status_code == 409

    payments_page = await client.get("/api/v1/payments", headers=alice["headers"])
    assert payments_page.status_code == 200
    page = payments_page.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] == payment["id"]


@requires_database
async def test_refresh_and_logout_flow(client):
    email = f"refresh2-{uuidlib.uuid4().hex[:8]}@example.com"
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": "SuperSecurePass123",
    })
    tokens = (await client.post("/api/v1/auth/login", json={
        "email": email, "password": "SuperSecurePass123",
    })).json()
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]},
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refreshToken"] != tokens["refreshToken"]

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]},
    )
    assert replay.status_code == 401

    logout = await client.post(
        "/api/v1/auth/logout", json={"refreshToken": new_tokens["refreshToken"]},
    )
    assert logout.status_code == 204
    revoked = await client.post(
        "/api/v1/auth/refresh", json={"refreshToken": new_tokens["refreshToken"]},
    )
    assert revoked.status_code == 401
