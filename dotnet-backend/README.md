# FinCore .NET Backend

ASP.NET Core modular monolith implementing the same API contract as the Python backend:
`src/FinCore.Api` (minimal-API endpoints), `FinCore.Application` (use cases and DTOs),
`FinCore.Domain` (pure domain logic — money, journal drafts, fraud/AML rules, state machine,
Bloom filter),
and `FinCore.Infrastructure` (EF Core, providers, workers, integrations). Unit and
integration test projects live under `tests/FinCore.Tests`.

## Run

```bash
dotnet run --project src/FinCore.Api   # http://localhost:5000
```

Configuration is environment-driven (`DATABASE_URL` or `ConnectionStrings__FinCore`,
`JWT_SIGNING_KEY`, `MESSAGE_PROVIDER`, ...). See the root `.env.example` and `docs/LOCAL_SETUP.md`.

## Database

The PostgreSQL schema is owned by the Alembic migrations in `python-backend/migrations/` —
apply them with `alembic upgrade head` before starting either backend. `FinCoreDbContext`
maps the migrated tables one-to-one (including the provider-transaction, dispute and
settlement tables from migration 0007). One migration history is deliberate: it is what keeps
the two implementations contract-compatible. Integration tests therefore require a migrated
database; they are gated by `FINCORE_TEST_CONNECTION_STRING` (see `.env.test.example`).

## Background workers

`OutboxDispatcherWorker` and `ServiceBusConsumerWorker` are hosted `BackgroundService`s inside
the API process (`MESSAGE_PROVIDER=inline` runs the handlers directly; `servicebus` publishes
to the topic and consumes the subscription). There is no separate Worker project — compose
runs one API process per implementation, and the worker lifecycle follows the API's.

## Modules of note

* **Payments** — idempotent creation with a provider-simulator hop (success / failure /
  timeout / duplicate / delayed / partial, driven by reference markers). A timeout holds the
  funds until reconciliation settles the payment (`PaymentHoldService.SettleUnconfirmedAsync`).
* **Fraud investigator** — a Microsoft Agent Framework agent on Gemini (`FraudInvestigator`,
  `InvestigationFacts`) that drafts recommendations with read-only, case-scoped tools.
* **Authentication** — PBKDF2 passwords, registration screened against a common-password Bloom
  filter (`CommonPasswordList`, bundled list embedded from `FinCore.Infrastructure/Resources`),
  and login limited per account as well as per IP (`RateLimiter`).
* **Corrections** — refunds, payment reversals and journal reversals, all idempotent.
* **Disputes** — the chargeback workflow (open → evidence → decision; lost reverses the ledgers).
* **Settlements** — daily payout batches with a generated CSV settlement file.
* **Admin** — fraud rules, user roles, dead-letter listing and replay, audit logs.
