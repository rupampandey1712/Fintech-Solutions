"""Cross-backend contract tests (architecture spec, section 51).

Two layers:

1. **Static contract tests** (always run, no services needed): every path in
   ``contracts/openapi.yaml`` exists in the Python backend's own OpenAPI document, and the
   .NET backend declares the same routes in source. This is the gate the CI
   "contract compatibility" workflow runs.

2. **Live contract tests** (opt-in): the same requests against both running backends must
   produce compatible responses — same status codes, same response fields, same error
   envelope. Enabled by setting ``FINCORE_PYTHON_URL`` and ``FINCORE_DOTNET_URL``; skipped
   when either is unset.

Run with the python-backend dependencies installed (httpx, PyYAML, pytest)::

    cd tests && python -m pytest -q
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi.yaml"

PYTHON_URL = os.environ.get("FINCORE_PYTHON_URL", "").rstrip("/")
DOTNET_URL = os.environ.get("FINCORE_DOTNET_URL", "").rstrip("/")

skip_live = pytest.mark.skipif(
    not (PYTHON_URL and DOTNET_URL),
    reason="FINCORE_PYTHON_URL and FINCORE_DOTNET_URL are not both set",
)


def _load_contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _contract_paths() -> set[str]:
    return set(_load_contract()["paths"])


def _python_routes() -> set[str]:
    """Routes declared by the FastAPI app's own OpenAPI document."""
    import sys

    sys.path.insert(0, str(ROOT / "python-backend"))
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
    from app.main import app  # noqa: PLC0415  (late import keeps startup cheap)

    return set(app.openapi()["paths"])


def _dotnet_routes() -> set[str]:
    """Full route templates declared by the .NET API.

    Controllers: the class-level ``[Route("...")]`` scopes every ``[Http*("...")]`` action
    template in the same class block. ``Program.cs`` still declares a few top-level minimal
    API routes (health/readiness) with absolute paths. Templates are normalised to bare "{}"
    placeholders because the sources constrain parameters inline ("{paymentId:guid}") while
    the contract writes "{paymentId}".
    """
    api_dir = ROOT / "dotnet-backend" / "src" / "FinCore.Api"
    declared: set[str] = set()
    for path in api_dir.rglob("*.cs"):
        current_route: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            route_match = re.search(r'\[Route\("([^"]*)"(?:\s*,[^]]*)?\)\]', stripped)
            if route_match:
                # Each controller class carries exactly one class-level [Route]; the most
                # recent one scopes the [Http*] templates that follow.
                current_route = route_match.group(1).rstrip("/")
                if current_route.startswith("/api"):
                    declared.add(_normalise(current_route))
            for verb in ("Get", "Post", "Put", "Patch", "Delete"):
                verb_match = re.search(rf'\[Http{verb}\("([^"]*)"(?:\s*,[^]]*)?\)\]', stripped)
                if verb_match:
                    template = verb_match.group(1).strip("/")
                    joined = f"{(current_route or '').rstrip('/')}/{template}".rstrip("/")
                    if joined:
                        declared.add(_normalise(joined))
                elif re.fullmatch(rf'\[Http{verb}(?:\s*,[^]]*)?\]', stripped):
                    joined = (current_route or "").rstrip("/")
                    if joined:
                        declared.add(_normalise(joined))
            # Minimal API routes that survive the migration (health, readiness, meta).
            for leaf in re.findall(r'Map(?:Get|Post|Put|Patch|Delete)\("(/[^"]*)"', stripped):
                declared.add(_normalise(leaf))
    # Controller class routes are declared without a leading slash ("api/v1/..."); the
    # contract uses absolute paths, so normalise before comparing.
    return {(route if route.startswith("/") else f"/{route}") for route in declared if route}


