# API Contract

Both backends expose the same JSON contract at `/api/v1`; each publishes OpenAPI. JSON fields are camelCase, amounts are integer minor units, timestamps are ISO-8601 UTC, and IDs are UUID strings. Protected routes use bearer tokens and enforce ownership on implemented operations. The shared contract (`contracts/openapi.yaml`) covers all implemented endpoint groups: auth with refresh tokens, accounts, beneficiaries, payments, transactions, ledger, KYC, notifications, fraud, AML, reconciliation, reports, and admin.

Registration and login use the same versioned PBKDF2-SHA256 hash encoding in both implementations so a local customer can authenticate against either API sharing the database. Access tokens are HS256 JWTs with matching issuer, audience, subject, issued-at, and 15-minute expiration claims. Login and refresh also issue refresh tokens, which rotate on `POST /api/v1/auth/refresh` and are revoked on `POST /api/v1/auth/logout`. Local signing-key defaults are for development only.

## Errors and pagination

```json
{
  "type": "https://fincore.local/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "code": "validation_error",
  "detail": "One or more fields are invalid.",
  "traceId": "00-...",
  "errors": [{ "field": "amountMinor", "code": "must_be_positive" }]
}
```

List response: `{ "items": [], "nextCursor": null }`. Payment creation requires `Idempotency-Key` with 8–128 non-whitespace characters. Reusing a key with a different body returns `409 idempotency_key_reused`.

Codes both backends return identically, besides the per-route ones:

| Status | `code` | When |
|---|---|---|
| 413 | `request_too_large` | The request body exceeds `MAX_REQUEST_BODY_BYTES` (8 MiB by default). |
| 422 | `password_too_common` | Registration with a password on the common-password list (checked through a Bloom filter). |
| 429 | `rate_limited` | A rate limit was exceeded: per IP or customer on every API route, and per account on login whatever the IP. Carries `Retry-After` (seconds). |
| 503 | `agent_not_configured` | The fraud investigator was called but no `GEMINI_API_KEY` is set. |
| 502 | `agent_failed` / `agent_incomplete` | The model provider failed, or the agent ended without a report (for example, it used up `AGENT_MAX_STEPS`). |
| 504 | `agent_timeout` | The investigation exceeded `AGENT_TIMEOUT_SECONDS`. |
| 401 | `invalid_credentials` | Login with a wrong email or password. The response does not say which, and takes the same time either way. |

## Routes and implementation status

Route availability is evolving: all groups below are implemented in both backends. KYC review, fraud case review, and fraud-rule administration require the admin or fraud_analyst role; reconciliation and admin customer/account management require admin or finance_user/admin respectively. Fraud and AML lists show all records to reviewers and only the caller's records to customers.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | process liveness |
| GET | `/ready` | dependency readiness |
| POST | `/api/v1/auth/register` | development customer registration |
| POST | `/api/v1/auth/login` | issue access + refresh tokens |
| POST | `/api/v1/auth/refresh` | rotate refresh token, issue access token |
| POST | `/api/v1/auth/logout` | revoke refresh token |
| GET | `/api/v1/customers/me` | view current customer profile |
| GET | `/api/v1/accounts` | list owned accounts |
| GET | `/api/v1/accounts/{id}` | account and balance view |
| POST | `/api/v1/accounts` | open a zero-balance account |
| POST | `/api/v1/beneficiaries` | add beneficiary |
| GET | `/api/v1/beneficiaries` | list owned beneficiaries |
| POST | `/api/v1/beneficiaries/{id}/verify` | verify beneficiary |
| POST | `/api/v1/beneficiaries/{id}/activate` | activate after cooling period |
| POST | `/api/v1/beneficiaries/{id}/disable` | disable beneficiary |
| POST | `/api/v1/payments` | post an idempotent same-currency internal transfer |
| GET | `/api/v1/payments` | cursor-paginated payments (status/limit/cursor) |
| GET | `/api/v1/payments/{id}` | payment state/details |
| GET | `/api/v1/transactions` | cursor-paginated ledger-derived history |
| GET | `/api/v1/transactions/{lineId}` | single transaction view |
| GET | `/api/v1/ledger/accounts/{id}` | ledger statement with derived balance |
| POST | `/api/v1/kyc` | create KYC application |
| POST | `/api/v1/kyc/{id}/documents` | attach base64 document |
| POST | `/api/v1/kyc/{id}/submit` | submit for sanctions screening/review |
| POST | `/api/v1/kyc/{id}/review` | approve/reject (admin/fraud_analyst) |
| GET | `/api/v1/kyc`, `/api/v1/kyc/{id}` | list and read applications |
| GET | `/api/v1/notifications` | notification feed |
| GET | `/api/v1/fraud/cases` | fraud case list (status/limit) |
| POST | `/api/v1/fraud/cases/{id}/review` | resolve case (admin/fraud_analyst) |
| POST | `/api/v1/fraud/cases/{id}/investigate` | AI investigator drafts a recommendation; changes nothing (admin/fraud_analyst) |
| GET | `/api/v1/fraud/agent/status` | whether the investigator is configured and usable by the caller |
| GET | `/api/v1/aml/alerts` | AML alert list (limit) |
| POST | `/api/v1/reconciliation/run` | run reconciliation vs provider simulator |
| GET | `/api/v1/reconciliation/runs[/{id}]` | list/read reconciliation runs |
| GET | `/api/v1/reports/dashboard` | payment/fraud/AML totals + monthly volume, one row per month per currency |
| GET | `/api/v1/reports/transactions` | daily volume series (?days=), one row per day per currency |
| GET | `/api/v1/admin/customers` | customers with roles (admin) |
| PATCH | `/api/v1/admin/customers/{id}/status` | change customer status (admin) |
| GET/PATCH | `/api/v1/admin/fraud-rules[/{code}]` | view/configure fraud rules |
| GET | `/api/v1/admin/audit-logs` | audit trail (admin) |
| PATCH | `/api/v1/admin/accounts/{id}/status` | change account status (admin) |
| POST | `/api/v1/payments/{id}/refund` | partial/full refund (staff, idempotent) |
| GET | `/api/v1/payments/{id}/refunds` | refund history (payer + staff) |
| POST | `/api/v1/payments/{id}/reversal` | mirror all journals of a payment (staff, idempotent) |
| POST | `/api/v1/admin/journal-entries/{id}/reversal` | reverse a non-payment posting (staff, idempotent) |
| GET | `/api/v1/admin/customers/{id}/payments` | customer payments with refunded totals (staff) |
| GET | `/api/v1/admin/customers/{id}/deposits` | customer deposits with reversal state (staff) |
| GET/PATCH | `/api/v1/admin/users[/{id}/roles]` | user list and role management (admin) |
| GET | `/api/v1/admin/outbox/dead-letters` | failed outbox events (admin/operations) |
| POST | `/api/v1/admin/outbox/dead-letters/{id}/replay` | re-queue a dead-lettered event |
| POST | `/api/v1/payments/{id}/dispute` | open a chargeback dispute (payer) |
| GET | `/api/v1/payments/{id}/dispute` | dispute view (payer + staff) |
| GET | `/api/v1/admin/disputes` | dispute list (staff) |
| POST | `/api/v1/admin/disputes/{id}/evidence` | append evidence, starts review (staff) |
| POST | `/api/v1/admin/disputes/{id}/decision` | decide won/lost; lost reverses the payment (admin/fraud_analyst) |
| GET/POST | `/api/v1/admin/settlement-batches[/{id}]` | settlement batch list/create/detail (staff) |
| POST | `/api/v1/admin/settlement-batches/{id}/process` | process batch, generate payout file (staff) |
| GET | `/api/v1/admin/settlement-batches/{id}/file` | download the settlement CSV (staff) |
| PATCH | `/api/v1/aml/alerts/{id}/status` | progress an AML alert (admin/fraud_analyst/compliance_officer) |
| GET | `/api/v1/reports/fraud` | fraud dashboard: cases, risk distribution, top rules |
| GET | `/api/v1/reports/finance` | finance dashboard: settlement + reconciliation totals |
| GET | `/api/v1/reports/{type}/export` | CSV export of any report (fraud/AML/recon/settlement: staff) |

