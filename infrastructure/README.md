# Infrastructure

Local development and emulator configuration for the FinCore platform.

## Services (docker-compose.yml at the repository root)

| Service | Image | Purpose |
| --- | --- | --- |
| `postgres` | postgres:16 | Authoritative datastore for both backends (Alembic owns the schema). |
| `redis` | redis:7 | Shared rate-limit counters and the fraud-rule/account-limit cache. |
| `servicebus-sql` | mssql/server | Backing store for the Azure Service Bus emulator. |
| `servicebus` | Azure Service Bus emulator 2.0.1 | Topic `fincore-events` with a `notifications` subscription; duplicate detection and dead-lettering on. |
| `azurite` | Azure Storage emulator | Private blob storage for KYC documents and generated reports. |
| `observability` | .NET Aspire dashboard | OTLP collector for traces/metrics (port 18889). |
| `python-api` | built from `python-backend/` | FastAPI implementation (outbox worker runs in-process). |
| `dotnet-api` | built from `dotnet-backend/` | ASP.NET Core implementation (BackgroundService workers). |
| `frontend` | built from `frontend/` | Shared React client; `VITE_API_URL` selects the backend. |

Start everything with `docker compose up --build`; see `docs/LOCAL_SETUP.md` and the
root `.env.example` for the environment variables (host ports, JWT, limits, provider
selection, seed admin).

## Emulator configuration

`servicebus/config.json` declares the topic, subscription, duplicate-detection window and
dead-lettering behaviour used by the Service Bus emulator. The APIs talk to it when
`MESSAGE_PROVIDER=servicebus`; with `MESSAGE_PROVIDER=inline` the outbox handlers run in the
worker process without a broker.

## Schema ownership

PostgreSQL schema changes are made through Alembic migrations in `python-backend/migrations/`
and applied with `alembic upgrade head`. Both backends map the same tables — the .NET
`FinCoreDbContext` mirrors the migrated schema, and the CI `contract.yml` workflow applies the
migrations before running either test suite. There are deliberately no parallel EF Core
migrations: one migration history prevents the two implementations from drifting apart.
