"""Realistic demo data, generated through the real API.

Run inside the Python API container against a fresh database:

    docker compose exec python-api python -m app.seed

Everything a customer does (registration, KYC, accounts, beneficiaries, payments) and everything
staff do (deposits, fraud reviews, refunds, a reversal, reconciliation) goes through the HTTP API
in-process, so fraud scoring, holds, AML monitoring, the ledger and the outbox all behave exactly as
in production and the ledger stays balanced. Only the staff users are inserted directly, because
roles cannot be assigned through the API.

Two things are adjusted for a demo, and only in this process:
* the beneficiary cooling period is 0, so beneficiaries can be paid immediately;
* each historical payment's business timestamp is back-dated after it is created, spreading 90
  days of activity so that velocity and structuring rules see a realistic history instead of 100
  payments in one minute.

Ledger postings are never touched: the database rejects any UPDATE or DELETE on journal entries
(posted ledger records are immutable), and the seeder respects that. Journals therefore keep their
real posting time, so ledger views show the seed time while payments, reports and the dashboard
show the 90-day history.

Credentials are clearly fake and local-only. Both APIs share the database, so the data is visible
from the Python and the .NET backend.
"""

import os

# Must be set before any app module reads the settings.
os.environ["BENEFICIARY_COOLING_SECONDS"] = "0"
os.environ["RATE_LIMIT_ENABLED"] = "false"

import asyncio  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402
from base64 import b64encode  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402

from app.api.auth import hash_password  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.infrastructure.database import SessionFactory, engine  # noqa: E402
from app.infrastructure.models import (  # noqa: E402
    AmlAlert,
    Beneficiary,
    Customer,
    CustomerAccount,
    FraudCase,
    Payment,
    ProviderTransaction,
)

DEMO_PASSWORD = "Demo-Password-123"
STAFF = [
    ("fraud.analyst@example.com", ["fraud_analyst"]),
    ("finance.user@example.com", ["finance_user"]),
    ("operations.user@example.com", ["operations_user"]),
    ("compliance.officer@example.com", ["compliance_officer"]),
]
CUSTOMERS = [
    "Olivia Chen", "Liam Patel", "Sofia Garcia", "Noah Okafor", "Amara Nwosu",
    "Ethan Kowalski", "Mia Johansson", "Lucas Moreau", "Aisha Rahman", "Mateo Rossi",
]
REFERENCES = [
    "Rent share", "Groceries", "Dinner split", "Utilities", "Concert tickets", "Gym membership",
    "Birthday gift", "Book club", "Car pool", "Holiday deposit", "Phone bill", "Coffee club",
]
NOW = datetime.now(UTC)
rng = random.Random(20261011)  # deterministic, so every fresh seed looks the same


def email_for(name: str) -> str:
    return name.lower().replace(" ", ".") + "@example.com"


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


class Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def call(self, method: str, path: str, token: str | None = None, *,
                   expect: tuple[int, ...] = (200, 201, 204), **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await self.client.request(method, path, headers=headers, **kwargs)
        if response.status_code not in expect:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    async def login(self, email: str, password: str) -> str:
        data = await self.call("POST", "/api/v1/auth/login",
                               json={"email": email, "password": password})
        return data["accessToken"]


async def backdate(model, row_id: uuid.UUID, when: datetime, column: str = "created_at") -> None:
    async with SessionFactory() as session:
        await session.execute(update(model).where(model.id == row_id).values({column: when}))
        await session.commit()


async def backdate_payment(payment_id: str, when: datetime) -> None:
    """Move a payment and any case/alert it raised to a past time (never its ledger posting)."""
    pid = uuid.UUID(payment_id)
    async with SessionFactory() as session:
        await session.execute(update(Payment).where(Payment.id == pid).values(created_at=when))
        await session.execute(update(FraudCase).where(FraudCase.payment_id == pid)
                              .values(created_at=when))
        await session.execute(update(AmlAlert).where(AmlAlert.payment_id == pid)
                              .values(created_at=when))
        await session.commit()


async def ensure_staff() -> tuple[str, str]:
    """Create the staff users; return the admin's credentials."""
    settings = get_settings()
    admin_email = settings.admin_email.strip().lower()
    admin_password = settings.admin_password.get_secret_value()
    users = list(STAFF)
    if not (admin_email and admin_password):
        admin_email, admin_password = "admin@example.com", DEMO_PASSWORD
        users.append((admin_email, ["admin"]))
    async with SessionFactory() as session:
        for email, roles in users:
            if await session.scalar(select(Customer.id).where(Customer.email_normalized == email)):
                continue
            session.add(Customer(email_normalized=email, password_hash=hash_password(DEMO_PASSWORD),
                                 roles=["customer", *roles], status="active",
                                 created_at=NOW - timedelta(days=180)))
        await session.commit()
    return admin_email, admin_password


async def seed(api: Api) -> dict[str, int]:
    admin_email, admin_password = await ensure_staff()
    admin = await api.login(admin_email, admin_password)
    analyst = await api.login("fraud.analyst@example.com", DEMO_PASSWORD)
    finance = await api.login("finance.user@example.com", DEMO_PASSWORD)
    operations = await api.login("operations.user@example.com", DEMO_PASSWORD)

    # Customers: register, verify identity, open accounts.
    customers = []
    for index, name in enumerate(CUSTOMERS):
        email = email_for(name)
        registered = await api.call("POST", "/api/v1/auth/register",
                                    json={"email": email, "password": DEMO_PASSWORD})
        token = await api.login(email, DEMO_PASSWORD)
        kyc = await api.call("POST", "/api/v1/kyc", token,
                             json={"fullName": name, "country": "GB"})
        await api.call("POST", f"/api/v1/kyc/{kyc['id']}/documents", token, json={
            "documentType": "passport",
            "contentBase64": b64encode(f"demo passport for {name}".encode()).decode(),
        })
        await api.call("POST", f"/api/v1/kyc/{kyc['id']}/submit", token)
        checking = await api.call("POST", "/api/v1/accounts", token,
                                  json={"accountType": "checking", "currency": "USD"})
        savings = None
        if index < 5:
            savings = await api.call("POST", "/api/v1/accounts", token,
                                     json={"accountType": "savings", "currency": "USD"})
        customers.append({"name": name, "id": registered["id"], "token": token,
                          "checking": checking["id"], "savings": savings and savings["id"]})

    # Money in: treasury deposits (the only way money enters the platform).
    for customer in customers:
        await api.call("POST", f"/api/v1/admin/accounts/{customer['checking']}/deposits", admin,
                       headers=key(),
                       json={"amountMinor": 15_000_000, "reference": "Opening balance"})
        if customer["savings"]:
            await api.call("POST", f"/api/v1/admin/accounts/{customer['savings']}/deposits",
                           admin, headers=key(), json={"amountMinor": 13_000_000,
                                                       "reference": "Savings transfer in"})

    # History starts 120 days ago. Savings accounts stay new on purpose: young accounts paying new
    # beneficiaries are what the fraud rules look for (see the risky payments below).
    for customer in customers:
        await backdate(Customer, uuid.UUID(customer["id"]), NOW - timedelta(days=120))
        await backdate(CustomerAccount, uuid.UUID(customer["checking"]), NOW - timedelta(days=120))

    # 25 established beneficiaries: everyone pays two or three of the others.
    for index, customer in enumerate(customers):
        payees = []
        for offset in range(1, 4 if index < 5 else 3):
            other = customers[(index + offset) % len(customers)]
            created = await api.call("POST", "/api/v1/beneficiaries", customer["token"], json={
                "destinationAccountId": other["checking"], "label": other["name"]})
            await api.call("POST", f"/api/v1/beneficiaries/{created['id']}/verify",
                           customer["token"])
            await backdate(Beneficiary, uuid.UUID(created["id"]), NOW - timedelta(days=100))
            payees.append(created["id"])
        customer["payees"] = payees

    async def pay(customer, source, beneficiary, amount, reference, expect=(201,)):
        return await api.call("POST", "/api/v1/payments", customer["token"], headers=key(),
                              expect=expect, json={"sourceAccountId": source,
                                                   "beneficiaryId": beneficiary,
                                                   "amountMinor": amount, "currency": "USD",
                                                   "reference": reference})

    # 75 everyday payments over the last 90 days. Two carry references the simulated payment
    # provider reports differently, so reconciliation has real mismatches to show.
    moments = sorted(NOW - timedelta(days=rng.uniform(1, 90)) for _ in range(75))
    history = []
    for number, when in enumerate(moments):
        customer = customers[number % len(customers)]
        reference = rng.choice(REFERENCES)
        if number == 30:
            reference = "force-status-mismatch"
        elif number == 50:
            reference = "force-amount-mismatch"
        payment = await pay(customer, customer["checking"], rng.choice(customer["payees"]),
                            rng.randint(1_500, 120_000), reference)
        await backdate_payment(payment["id"], when)
        history.append(payment["id"])

    # 5 unusually large payments: allowed by fraud scoring, but each raises an AML alert.
    for number in range(5):
        customer = customers[number * 2]
        payment = await pay(customer, customer["checking"], customer["payees"][0],
                            rng.randint(6_000_000, 9_000_000), "Property deposit")
        await backdate_payment(payment["id"], NOW - timedelta(days=rng.uniform(2, 40)))

    # 20 risky payments: a brand-new savings account paying a brand-new beneficiary scores in the
    # review band, so each is held and opens a fraud case.
    held = []
    for customer in customers[:5]:
        other = customers[(customers.index(customer) + 5) % len(customers)]
        fresh = await api.call("POST", "/api/v1/beneficiaries", customer["token"], json={
            "destinationAccountId": other["checking"], "label": f"{other['name']} (new)"})
        await api.call("POST", f"/api/v1/beneficiaries/{fresh['id']}/verify", customer["token"])
        for _ in range(4):
            payment = await pay(customer, customer["savings"], fresh["id"],
                                rng.randint(2_100_000, 3_000_000), "Urgent transfer")
            if payment["status"] != "pending":
                raise RuntimeError(f"expected a held payment, got {payment['status']}")
            await backdate_payment(payment["id"], NOW - timedelta(hours=rng.uniform(2, 72)))
            held.append(payment["id"])

    # Analysts settle some cases: 5 released as false positives, 3 refunded as confirmed fraud.
    cases = (await api.call("GET", "/api/v1/fraud/cases", analyst))["items"]
    case_by_payment = {case["paymentId"]: case["id"] for case in cases}
    for payment_id in held[:5]:
        await api.call("POST", f"/api/v1/fraud/cases/{case_by_payment[payment_id]}/review",
                       analyst, json={"resolution": "false_positive",
                                      "note": "Customer confirmed by phone"})
    for payment_id in held[5:8]:
        await api.call("POST", f"/api/v1/fraud/cases/{case_by_payment[payment_id]}/review",
                       analyst, json={"resolution": "closed", "note": "Account takeover confirmed"})

    # Corrections: three partial refunds and one reversal of a payment sent in error.
    for payment_id in history[3:6]:
        await api.call("POST", f"/api/v1/payments/{payment_id}/refund", finance, headers=key(),
                       json={"amountMinor": 500, "reason": "Overcharged"})
    await api.call("POST", f"/api/v1/payments/{history[10]}/reversal", finance, headers=key(),
                   json={"reason": "Sent to the wrong beneficiary"})

    # The provider's ledger holds two transactions our platform never recorded — transfers that
    # left the provider without ever becoming internal payments. Reconciliation is what turns
    # this silent gap into a "missing internal" finding (spec section 20).
    async with SessionFactory() as session:
        session.add_all([
            ProviderTransaction(
                id=uuid.uuid4(), payment_id=uuid.uuid4(), reference="provider:orphan-1",
                status="completed", amount_minor=750_000, currency="USD",
                mode="success", processed_at=NOW - timedelta(days=3),
            ),
            ProviderTransaction(
                id=uuid.uuid4(), payment_id=uuid.uuid4(), reference="provider:orphan-2",
                status="completed", amount_minor=1_250_000, currency="USD",
                mode="success", processed_at=NOW - timedelta(days=1),
            ),
        ])
        await session.commit()

    # Operations runs the daily reconciliation against the simulated provider.
    run = await api.call("POST", "/api/v1/reconciliation/run", operations)

    seeded_ids = [uuid.UUID(customer["id"]) for customer in customers]

    async def count(model) -> int:
        # Only what this run created, even if the database already held other data.
        async with SessionFactory() as session:
            return int(await session.scalar(
                select(func.count()).select_from(model).where(model.customer_id.in_(seeded_ids))
            ))

    counts = {
        "customers": len(customers),
        "staff users": len(STAFF) + 1,
        "accounts": len(customers) + 5,
        "beneficiaries": await count(Beneficiary),
        "payments": await count(Payment),
        "fraud cases": await count(FraudCase),
        "AML alerts": await count(AmlAlert),
        "reconciliation records": run["matched"] + run["amountMismatch"]
                                  + run["statusMismatch"] + run["missingExternal"]
                                  + run["missingInternal"],
    }
    seeded_admin = admin_email == "admin@example.com"
    return counts | {"_admin": admin_email,
                     "_admin_password": DEMO_PASSWORD if seeded_admin else None}


async def main() -> int:
    async with SessionFactory() as session:
        if await session.scalar(select(Customer.id).where(
                Customer.email_normalized == email_for(CUSTOMERS[0]))):
            print("Demo data is already present (found olivia.chen@example.com); nothing to do.")
            print("Start from an empty database to seed again: docker compose down -v")
            return 0

    from app.main import app, seed_fraud_rules  # late, so the overrides above are in effect

    await seed_fraud_rules()  # normally done by API startup; idempotent

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://seed",
                                     timeout=60) as client:
            counts = await seed(Api(client))
    finally:
        await engine.dispose()

    admin = counts.pop("_admin")
    admin_password = counts.pop("_admin_password")
    print("\nFinCore demo data created:")
    for label, value in counts.items():
        print(f"  {label:<24}{value}")
    print("\nSign in (all demo passwords are fake and local-only):")
    # An admin from ADMIN_EMAIL/ADMIN_PASSWORD keeps its own password, which is never printed.
    print(f"  admin              {admin:<34}{admin_password or '(your ADMIN_PASSWORD)'}")
    for email, roles in STAFF:
        print(f"  {roles[0]:<19}{email:<34}{DEMO_PASSWORD}")
    print(f"  customer           {email_for(CUSTOMERS[0]):<34}{DEMO_PASSWORD}  (and 9 more)")
    print("Notifications appear within seconds, once the running APIs publish the outbox events.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
