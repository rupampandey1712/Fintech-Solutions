# Security Headers and Technology-Stack Hardening

Both backends now run a **helmet-equivalent security-headers layer** and a set of
**technology-fingerprint hiding** measures. The goal: an attacker probing the API learns
nothing about which programming language or framework serves a response — and the browser-side
baseline protections helmet provides are in place on every response.

This document covers the security-headers work and summarises the recent API-structure change
(minimal APIs → controllers). Both changes are behaviour-preserving: the HTTP contract in
`contracts/openapi.yaml` is unchanged.

---

## 1. Security response headers (the helmet port)

[Helmet](https://helmetjs.github.io/) is Express.js middleware that sets security-related HTTP
response headers. Neither FastAPI nor ASP.NET Core ships an equivalent, so both backends now
apply **the exact same set, with the exact same values**, from one shared definition per
stack — an attacker diffing headers across the two backends learns nothing about which one
answered.

| Header | Value | What it protects against |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Browsers reinterpreting responses as a different MIME type (drive-by XSS via MIME sniffing). |
| `X-Frame-Options` | `DENY` | The API being framed by a malicious page (clickjacking). |
| `Strict-Transport-Security` | `max-age=15552000; includeSubDomains` | Downgrade to plain HTTP once HTTPS is in front (180 days, helmet's default). |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' | Injection of third-party scripts, framing, and form hijacking on the pages the server does render (Swagger UI in development). |
| `Referrer-Policy` | `no-referrer` | Leaking internal URLs to third parties via the `Referer` header. |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Delegating powerful browser capabilities to embedded content. |
| `Cross-Origin-Opener-Policy` | `same-origin` | Cross-window attacks (Spectre-style side channels between origins). |
| `Cross-Origin-Resource-Policy` | `same-origin` | Other origins embedding this API's resources. |
| `Origin-Agent-Cluster` | `?1` | Isolates the origin into its own agent cluster (defense in depth). |
| `X-DNS-Prefetch-Control` | `off` | Suppresses speculative DNS lookups to third parties. |

Deliberately **not** set:

* `Cross-Origin-Embedder-Policy` (helmet 8 sets `require-corp`) — it breaks Swagger UI's
  cross-origin assets, and an API serves no embeddable subresources. Revisit only if the
  platform ever serves rich documents.
* `Cache-Control` — the APIs already send no cache directives and are never authority for a
  client; adding blanket `no-store` is a product decision, not a security necessity.

### Where the headers are applied

| Backend | Implementation | Registration |
|---|---|---|
| Python | `SecurityHeadersMiddleware` (raw-ASGI, zero per-request overhead) in `app/main.py`; the header set is the `SECURITY_HEADERS` constant | Outermost middleware, so 429s from the rate limiter, 401 challenges, problem responses and errors all carry the headers |
| .NET | `SecurityHeadersMiddleware` in `src/FinCore.Api/SecurityHeadersMiddleware.cs` | First middleware in `Program.cs`, same reason |

Headers already present on a response are never overwritten (Python uses a setdefault-style
check; .NET assigns before the pipeline runs), so a route that deliberately sets one wins.

---

## 2. Technology-fingerprint hiding

Headers alone are not enough — several default behaviours announce the stack:

### 2.1 The `Server` header

* **uvicorn** stamps `server: uvicorn` on every response → the Python Dockerfile runs
  `uvicorn ... --no-server-header`. For local runs: `uvicorn app.main:app --no-server-header`.
* **Kestrel** stamps `Server: Kestrel` → `Program.cs` calls
  `builder.WebHost.ConfigureKestrel(options => options.AddServerHeader = false);`.

Neither backend now emits a `Server` header at all.

### 2.2 Documentation endpoints

Swagger UI, ReDoc and the OpenAPI JSON are development tools whose content literally names the
framework. Both backends now gate them to non-production:

* **Python**: `FastAPI(docs_url=..., redoc_url=..., openapi_url=...)` — all three are `None`
  when `APP_ENV` is `production`/`prod`, so `/docs`, `/redoc` and `/openapi.json` simply do not
  exist there.
* **.NET**: `MapOpenApi` was already development-only; it now also serves at the **same path**
  as Python (`/openapi.json`) instead of the framework default `/openapi/v1.json`.

### 2.3 Language-neutral API metadata

`GET /api/v1` used to return `"FinCore Python API"` / `"FinCore .NET API"`. Both backends now
return the identical, language-neutral body:

```json
{ "name": "FinCore API", "version": "0.1.0", "contract": "/openapi.json" }
```

The frontend does not consume this endpoint; it exists as a discoverability breadcrumb and was
a free fingerprint giveaway.

### 2.4 Identical error bodies, even for unmatched routes

FastAPI's default 404 body is `{"detail": "Not Found"}` — a framework signature. Both backends
now answer **unmatched routes** with the contract's problem envelope, byte-for-byte identical:

```json
{
  "type": "https://fincore.local/problems/not_found",
  "title": "Resource not found",
  "status": 404,
  "code": "not_found",
  "detail": "Not Found",
  "traceId": "…"
}
```

* Python: routing 404s already flowed through the problem handlers.
* .NET: a `MapFallback` in `Program.cs` produces the same body instead of an empty 404.

### 2.5 Error codes that no longer differ

While verifying with the live cross-backend contract tests, a pre-existing divergence surfaced:
the same payment refusal returned `code: "conflict"` from Python but
`code: "beneficiary_not_active"` from .NET. Python's `app/main.py` now maps its human-readable
refusal messages to the machine codes the .NET implementation raises
(`insufficient_funds`, `per_transaction_limit_exceeded`, `daily_limit_exceeded`,
`destination_unavailable`, `fraud_blocked`, `request_in_progress`, …). Clients branch on
`code`, so both stacks are now indistinguishable in error responses too.

### 2.6 What still differs (and why that is acceptable)

Response headers that are inherent to transport, not stack: `content-length` vs
`transfer-encoding: chunked`, `content-type` charset suffixes, and the `traceId` format inside
problem bodies. None of these identify a language, and forcing them identical would mean
fighting each framework's HTTP layer for no security benefit.

---

## 3. Related structural change: minimal APIs → controllers

The .NET API's HTTP layer was also restructured in the same period. Summary; full rationale,
steps and pitfalls in [DOTNET_CONTROLLERS_MIGRATION.md](DOTNET_CONTROLLERS_MIGRATION.md):

* All 48 routes moved from lambda-based minimal API groups (`MapGet`/`MapPost` …) to
  `[ApiController]` classes in `src/FinCore.Api/Controllers/`. The old `*Endpoints.cs` files
  were deleted; `Program.cs` calls `app.MapControllers()`.
* Cross-cutting plumbing is centralised: one `DomainExceptionFilter` replaces every
  `try/catch → Problem(...)` block; `FinCoreControllerBase.CustomerId` replaces the per-route
  identity boilerplate; seven database-backed authorization policies replace the inline role
  checks (roles are still read from PostgreSQL per request, so role changes take effect
  immediately).
* Automatic model validation returns **422** (not MVC's default 400) via
  `InvalidModelStateResponseFactory`, keeping the contract and the Python implementation
  identical.
* Verification: the full .NET suite stayed green (111 tests), the static contract gate passes,
  and the **live cross-backend contract tests** — booting both APIs and comparing responses —
  pass against the controller build.

Controllers were chosen for discoverability (route, policy and response types visible on the
class) and unit-testability; the HTTP contract did not change, so the Python backend and the
frontend are unaffected.

---

## 4. Verifying

With both APIs running (`docker compose up --build`):

```bash
# Headers: compare side by side — the security headers must be identical, and there must be
# no `server` header from either backend.
curl -sI http://localhost:8000/health
curl -sI http://localhost:5000/health

# Fingerprint probes: all three must return problem+json with `code: "not_found"`.
curl -s http://localhost:8000/does-not-exist
curl -s http://localhost:5000/does-not-exist
curl -s http://localhost:5000/docs          # 404 in production; Swagger UI only in development

# Language-neutral metadata: identical bodies from both backends.
curl -s http://localhost:8000/api/v1
curl -s http://localhost:5000/api/v1
```

In the test suites:

* `python-backend/tests/test_security_headers.py` asserts the headers on every response, the
  problem+json 404, and the neutral metadata — no database needed.
* `tests/test_contract.py` keeps both backends' route tables and live responses aligned.
