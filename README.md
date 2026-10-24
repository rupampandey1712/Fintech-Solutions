# FinCore

Educational, production-style fintech transaction and payment platform with independent FastAPI and ASP.NET Core backends and one shared React client.

**Status:** The full platform module set is implemented in BOTH backends: KYC with document storage, a configurable fraud rules engine with case review and held-payment settlement, AML monitoring with alert review, notifications across four channels, reconciliation against a provider simulator's own transaction records, refunds/reversals, the chargeback dispute workflow, settlement batches with generated payout files, a provider hop with deterministic failure modes (failure/timeout/duplicate/delayed/partial), an outbox dispatcher with retry, dead-letter handling and manual replay, reports with CSV export (per currency), ledger/transactions APIs, admin user management, rate limiting (per IP or customer, plus login attempts per account), request size limits, common-password screening through a Bloom filter, an AI fraud investigator agent (LangGraph in Python, Microsoft Agent Framework in .NET, both on Gemini) that drafts recommendations for analysts, refresh-token rotation, and append-only audit logging. Payments whose provider call times out hold their funds until reconciliation settles them. The shared React app implements every workflow including treasury, disputes and settlements. Test suites cover both backends (pytest for FastAPI, xUnit for ASP.NET Core), the cross-backend contract suite gates the shared OpenAPI contract, and CI workflows build, scan, and test each component. Builds and Compose startup have not yet been verified end-to-end with real services, and no external settlement occurs. See [Architecture](docs/ARCHITECTURE.md), [Database and ERD](docs/DATABASE.md), [API contract](docs/API.md), and [Local setup](docs/LOCAL_SETUP.md). Do not use this educational project to handle real funds or sensitive personal data.

The system begins as two modular monoliths, one per language. PostgreSQL is authoritative; money uses integer minor units; financial journals are balanced and immutable; idempotency and an outbox protect retries and event publication. Customer passwords use a shared PBKDF2 encoding so local data can be accessed through either implementation. The React app switches APIs through `VITE_API_URL` and owns no financial business rules.

```text
frontend/                 Shared React application
python-backend/           FastAPI implementation
dotnet-backend/           ASP.NET Core implementation
infrastructure/           Compose and emulator configuration
tests/                    Cross-backend contract/scenario tests
docs/                     Architecture and operational docs
.github/workflows/        CI workflows
```

Implemented scope: architecture/data model, auth with refresh-token rotation, accounts, beneficiaries, payments with the provider hop, ledger/transactions, KYC, fraud rules and case review, AML, notifications, reconciliation with the provider simulator, refunds, reversals, chargebacks, settlement, outbox delivery/retry/DLQ replay, reports with CSV export, admin endpoints, rate limiting, and audit logging in both backends. The pytest suite (129 tests) and the xUnit suite (119 facts/theories) cover the backends, the root `tests/` contract suite gates the shared OpenAPI contract, and GitHub Actions workflows build/test the Python backend, .NET backend, frontend, contract compatibility with financial invariants, and Docker images. Transfers do not settle externally; the reconciliation step compares against a deterministic provider simulator. Local architecture is not a claim that production Azure services are deployed.

## Run locally

With Docker installed, run `docker compose up --build`. Open the dashboard at `http://localhost:5173`; Python API at `http://localhost:8000`; .NET API at `http://localhost:5000`. Current API workflows include health/readiness, customer registration/login/refresh/logout, accounts, beneficiaries, payments, transactions, ledger, KYC, notifications, fraud cases, AML alerts, reconciliation, reports, and admin management. Build and Compose execution have not been validated in this environment.
