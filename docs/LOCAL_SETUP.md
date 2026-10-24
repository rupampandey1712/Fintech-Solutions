# Local Setup

The local stack uses Docker Compose for React, both APIs, PostgreSQL, Redis, and the two Azure emulators the production design depends on:

* **Azure Service Bus emulator** (with the SQL Server container it requires) carries integration events. The topology lives in `infrastructure/servicebus/config.json`: topic `fincore-events` with duplicate detection, subscription `notifications` with `MaxDeliveryCount` 5 and dead-lettering. The emulator caps message time-to-live at one hour and the duplicate-detection window at five minutes, so the local config uses `PT1H` and `PT5M`; use longer values on a real namespace.
* **Azurite** (Blob only) stores KYC documents in the private `kyc-documents` container.

The APIs use the same Azure SDKs locally as in Azure, so moving to real Service Bus and Storage accounts is a connection-string change.

1. Copy `.env.example` to `.env` if you want to override local defaults.
2. Run `docker compose up --build` from the repository root. The first start pulls SQL Server for the emulator (about 2 GB of RAM while running).
3. Open `http://localhost:5173` and pick Python or .NET with the backend switch in the top bar.
4. Python OpenAPI: `http://localhost:8000/docs`; .NET OpenAPI: `http://localhost:5000/openapi/v1.json`.

Host ports are configurable (`*_HOST_PORT` in `.env.example`). Service Bus and Azurite default to 5673/5301 and 10010 so they do not collide with other emulator stacks on the same machine; containers reach each other by service name, not host port.

## Event flow

Each API writes outbox rows in the same transaction as the business change. The outbox dispatcher (`OUTBOX_WORKER_ENABLED`, `OUTBOX_POLL_SECONDS`, `OUTBOX_MAX_ATTEMPTS`) publishes them to the topic with the outbox id as `MessageId`, so a re-publish after a crash is dropped by duplicate detection. A consumer in each API reads the `notifications` subscription, records the message id in `inbox_messages` in the same transaction as its writes (redeliveries are acknowledged without repeating them), and abandons failures so Service Bus dead-letters them after five deliveries. Both APIs share the subscription as competing consumers.

Set `MESSAGE_PROVIDER=inline` to run the handlers directly in the outbox worker without a broker, and `STORAGE_PROVIDER=filesystem` to write KYC documents under `KYC_STORAGE_PATH`. The test suites use both.

## Money in, money held

