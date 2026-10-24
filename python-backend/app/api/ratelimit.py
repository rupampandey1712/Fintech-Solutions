"""Fixed-window rate limiting keyed by bucket and principal.

With REDIS_URL set, counters live in Redis so every API replica (and the .NET implementation,
which uses the same key scheme and script) shares one budget per principal. If Redis is
unreachable the limiter degrades to per-process counters instead of failing requests, and
retries Redis after a short back-off.
"""

import hashlib
import time

import jwt
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger()

WINDOW_SECONDS = 60
REDIS_RETRY_SECONDS = 30.0
# INCR and EXPIRE in one round trip, atomically: the first hit in a window sets the expiry.
INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def window_key(bucket: str, principal: str, window_start: int) -> str:
    return f"fincore:ratelimit:{bucket}:{principal}:{window_start}"


class RateLimiter:
    """In-process counters: the fallback, and the backend when Redis is not configured."""

    def __init__(self, clock=time.time, window_seconds: float = WINDOW_SECONDS) -> None:
        self._clock = clock
        self._window = window_seconds
        self._counters: dict[tuple[str, str, int], int] = {}

    def check(self, bucket: str, principal: str, limit: int) -> bool:
        if limit <= 0:
            return True
        window_index = int(self._clock() // self._window)
        key = (bucket, principal, window_index)
        count = self._counters.get(key, 0) + 1
        self._counters[key] = count
        if len(self._counters) > 100_000:
            self._counters = {k: v for k, v in self._counters.items() if k[2] == window_index}
        return count <= limit

    async def acquire(self, bucket: str, principal: str, limit: int) -> bool:
        return self.check(bucket, principal, limit)


class RedisRateLimiter:
    def __init__(self, redis, fallback: RateLimiter, clock=time.time,
                 window_seconds: int = WINDOW_SECONDS) -> None:
        self._redis = redis
        self._fallback = fallback
        self._clock = clock
        self._window = window_seconds
        self._redis_retry_at = 0.0

    async def acquire(self, bucket: str, principal: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = self._clock()
        if now < self._redis_retry_at:
            return self._fallback.check(bucket, principal, limit)
        window_start = int(now // self._window * self._window)
        try:
            count = await self._redis.eval(
                INCREMENT_SCRIPT, 1, window_key(bucket, principal, window_start), self._window + 5,
            )
        except Exception as exc:  # redis errors, timeouts, refused connections
            self._redis_retry_at = now + REDIS_RETRY_SECONDS
            logger.warning("rate_limit_redis_unavailable", error_type=type(exc).__name__,
                           fallback="in_process", retry_in_seconds=REDIS_RETRY_SECONDS)
            return self._fallback.check(bucket, principal, limit)
        return int(count) <= limit


def build_limiter(redis_url: str) -> RateLimiter | RedisRateLimiter:
    fallback = RateLimiter()
    if not redis_url:
        return fallback
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, socket_timeout=0.25, socket_connect_timeout=0.25)
    return RedisRateLimiter(client, fallback)


def account_principal(normalized_email: str) -> str:
    """The per-account login principal. Hashed so Redis keys never carry an email address; the
    .NET implementation derives the same value, so both share one budget per account."""
    return "account:" + hashlib.sha256(normalized_email.encode()).hexdigest()[:32]


async def allow_account_login(request: Request, normalized_email: str) -> bool:
    """Spend one login attempt from the account's budget. True when the attempt may proceed."""
    settings = request.app.state.rate_limit_settings()
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None or not settings.rate_limit_enabled:
        return True
    return await limiter.acquire(
        "login_account", account_principal(normalized_email),
        settings.rate_limit_login_per_account_per_minute,
    )


def classify_bucket(method: str, path: str) -> str:
    if method == "POST" and path == "/api/v1/auth/login":
        return "login"
    if method == "POST" and path == "/api/v1/auth/register":
        return "register"
    if method == "POST" and path == "/api/v1/payments":
        return "payment"
    # Each investigation calls a paid model API several times, so it gets its own small budget.
    if (method == "POST" and path.startswith("/api/v1/fraud/cases/")
            and path.endswith("/investigate")):
        return "agent"
    if path.startswith("/api/"):
        return "default"
    return "exempt"


def add_rate_limit_middleware(app: FastAPI, get_settings_func) -> None:
    limiter = build_limiter(get_settings_func().redis_url)
    # Shared with handlers that need a budget the middleware cannot key (login per account).
    app.state.rate_limiter = limiter
    app.state.rate_limit_settings = get_settings_func

    def principal_for(request: Request) -> str:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            settings = get_settings_func()
            try:
                claims = jwt.decode(
                    authorization.removeprefix("Bearer "),
                    settings.jwt_signing_key.get_secret_value(),
                    algorithms=["HS256"],
                    options={"verify_aud": False, "verify_iss": False},
                )
                return f"customer:{claims.get('sub')}"
            except jwt.InvalidTokenError:
                pass
        return f"ip:{request.client.host}" if request.client else "ip:anonymous"

    async def rate_limit_middleware(request: Request, call_next):
        settings = get_settings_func()
        if not settings.rate_limit_enabled:
            return await call_next(request)
        bucket = classify_bucket(request.method, request.url.path)
        if bucket == "exempt":
            return await call_next(request)
        limits = {
            "login": settings.rate_limit_login_per_minute,
            "register": settings.rate_limit_register_per_minute,
            "payment": settings.rate_limit_payment_per_minute,
            "agent": settings.rate_limit_agent_per_minute,
            "default": settings.rate_limit_default_per_minute,
        }
        if not await limiter.acquire(bucket, principal_for(request), limits[bucket]):
            return JSONResponse(
                {
                    "type": "https://fincore.local/problems/rate_limited",
                    "title": "Too many requests",
                    "status": 429,
                    "code": "rate_limited",
                    "detail": "Rate limit exceeded. Retry later.",
                    "traceId": getattr(request.state, "trace_id", "unavailable"),
                },
                status_code=429,
                media_type="application/problem+json",
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
        return await call_next(request)

    app.middleware("http")(rate_limit_middleware)
