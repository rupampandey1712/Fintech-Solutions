"""Helmet-equivalent security headers and technology-fingerprint hiding (docs/SECURITY_HEADERS.md).

Runs against the ASGI app without a database: only /health and routing 404s are exercised, so
no dependency overrides are needed.
"""

import httpx

from app.main import SECURITY_HEADERS, app


async def test_every_response_carries_the_security_headers():
    transport = httpx.ASGITransport(app=app)
    # /health (no dependencies) and a routing 404: both produced without a database.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/health", "/does-not-exist"):
            response = await client.get(path)
            for name, value in SECURITY_HEADERS.items():
                assert response.headers.get(name) == value, (path, name)


async def test_unmatched_routes_are_problem_json_not_fastapi_json():
    # FastAPI's default 404 body is {"detail": "Not Found"} — a framework fingerprint. The
    # exception handlers wrap every routing 404 in the contract's problem envelope instead.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "not_found" and body["status"] == 404


async def test_api_metadata_is_language_neutral():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1")
    assert response.json() == {
        "name": "FinCore API", "version": "0.1.0", "contract": "/openapi.json",
    }