## Payment payload

```json
{
  "sourceAccountId": "00000000-0000-0000-0000-000000000001",
  "beneficiaryId": "00000000-0000-0000-0000-000000000002",
  "amountMinor": 1050,
  "currency": "INR",
  "reference": "Invoice 42"
}
```

Response contains `id`, `status`, `amountMinor`, `currency`, `createdAt`, `reference`, `providerReference`/`providerStatus` (the external provider's view once submitted), and a risk object `{score, level, decision, reasons[]}` produced by the configurable fraud rules engine.

The payment flow in both backends walks the full state machine — `initiated → pending → processing → completed/failed` — inside one PostgreSQL transaction: it claims the idempotency key, checks ownership and active states, enforces configured transaction/window limits, verifies ledger-derived funds, scores fraud, submits the transfer to the deterministic provider simulator (which records its own provider transaction), and only posts the balanced debit/credit journal when the provider accepts. A provider failure marks the payment `failed` without moving money. A simulated timeout leaves it `processing` with its funds moved to the payment-holds ledger, so they cannot be spent twice while the outcome is unknown; the next reconciliation run settles it from the provider's record (released to the beneficiary and `completed`, or returned to the source and `failed`). A timed-out payment whose provider record disagrees on the amount is left held for a person to resolve. PostgreSQL advisory account locks serialize concurrent transfers. A repeated matching key returns its stored response; a key reused with a different request returns `409`. AML alerts, fraud cases, lifecycle events (`payment.initiated/processed/completed/failed`, `ledger.posted`, `transaction.created`, `reconciliation.required`), and audit entries are recorded in the same flow; outbox delivery with retry/dead-letter handling and manual replay is performed by the worker, and reconciliation compares internal payments against the provider's own transaction records (matched, amount/status mismatch, missing external, missing internal). Held payments settled by reviewers and refunds/reversals/disputes/settlement batches are documented in the routes table above.

Report series never add amounts in different currencies together. `monthlyVolume` and the daily `series` carry a `currency` on every row, and each currency with activity in the window has its own zero-filled series, ordered by period then currency. With no payments in the window the list is empty. Report scope follows the caller: platform-wide for `admin`, the caller's own payments otherwise.

Beneficiaries are attached to another customer's active account. Verification is a local development simulation, followed by a configurable cooling period; it does not prove account ownership or deliver a real OTP. Only active beneficiaries are eligible for payment initiation. These routes and their current response shape are described in `contracts/openapi.yaml`.