def _normalise(path: str) -> str:
    """Compare routes positionally: "{paymentId:guid}" and "{payment_id}" both become "{}"."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_contract_covers_both_backends():
    contract = {_normalise(path) for path in _contract_paths()}
    python_paths = {_normalise(path) for path in _python_routes()}
    missing_in_contract = python_paths - contract
    assert not missing_in_contract, f"Routes missing from contracts/openapi.yaml: {sorted(missing_in_contract)}"
    stale_in_contract = contract - python_paths
    assert not stale_in_contract, f"Contract paths not implemented by Python: {sorted(stale_in_contract)}"


def test_dotnet_declares_the_contract_routes():
    contract = {_normalise(path) for path in _contract_paths()}
    declared = _dotnet_routes()
    missing = sorted(path for path in contract if path not in declared)
    assert not missing, f".NET backend does not declare contract routes: {missing}"


# --------------------------------------------------------------------------------------------
# Live contract tests: the same scenario against both backends.

def _base_urls() -> tuple[str, str]:
    return PYTHON_URL, DOTNET_URL


async def _register_and_login(httpx, base_url: str) -> tuple[dict, dict]:
    import uuid  # noqa: PLC0415

    email = f"contract-{uuid.uuid4().hex[:10]}@example.com"
    register = await httpx.post(f"{base_url}/api/v1/auth/register",
                                json={"email": email, "password": "Contract-Pass-123"})
    assert register.status_code == 201, register.text
    login = await httpx.post(f"{base_url}/api/v1/auth/login",
                             json={"email": email, "password": "Contract-Pass-123"})
    assert login.status_code == 200, login.text
    token = login.json()["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}
    account = await httpx.post(f"{base_url}/api/v1/accounts",
                               json={"accountType": "wallet", "currency": "USD"}, headers=headers)
    assert account.status_code == 201, account.text
    return headers, account.json()


@skip_live
def test_auth_and_accounts_are_contract_compatible():
    """Registration, login, token refresh and account creation must behave identically."""
    import httpx  # noqa: PLC0415
    import asyncio  # noqa: PLC0415

    async def scenario(base_url: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            headers, account = await _register_and_login(client, base_url)
            me = await client.get(f"{base_url}/api/v1/customers/me", headers=headers)
            accounts = await client.get(f"{base_url}/api/v1/accounts", headers=headers)
            return {
                "account_fields": sorted(account),
                "me_fields": sorted(me.json()),
                "accounts_shape": sorted(accounts.json()),
            }

    python, dotnet = _base_urls()
    left = asyncio.run(scenario(python))
    right = asyncio.run(scenario(dotnet))
    assert left["account_fields"] == right["account_fields"], (left, right)
    assert left["me_fields"] == right["me_fields"], (left, right)
    assert left["accounts_shape"] == right["accounts_shape"], (left, right)


@skip_live
def test_payment_lifecycle_and_errors_are_contract_compatible():
    """Payment, idempotent replay and the error envelope must match across backends."""
    import httpx  # noqa: PLC0415
    import asyncio  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    async def scenario(base_url: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            alice, alice_account = await _register_and_login(client, base_url)
            _, bob_account = await _register_and_login(client, base_url)
            beneficiary = await client.post(f"{base_url}/api/v1/beneficiaries", headers=alice, json={
                "destinationAccountId": bob_account["id"], "label": "bob",
            })
            assert beneficiary.status_code == 201, beneficiary.text
            beneficiary_id = beneficiary.json()["id"]
            await client.post(f"{base_url}/api/v1/beneficiaries/{beneficiary_id}/verify", headers=alice)

            # A plain customer cannot fund an account (deposits are staff operations), so the
            # comparable cross-backend surface here is the deterministic refusal contract: an
            # unfunded transfer, a key reused with a different body, and an anonymous read.
            key = {"Idempotency-Key": uuid.uuid4().hex}
            payment = await client.post(f"{base_url}/api/v1/payments", headers={**alice, **key}, json={
                "sourceAccountId": alice_account["id"], "beneficiaryId": beneficiary_id,
                "amountMinor": 12_500, "currency": "USD",
            })
            replay = await client.post(f"{base_url}/api/v1/payments", headers={**alice, **key}, json={
                "sourceAccountId": alice_account["id"], "beneficiaryId": beneficiary_id,
                "amountMinor": 1, "currency": "USD",
            })
            unauthorised = await client.get(f"{base_url}/api/v1/payments/{uuid.uuid4()}")
            return {
                "payment_status": payment.status_code,
                "payment_fields": sorted(payment.json()),
                "payment_code": payment.json().get("code"),
                "replay_status": replay.status_code,
                "replay_code": replay.json().get("code"),
                "unauthorised_status": unauthorised.status_code,
            }

    python, dotnet = _base_urls()
    left = asyncio.run(scenario(python))
    right = asyncio.run(scenario(dotnet))
    assert left["payment_status"] == right["payment_status"], (left, right)
    assert left["payment_fields"] == right["payment_fields"], (left, right)
    assert left["payment_code"] == right["payment_code"], (left, right)
    assert left["replay_status"] == right["replay_status"], (left, right)
    assert left["replay_code"] == right["replay_code"], (left, right)
    assert left["unauthorised_status"] == right["unauthorised_status"], (left, right)


def test_financial_invariants_seeded_by_the_pytest_suite():
    """The ledger, idempotency and concurrency invariant suites must exist and be collectable."""
    suite = ROOT / "python-backend" / "tests"
    required = [
        "test_ledger.py", "test_concurrency.py", "test_request_hash.py",
        "test_refunds_reversals.py", "test_provider_flow.py", "test_disputes_settlement.py",
    ]
    missing = [name for name in required if not (suite / name).exists()]
    assert not missing, f"Financial invariant test modules missing: {missing}"
    listing = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", *(f"{suite / name}" for name in required)],
        capture_output=True, text=True, cwd=ROOT / "python-backend",
    )
    assert listing.returncode == 0, listing.stdout + listing.stderr


def test_both_backends_bundle_the_same_common_password_list():
    """Registration refuses the same passwords whichever backend answers: the bundled lists that
    feed each Bloom filter must stay byte-for-byte identical."""
    python_list = ROOT / "python-backend" / "app" / "resources" / "common_passwords.txt"
    dotnet_list = (ROOT / "dotnet-backend" / "src" / "FinCore.Infrastructure" / "Resources"
                   / "common_passwords.txt")
    assert python_list.read_bytes() == dotnet_list.read_bytes()


def test_frontend_types_allow_null_wherever_the_contract_does():
    """A field the contract marks nullable must be nullable or optional in the shared React types.

    Typing one as always present lets code call methods on it unchecked: the admin audit log
    crashed on `actorId.slice()` for failed logins, whose actor is null.
    """
    import re

    types = (ROOT / "frontend" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
    interfaces = {match.group(1): match.group(2) for match in
                  re.finditer(r"export interface (\w+)[^{]*\{(.*?)\n\}", types, re.S)}
    drifted = []
    for name, schema in _load_contract()["components"]["schemas"].items():
        body = interfaces.get(name)
        if body is None:
            continue
        for prop, definition in schema.get("properties", {}).items():
            kind = definition.get("type")
            if not (isinstance(kind, list) and "null" in kind):
                continue
            field = re.search(rf"^\s*{prop}(\??):\s*([^;]+);", body, re.M)
            if field and not field.group(1) and "null" not in field.group(2):
                drifted.append(f"{name}.{prop}: `{field.group(2).strip()}`")
    assert not drifted, f"nullable in the contract but not in types/index.ts: {drifted}"


def test_both_backends_give_the_fraud_investigator_the_same_instructions():
    """The LangGraph (Python) and Agent Framework (.NET) investigators must behave alike: they load
    their instructions from two copies of one prompt file that must stay identical."""
    python_prompt = ROOT / "python-backend" / "app" / "resources" / "fraud_investigator_prompt.txt"
    dotnet_prompt = (ROOT / "dotnet-backend" / "src" / "FinCore.Infrastructure" / "Resources"
                     / "fraud_investigator_prompt.txt")
    assert python_prompt.read_bytes() == dotnet_prompt.read_bytes()
