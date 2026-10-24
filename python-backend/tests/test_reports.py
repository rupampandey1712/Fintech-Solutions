"""Report series keep each currency separate: amounts in different currencies are never summed."""

import uuid as uuidlib
from datetime import UTC, datetime

from tests.conftest import requires_database
from tests.test_api_flow import create_account, fund_account, register_and_login


async def _pay(client, session, payer, currency: str, amount: int) -> None:
    payee = await register_and_login(client, f"rp2-{uuidlib.uuid4().hex[:8]}@example.com")
    source = await create_account(client, payer["headers"], currency)
    destination = await create_account(client, payee["headers"], currency)
    await fund_account(session, source["id"], amount * 2, currency)
    beneficiary = (await client.post("/api/v1/beneficiaries", json={
        "destinationAccountId": destination["id"], "label": "payee",
    }, headers=payer["headers"])).json()["id"]
    await client.post(f"/api/v1/beneficiaries/{beneficiary}/verify", headers=payer["headers"])
    response = await client.post("/api/v1/payments", json={
        "sourceAccountId": source["id"], "beneficiaryId": beneficiary,
        "amountMinor": amount, "currency": currency,
    }, headers={**payer["headers"], "Idempotency-Key": uuidlib.uuid4().hex})
    assert response.status_code == 201, response.text


@requires_database
async def test_dashboard_and_daily_series_are_split_by_currency(client, session):
    payer = await register_and_login(client, f"rp-{uuidlib.uuid4().hex[:8]}@example.com")
    await _pay(client, session, payer, "USD", 1_000)
    await _pay(client, session, payer, "EUR", 250)
    month = datetime.now(UTC).strftime("%Y-%m")
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    dashboard = (await client.get("/api/v1/reports/dashboard", headers=payer["headers"])).json()
    monthly = dashboard["monthlyVolume"]
    # Six zero-filled months for each currency, ordered by month then currency.
    assert len(monthly) == 12
    assert [point["currency"] for point in monthly[:2]] == ["EUR", "USD"]
    current = {point["currency"]: point for point in monthly if point["month"] == month}
    assert current["USD"]["volumeMinor"] == 1_000 and current["USD"]["count"] == 1
    assert current["EUR"]["volumeMinor"] == 250 and current["EUR"]["count"] == 1

    series = (await client.get("/api/v1/reports/transactions?days=7",
                               headers=payer["headers"])).json()["series"]
    assert len(series) == 14
    todays = {point["currency"]: point["volumeMinor"] for point in series if point["day"] == today}
    assert todays == {"EUR": 250, "USD": 1_000}


@requires_database
async def test_report_series_are_empty_without_payments(client):
    newcomer = await register_and_login(client, f"rp0-{uuidlib.uuid4().hex[:8]}@example.com")
    dashboard = (await client.get("/api/v1/reports/dashboard", headers=newcomer["headers"])).json()
    assert dashboard["monthlyVolume"] == []
