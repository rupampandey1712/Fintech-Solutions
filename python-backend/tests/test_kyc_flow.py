"""KYC application flow: create, document upload, submit, approval activates customer."""

import uuid as uuidlib

from tests.conftest import requires_database


@requires_database
async def test_kyc_flow_approves_and_activates(client, session, tmp_path):
    from pathlib import Path

    from app.config.settings import get_settings
    from app.infrastructure.models import Customer

    email = f"kyc-{uuidlib.uuid4().hex[:8]}@example.com"
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": "SuperSecurePass123",
    })
    tokens = (await client.post("/api/v1/auth/login", json={
        "email": email, "password": "SuperSecurePass123",
    })).json()
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}

    created = await client.post("/api/v1/kyc", json={
        "fullName": "Alice Wonderland", "country": "US",
    }, headers=headers)
    assert created.status_code == 201, created.text
    application = created.json()
    assert application["status"] == "pending"

    empty = await client.post(f"/api/v1/kyc/{application['id']}/submit", headers=headers)
    assert empty.status_code == 422
    assert empty.json()["code"] == "kyc_documents_required"

    document = await client.post(f"/api/v1/kyc/{application['id']}/documents", json={
        "documentType": "passport", "contentBase64": "aGVsbG8=",
    }, headers=headers)
    assert document.status_code == 201, document.text
    blob_path = document.json()["blobPath"]
    assert blob_path.startswith("kyc-documents/")
    stored = Path(get_settings().kyc_storage_path) / Path(blob_path).name
    assert stored.exists()
    assert stored.read_bytes() == b"hello"

    submitted = await client.post(f"/api/v1/kyc/{application['id']}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "approved"

    me = await client.get("/api/v1/customers/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["status"] == "active"

    listing = await client.get("/api/v1/kyc", headers=headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "approved"
    assert items[0]["documents"][0]["documentType"] == "passport"

    customer = await session.get(Customer, __import__("uuid").UUID(me.json()["id"]))
    assert customer.status == "active"
