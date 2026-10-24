# CI Workflows

Build and verification workflows for the frontend, Python backend, .NET backend, Docker images,
API contract compatibility, and financial invariants:

* `python.yml` — ruff lint, pytest (unit), pip-audit security scan.
* `dotnet.yml` — restore, Release build, xUnit test run, `dotnet list package --vulnerable` scan.
* `frontend.yml` — `npm ci` and a production build (typecheck included).
* `contract.yml` — the contract-compatibility and financial-invariant pipelines:
  * **static-contract**: `contracts/openapi.yaml` routes must exist in both backends (no services needed).
  * **financial-invariants**: the pytest suite (ledger balance, idempotency, concurrency, holds,
    disputes, settlement, reconciliation) against a real PostgreSQL with the Alembic schema.
  * **dotnet-invariants**: the same schema, the xUnit suite against PostgreSQL.
  * **live-contract**: boots both APIs and runs the cross-backend contract tests
    (`tests/test_contract.py` live layer) to prove the responses stay compatible.
* `docker.yml` — builds the three Docker images on main.
