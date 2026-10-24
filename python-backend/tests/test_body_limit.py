"""Request bodies over the cap are refused with 413, declared or streamed."""

import httpx
from fastapi import FastAPI, Request

from app.main import BodySizeLimitMiddleware
from app.main import app as fincore_app


def _echo_app(max_bytes: int):
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    return BodySizeLimitMiddleware(app, max_bytes=max_bytes)


async def _post(app, content, headers=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/echo", content=content, headers=headers)


async def test_bodies_within_the_cap_pass_through():
    response = await _post(_echo_app(10), b"x" * 10)
    assert response.status_code == 200 and response.json() == {"received": 10}


async def test_a_declared_oversized_body_is_refused_before_it_is_read():
    response = await _post(_echo_app(10), b"x" * 11)
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


async def test_a_streamed_body_is_refused_once_it_passes_the_cap():
    async def chunks():
        for _ in range(4):
            yield b"x" * 5

    # No Content-Length: the body is counted as it arrives.
    response = await _post(_echo_app(10), chunks())
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


async def test_the_api_refuses_oversized_bodies_with_security_headers():
    big = b"x" * (8 * 1024 * 1024 + 1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fincore_app),
                                 base_url="http://t") as client:
        response = await client.post("/api/v1/auth/login", content=big,
                                     headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
