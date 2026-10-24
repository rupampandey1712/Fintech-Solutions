"""FastAPI composition root for the FinCore Python implementation."""

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from app.api import (
    aml,
    corrections,
    fraud,
    kyc,
    ledger,
    notifications,
    ratelimit,
    reconciliation,
    reports,
    transactions,
    treasury,
)
from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.auth import hash_password
from app.api.auth import router as auth_router
from app.api.beneficiaries import router as beneficiaries_router
from app.api.customers import router as customers_router
from app.api.disputes import router as disputes_router
from app.api.payments import router as payments_router
from app.api.settlements import router as settlements_router
from app.config.settings import get_settings
from app.infrastructure.database import SessionFactory, engine
from app.infrastructure.models import Customer, FraudRule
from app.infrastructure.telemetry import add_trace_ids, configure_telemetry
from app.worker import run_messaging

settings = get_settings()
logger = structlog.get_logger()
MACHINE_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")

# Helmet-equivalent security response headers (docs/SECURITY_HEADERS.md). The exact same set,
# with the exact same values, is applied by the .NET implementation, so an attacker comparing
# response headers across the two backends learns nothing about which stack answered.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=15552000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Origin-Agent-Cluster": "?1",
    "X-DNS-Prefetch-Control": "off",
}


class SecurityHeadersMiddleware:
    """Adds the helmet-equivalent headers to every response (raw ASGI, no per-request overhead).

    Headers already present are left alone, so a route that deliberately sets one wins.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)

class BodySizeLimitMiddleware:
    """Refuses request bodies over ``max_bytes`` with 413 (raw ASGI).

    uvicorn buffers whatever a client sends, so without a cap one oversized request could
    exhaust memory. A declared Content-Length is refused before any of the body is read; a body
    without one (chunked) is counted as it streams and refused once it passes the cap.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(self.max_bytes)(scope, receive, send)
            return

        received = 0
        refused = False
        started = False

        async def guarded_send(message) -> None:
            nonlocal started
            if refused:
                return  # the 413 has been sent; anything the app produces afterwards is dropped
            started = started or message["type"] == "http.response.start"
            await send(message)

        async def counting_receive():
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request" and not refused:
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Answer here: FastAPI's body parser would turn a raised error into a 400.
                    # The app is then told the client went away, so it stops reading.
                    if not started:
                        await _too_large(self.max_bytes)(scope, receive, send)
                    refused = True
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, counting_receive, guarded_send)
        except ClientDisconnect:
            if not refused:
                raise


def _too_large(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        {
            "type": "https://fincore.local/problems/request_too_large",
            "title": "Payload too large",
            "status": 413,
            "code": "request_too_large",
            "detail": f"Request bodies are limited to {max_bytes} bytes.",
            "traceId": "unavailable",
        },
        status_code=413,
        media_type="application/problem+json",
    )


# Human-readable refusal messages -> the machine codes clients branch on (the same codes the
# .NET implementation raises). Keyed on the lowercased detail text.
CODE_BY_DETAIL = {
    "source account is not active": "account_not_active",
    "beneficiary is not active": "beneficiary_not_active",
    "beneficiary destination is unavailable": "destination_unavailable",
    "payment currency must match source account": "currency_mismatch",
    "cross-currency transfers are not supported": "currency_mismatch",
    "source and destination accounts must differ": "self_transfer",
    "source account has no configured limits": "limits_not_configured",
    "per-transaction limit exceeded": "per_transaction_limit_exceeded",
    "insufficient available balance": "insufficient_funds",
    "this password is too common. choose one that is harder to guess.": "password_too_common",
    "daily transfer limit exceeded": "daily_limit_exceeded",
    "weekly transfer limit exceeded": "weekly_limit_exceeded",
    "monthly transfer limit exceeded": "monthly_limit_exceeded",
    "account ledger is not configured": "ledger_not_configured",
    "idempotency request is being processed": "request_in_progress",
    "payment blocked by fraud rules": "fraud_blocked",
}

DEFAULT_FRAUD_RULES = [
    FraudRule(
        code="large_amount",
        description="Payment amount exceeds the configured large-amount threshold",
        threshold_minor=5_000_000, weight=30,
    ),
    FraudRule(
        code="velocity_burst",
        description="Too many payments within one hour",
        threshold_minor=3, weight=25,
    ),
    FraudRule(
        code="new_beneficiary_high_amount",
        description="High amount to a beneficiary younger than one day",
        threshold_minor=2_000_000, weight=20,
    ),
    FraudRule(
        code="young_account_large_transfer",
        description="Large transfer from an account younger than one day",
        threshold_minor=1_000_000, weight=25,
    ),
    FraudRule(
        code="failed_attempts",
        description="Too many failed payment attempts within one hour",
        threshold_minor=3, weight=20,
    ),
]


async def seed_fraud_rules() -> None:
    try:
        async with SessionFactory() as session:
            count = await session.scalar(select(func.count()).select_from(FraudRule))
            if count == 0:
                session.add_all(DEFAULT_FRAUD_RULES)
                await session.commit()
                logger.info("fraud_rules_seeded", rules=len(DEFAULT_FRAUD_RULES))
    except SQLAlchemyError as exc:
        logger.warning("fraud_rules_seed_failed", error_type=type(exc).__name__, error=str(exc))