* **Funding.** Money enters only through a staff deposit: `POST /api/v1/admin/accounts/{accountId}/deposits` (roles `admin` or `finance_user`, `Idempotency-Key` required), or the **Treasury** page in the UI. Each deposit debits the per-currency `system:treasury:<CUR>` ledger, credits the customer, is audited, and notifies the customer (`account.funded.v1`). Set `ADMIN_EMAIL`/`ADMIN_PASSWORD` to get a staff login locally.
* **Held payments.** When fraud scoring lands in the review band, the payment is created `pending` and its amount moves to the `system:payment-holds:<CUR>` clearing ledger, out of the customer's balance but not yet at the beneficiary. Reviewing the fraud case settles it: *false positive* releases the funds (payment `completed`), *confirmed fraud* (`closed`) refunds them (payment `rejected`), *escalated* keeps them held.
* **Refunds and reversals.** Staff refund a completed payment (partially or in several steps, up to the original amount) or reverse a posting made in error, from the **Refunds & reversals** tab of the Treasury page. See [Refund vs reversal](FINTECH_CONCEPTS.md#refund-vs-reversal).

## Demo data

On a fresh database, seed realistic demo data through the real API:

```sh
docker compose exec python-api python -m app.seed
```

It creates 10 verified customers, 15 accounts, 30 beneficiaries, 100 payments spread over 90 days (5 of them raising AML alerts, 20 held by fraud review, of which analysts released 5 and refunded 3), 3 refunds, 1 reversal and a reconciliation run with two deliberate mismatches. Staff users are `fraud.analyst@example.com`, `finance.user@example.com`, `operations.user@example.com` and, unless `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set, `admin@example.com`. Every demo password is `Demo-Password-123`; they are fake and local-only. Customers include `olivia.chen@example.com`.

Running it again does nothing; `docker compose down -v` resets the database. Payment timestamps are back-dated to build the history, but ledger postings are not: the database rejects changes to posted entries, so ledger views show the seed time.

## Caching

Redis caches two pieces of slow-changing configuration with the cache-aside pattern, using the same keys and JSON in both APIs:

| Key | Contents | TTL | Invalidated when |
|---|---|---|---|
| `fincore:cache:v1:fraud-rules` | All fraud rules (read on every payment) | 60 s | A fraud rule is edited (`PATCH /admin/fraud-rules/{code}`) |
| `fincore:cache:v1:account:{id}:limits` | An account's configured limits | 10 min | Limits change (today they are only written when the account opens) |

Writers commit to PostgreSQL first and delete the key afterwards. Deleting before the commit would let a concurrent reader re-cache the old value. Because both APIs share the keys, an edit made through either one takes effect for both on the next read. If Redis is down, reads fall through to the database (retrying Redis after 30 seconds), and if a delete fails, the TTL bounds how long a stale value can live.

Deliberately **not** cached: balances and the running daily/weekly/monthly totals (always recomputed under the account locks), and the customer record used for authentication (status and roles must take effect immediately, for example when an account is suspended).

## Rate limiting

With `REDIS_URL` set (Compose sets it), both APIs keep rate-limit counters in Redis under `fincore:ratelimit:{bucket}:{principal}:{window}` with the same script, so one client has one budget across every replica and both implementations. If Redis is unreachable, each process falls back to in-memory counters and retries Redis after 30 seconds, so limits degrade instead of failing requests. Without `REDIS_URL` the counters are in-process (tests, single instance).

Login has a second budget per account, `RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_MINUTE` (10 by default), counted whatever IP the attempts come from. Its key holds a hash of the email (`login_account:account:<sha256 prefix>`), so Redis never stores an address, and both APIs derive the same key. A refusal is `429 rate_limited` with `Retry-After`.

## Fraud investigator agent

The AI investigator on the Fraud cases page is off until you set `GEMINI_API_KEY` (get one at aistudio.google.com) in `.env`; both APIs read it. `AGENT_MODEL` picks the Gemini model (`gemini-2.5-flash` by default), `AGENT_MAX_STEPS` caps model turns per investigation, `AGENT_TIMEOUT_SECONDS` caps its duration, and `RATE_LIMIT_AGENT_PER_MINUTE` limits investigations per user. Restart the APIs after changing them. The Python dependencies are `langgraph`, `langchain-core` and `langchain-google-genai`; the .NET ones are `Microsoft.Agents.AI` and `Microsoft.Agents.AI.OpenAI`. See [Architecture: Fraud investigator agent](ARCHITECTURE.md#fraud-investigator-agent).

## Other settings

The Python API applies its Alembic migrations on container start; the .NET API connects to the same database and starts after Python is healthy. Setting `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env` bootstraps an admin customer for reviewer/finance endpoints. Login/register/payment rate limits are configured with `RATE_LIMIT_ENABLED` and the `RATE_LIMIT_*_PER_MINUTE` variables; refresh tokens live for `REFRESH_TOKEN_LIFETIME_DAYS`. `MAX_REQUEST_BODY_BYTES` caps request bodies in both APIs (8 MiB, enough for a base64-encoded 5 MiB KYC document). `COMMON_PASSWORDS_PATH` points registration's common-password check at a list with one password per line; left empty, both APIs use their bundled list (`python-backend/app/resources/common_passwords.txt` and its identical .NET copy). The list is loaded into a Bloom filter at startup, so a change needs a restart.

Never commit production credentials. `GET /health` reports process liveness; `GET /ready` reports dependencies required for safe work.

The local transfer implementation is a development slice, not a payment processor. It does not move funds outside the internal ledger, settle with a bank/network, or run real AML/compliance screening; fraud rules, sanctions screening, and reconciliation use local simulation and configurable rules.

## Running the tests

Integration tests need PostgreSQL. On Windows, Docker Desktop's port proxy can reset host connections from asyncpg/Npgsql, so running the suites in containers on a shared network is the most reliable option:

```sh
docker network create fincore-test-net
docker run -d --name fincore-test-pg --network fincore-test-net --network-alias pg \
  -e POSTGRES_USER=fincore -e POSTGRES_PASSWORD=fincore-test -e POSTGRES_DB=fincore_test postgres:16.15
# Python: FINCORE_TEST_DATABASE_URL=postgresql+asyncpg://fincore:fincore-test@pg:5432/fincore_test pytest
# .NET (after `alembic upgrade head` against its database):
#   FINCORE_TEST_CONNECTION_STRING="Host=pg;Database=...;Username=fincore;Password=fincore-test" dotnet test
```
