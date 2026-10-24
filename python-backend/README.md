# FinCore Python Backend

FastAPI modular monolith with clean boundaries:

* `app/api/` — endpoint modules (auth, accounts, payments, corrections, disputes,
  settlements, kyc, transactions, ledger, notifications, fraud, aml, reconciliation,
  reports, admin, treasury, rate limiting)
* `app/services/` — use-case services (ledger postings, payment holds, provider simulator,
  notifications, KYC document store, audit, configuration, report storage, password policy)
* `app/domain/` — pure domain rules (money, ledger journal, fraud/AML rules, payment state
  machine, sanctions screening, Bloom filter)
* `app/agents/` — AI agents: the fraud investigator (LangGraph state graph over Gemini)
* `app/resources/` — bundled data files (the common-password list, the investigator's instructions)
* `app/infrastructure/` — SQLAlchemy models, database session, Redis cache, OpenTelemetry
* `migrations/` — Alembic migrations (the schema owner for **both** backends)
* `app/worker.py` — outbox dispatcher, event handlers, scheduled reconciliation
* `app/seed.py` — realistic demo data generated through the real API

## Run

```bash
alembic upgrade head        # apply the schema (needs DATABASE_URL)
uvicorn app.main:app        # http://localhost:8000
docker compose exec python-api python -m app.seed   # optional demo data
```

## Test

```bash
pytest -q                                             # unit tests, no services needed
FINCORE_TEST_DATABASE_URL=postgresql+asyncpg://... pytest -q   # + integration tests
ruff check app tests
```

Integration tests are gated on `FINCORE_TEST_DATABASE_URL` (see the root `.env.test.example`).
Notable flows: payments go through a deterministic provider simulator (failure modes are
driven by reference markers — see `app/services/provider.py`), money only moves when the
provider accepts, and the full event catalogue is written to the outbox in the same
transaction as the business data. A timed-out payment holds its funds until reconciliation
settles it (`settle_unconfirmed_payment` in `app/services/payment_holds.py`).