async def seed_admin_user() -> None:
    if not settings.admin_email or not settings.admin_password.get_secret_value():
        return
    email = settings.admin_email.strip().lower()
    try:
        async with SessionFactory() as session:
            existing = await session.scalar(
                select(Customer.id).where(Customer.email_normalized == email),
            )
            if existing is not None:
                return
            session.add(Customer(
                email_normalized=email,
                password_hash=hash_password(settings.admin_password.get_secret_value()),
                roles=["admin", "fraud_analyst", "finance_user"], status="active",
            ))
            await session.commit()
            logger.info("admin_user_bootstrapped", email=email)
    except SQLAlchemyError as exc:
        logger.warning("admin_user_seed_failed", error_type=type(exc).__name__, error=str(exc))


@asynccontextmanager
async def lifespan(_: FastAPI):
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_ids,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("application_starting", environment=settings.app_env)
    await seed_fraud_rules()
    await seed_admin_user()
    worker_task = None
    if settings.outbox_worker_enabled:
        worker_task = asyncio.create_task(run_messaging())
    yield
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()
    logger.info("application_stopped")


# Swagger UI, ReDoc and the OpenAPI schema are development tools that announce the framework;
# production serves none of them (the contract file is shared source, not an HTTP surface).
_production = settings.app_env.lower() in {"production", "prod"}
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    openapi_url=None if _production else "/openapi.json",
)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(beneficiaries_router)
app.include_router(customers_router)
app.include_router(payments_router)
app.include_router(kyc.router)
app.include_router(transactions.router)
app.include_router(ledger.router)
app.include_router(notifications.router)
app.include_router(fraud.router)
app.include_router(aml.router)
app.include_router(reconciliation.router)
app.include_router(reports.router)
app.include_router(admin_router)
app.include_router(treasury.router)
app.include_router(corrections.router)
app.include_router(disputes_router)
app.include_router(settlements_router)
ratelimit.add_rate_limit_middleware(app, get_settings)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    import uuid

    request.state.trace_id = (request.headers.get("X-Correlation-ID") or uuid.uuid4().hex)[:128]
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = request.state.trace_id
    return response


# Inside CORS, so a 413 still carries CORS headers and the browser can read the problem body.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
# Registered last so it is the outermost middleware: every response, including 429s from the
# rate limiter, carries CORS headers and the browser can read the problem body.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID", "traceparent",
    ],
    expose_headers=["X-Correlation-ID", "Retry-After"],
)
# Outermost of all: the security headers ride on every response, whatever produced it.
app.add_middleware(SecurityHeadersMiddleware)


def _problem(request: Request, status_code: int, code: str, title: str, detail: str,
             errors: list[dict[str, str]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://fincore.local/problems/{code}",
        "title": title,
        "status": status_code,
        "code": code,
        "detail": detail,
        "traceId": getattr(request.state, "trace_id", "unavailable"),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status_code, media_type="application/problem+json")


@app.exception_handler(StarletteHTTPException)
async def http_problem(request: Request, exception: StarletteHTTPException) -> JSONResponse:
    titles = {
        401: "Authentication required", 403: "Access denied",
        404: "Resource not found", 409: "Conflict", 413: "Payload too large",
        429: "Too many requests",
    }
    detail = (
        exception.detail if isinstance(exception.detail, str) else "Request could not be completed"
    )
    # The payment flow's human messages map to the machine codes the contract vocabulary uses
    # (the .NET implementation raises them directly), so clients branch on the same `code`
    # whichever backend answered.
    code = CODE_BY_DETAIL.get(detail.lower())
    if code is None:
        code = {
            401: "unauthorized", 403: "forbidden",
            404: "not_found", 409: "conflict",
        }.get(exception.status_code, "request_failed")
    if MACHINE_CODE.fullmatch(detail):
        # Handlers that raise with a machine-readable detail ("kyc_documents_required") expose
        # it as the problem code, which is what clients branch on (and what .NET returns).
        code = detail
    elif exception.status_code == 409 and "already exists" in detail.lower():
        code = "email_already_registered"
    elif exception.status_code == 401 and "invalid email or password" in detail.lower():
        code = "invalid_credentials"
    elif exception.status_code == 403 and "not active" in detail.lower():
        code = "customer_not_active"
    response = _problem(request, exception.status_code, code,
                        titles.get(exception.status_code, "Request could not be completed"), detail)
    response.headers.update(exception.headers or {})
    return response


@app.exception_handler(RequestValidationError)
async def validation_problem(request: Request, exception: RequestValidationError) -> JSONResponse:
    errors = [
        {
            "field": ".".join(
                str(part) for part in item.get("loc", ()) if part not in {"body", "query", "path"}
            ),
            "code": str(item.get("type", "invalid_value")),
        }
        for item in exception.errors()
    ]
    return _problem(request, 422, "validation_error", "Request validation failed",
                    "One or more fields are invalid.", errors)


@app.exception_handler(Exception)
async def unhandled_problem(request: Request, exception: Exception) -> JSONResponse:
    logger.error("unhandled_request_error", error_type=type(exception).__name__)
    return _problem(request, 500, "internal_error", "Internal server error",
                    "The request could not be completed.")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
async def ready() -> dict[str, str]:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("readiness_failed", dependency="postgresql", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Required dependency unavailable") from exc
    return {"status": "ready"}


@app.get("/api/v1", tags=["meta"])
async def api_root() -> dict[str, Any]:
    # Deliberately language-neutral: the same body is served by the .NET implementation, so the
    # API metadata does not reveal which stack answered (docs/SECURITY_HEADERS.md).
    return {"name": "FinCore API", "version": "0.1.0", "contract": "/openapi.json"}


configure_telemetry(app)
