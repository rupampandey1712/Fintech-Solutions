# Cross-backend tests

Contract and financial-scenario tests for the two FinCore backends (architecture spec,
section 51). Run from this folder with the python-backend dependencies installed
(httpx and PyYAML come from `pip install -e "../python-backend[dev]"` plus `pyyaml`):

```bash
cd tests
python -m pytest -q
```

Two layers:

* **Static contract tests** (always run): every route in `contracts/openapi.yaml` must exist
  in the Python backend's own OpenAPI document and be declared by the .NET endpoint sources.
  This is the gate the *API contract compatibility* CI workflow runs — no services needed.
* **Live contract tests** (opt-in): the same scenarios against both running backends must
  produce compatible responses. Set both `FINCORE_PYTHON_URL` (e.g. `http://localhost:8000`)
  and `FINCORE_DOTNET_URL` (e.g. `http://localhost:5000`) to enable them.

The financial-consistency tests themselves (debits == credits, idempotent retry without
double-charging, concurrent-transfer safety, failed writes leaving a balanced ledger) live
next to the backends they exercise:

* `python-backend/tests/` — pytest, integration tests need `FINCORE_TEST_DATABASE_URL`
* `dotnet-backend/tests/FinCore.Tests/` — xUnit, integration tests need
  `FINCORE_TEST_CONNECTION_STRING`
