"""Customer registration, login, refresh tokens, and bearer-token identity."""

import asyncio
import base64
import functools
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ratelimit import WINDOW_SECONDS, allow_account_login
from app.config.settings import get_settings
from app.infrastructure.database import get_session
from app.infrastructure.models import Customer, OutboxEvent, RefreshToken
from app.services.audit import record_audit
from app.services.password_policy import is_common

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
security = HTTPBearer(auto_error=False)
PASSWORD_HASH_ITERATIONS = 600_000
ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
REFRESH_TOKEN_BYTES = 48


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS, dklen=32,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_HASH_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(derived).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$")
        iterations = int(iterations_text)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, iterations, dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@functools.cache
def _dummy_password_hash() -> str:
    """A real hash of a random password, verified when the email is unknown.

    Login then costs the same full PBKDF2 run whether or not the account exists, so response
    time does not reveal which emails are registered.
    """
    return hash_password(secrets.token_urlsafe(32))


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    # Login is a lookup, not a registration, so it skips email-format rules. EmailStr rejects
    # special-use domains such as ".local", which locked out a bootstrapped ADMIN_EMAIL.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class CustomerResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: UUID
    email: str  # output: stored value, not re-validated
    status: str


class TokenResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str


def effective_roles(customer: Customer) -> list[str]:
    return customer.roles or ["customer"]


def create_access_token(customer_id: UUID, roles: list[str] | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(customer_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + ACCESS_TOKEN_LIFETIME,
        "roles": roles or ["customer"],
    }
    return jwt.encode(claims, settings.jwt_signing_key.get_secret_value(), algorithm="HS256")


def issue_refresh_token(customer: Customer) -> tuple[RefreshToken, str]:
    settings = get_settings()
    raw = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    token = RefreshToken(
        customer_id=customer.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_lifetime_days),
    )
    return token, raw


def require_roles(*required: str):
    async def dependency(customer: Customer = Depends(get_current_customer)) -> Customer:
        if not set(effective_roles(customer)) & set(required):
            raise HTTPException(status_code=403, detail="forbidden")
        return customer

    return dependency


@router.post("/register", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> CustomerResponse:
    email = str(request.email).strip().lower()
    if is_common(request.password):
        raise HTTPException(
            status_code=422,
            detail="This password is too common. Choose one that is harder to guess.",
        )
    existing = await session.scalar(select(Customer.id).where(Customer.email_normalized == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # PBKDF2 is deliberately slow CPU work; on the event loop it would stall every request.
    password_hash = await asyncio.to_thread(hash_password, request.password)
    customer = Customer(email_normalized=email, password_hash=password_hash)
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="An account with this email already exists",
        ) from exc
    # Spec section 17: CustomerCreated is announced on the event bus like every other
    # lifecycle event, so downstream services can react without coupling to registration.
    session.add(OutboxEvent(
        id=uuid4(), aggregate_id=customer.id, event_type="customer.created.v1", schema_version=1,
        payload={"customerId": str(customer.id), "email": email,
                 "roles": customer.roles or ["customer"]},
    ))
    await session.commit()
    await session.refresh(customer)
    await record_audit(session, customer.id, "auth.register", "customer", customer.id,
                       request=http_request)
    await session.commit()
    return CustomerResponse(id=customer.id, email=email, status=customer.status)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = str(request.email).strip().lower()
    # Checked before the password, and for unknown emails too, so the refusal reveals nothing
    # about whether the account exists.
    if not await allow_account_login(http_request, email):
        raise HTTPException(
            status_code=429, detail="rate_limited",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
    customer = await session.scalar(select(Customer).where(Customer.email_normalized == email))
    stored_hash = customer.password_hash if customer is not None else None
    valid = await asyncio.to_thread(
        lambda: verify_password(request.password, stored_hash or _dummy_password_hash()),
    )
    if not valid or customer is None:
        await record_audit(session, customer.id if customer else None, "auth.login_failed",
                           "customer", None, request=http_request)
        await session.commit()
        raise HTTPException(
            status_code=401, detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if customer.status in {"suspended", "blocked", "closed"}:
        raise HTTPException(status_code=403, detail="Customer access is not active")
    token, raw_refresh = issue_refresh_token(customer)
    session.add(token)
    response = TokenResponse(
        access_token=create_access_token(customer.id, effective_roles(customer)),
        expires_in=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
        refresh_token=raw_refresh,
    )
    await record_audit(session, customer.id, "auth.login", "customer", customer.id,
                       request=http_request)
    await session.commit()
    return response


class RefreshRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    refresh_token: str = Field(min_length=1, max_length=256)


async def _rotate_refresh_token(session: AsyncSession, raw_refresh: str) -> tuple[Customer, str]:
    token_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    customer = await session.get(Customer, token.customer_id)
    if customer is None or customer.status in {"suspended", "blocked", "closed"}:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    token.revoked_at = now
    new_token, new_raw = issue_refresh_token(customer)
    session.add(new_token)
    return customer, new_raw


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    customer, new_raw = await _rotate_refresh_token(session, request.refresh_token)
    await session.commit()
    return TokenResponse(
        access_token=create_access_token(customer.id, effective_roles(customer)),
        expires_in=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
        refresh_token=new_raw,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> None:
    token_hash = hashlib.sha256(request.refresh_token.encode()).hexdigest()
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await session.commit()


async def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> Customer:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_signing_key.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
        customer_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=401, detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if customer.status in {"suspended", "blocked", "closed"}:
        raise HTTPException(status_code=403, detail="Customer access is not active")
    return customer
