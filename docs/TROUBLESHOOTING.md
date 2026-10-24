# Incident Runbooks

These are response templates; deployment owners must provide thresholds, contacts, and escalation paths.

## Payment API down
Detect with availability/latency alerts. Check rollout, readiness, dependencies, traces. Halt rollout or route to healthy instances; never replay writes without idempotency keys. Restore and verify health plus a controlled idempotent request. Review errors and deployment.

## Service Bus unavailable
Inspect broker health/credentials and outbox age. Keep committed rows pending; do not mark published. Restore connectivity and resume bounded publishing. Verify delivery/deduplication and backlog; review duplicates.

## Database unavailable
Check health, saturation, and migrations. Reject new financial writes; do not acknowledge from cache. Restore/fail over per recovery plan; verify ledger/idempotency before reopening. Record RPO impact.

## Duplicate payment suspected
Inspect idempotency record, payment, journals, and provider evidence. Stop repeat submissions; use approved refund/reversal if confirmed. Verify balanced journals and reconciliation; preserve audit evidence and investigate cause.

## Fraud engine unavailable
Check configured fail policy; high-risk work should fail closed or enter manual review. Restore rules service, re-evaluate eligible pending payments idempotently, and audit overrides.

## Reconciliation mismatch
Preserve source records; compare IDs, amount, currency, settlement date. Never silently edit balances. Escalate, post approved referenced compensating entries, rerun reconciliation, and retain evidence.

## Incorrect deployment
Stop promotion; inspect health, errors, and integrity signals. Roll back if schema compatible, otherwise follow tested forward-fix/restore plan. Verify payment and ledger paths; document impact.

## Worker repeatedly fails
Inspect exception, message version, dependency, attempts, and idempotency. Retry boundedly then isolate in DLQ, never discard. Fix and replay with audit/deduplication; verify a single side effect.

## Customers cannot register or sign in
`422 password_too_common` at registration means the password is on the common-password list (`COMMON_PASSWORDS_PATH`, or the bundled list). About 0.1% of other passwords are refused too, a Bloom filter false positive; any other password works. `429 rate_limited` on login means the per-IP limit or the per-account limit (`RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_MINUTE`) was hit: wait for `Retry-After`. Many 429s on one account from many IPs suggest password guessing; review the `auth.login_failed` audit entries. `413 request_too_large` means a body over `MAX_REQUEST_BODY_BYTES`.

## Fraud investigator fails or is missing
No Investigate button: the agent is off (`GEMINI_API_KEY` unset, `GET /api/v1/fraud/agent/status` shows `enabled: false`) or the user is not an `admin`/`fraud_analyst`. `503 agent_not_configured`: same cause; set the key and restart the APIs. `502 agent_failed`: the Gemini call failed; check the key, quota and `AGENT_MODEL` name in the API logs. `502 agent_incomplete`: the agent used up `AGENT_MAX_STEPS` or stopped without a report; retry, or raise the limit. `504 agent_timeout`: raise `AGENT_TIMEOUT_SECONDS`. `429 rate_limited`: `RATE_LIMIT_AGENT_PER_MINUTE` was hit. A failed investigation changes nothing; the case can always be reviewed by hand.

## Redis unavailable
Bypass cache for authoritative reads and use conservative rate limits if counters are unavailable. Restore and rebuild disposable cache; verify no cached balance authorized spending.

## External provider unavailable
Preserve provider request IDs/idempotency keys; stop uncontrolled retries. Keep outcome pending/unknown until status inquiry and reconciliation resolve it. Recover, reconcile, notify, review timeout behavior. Timed-out payments hold their funds in the payment-holds ledger while `processing`; a reconciliation run settles each one from the provider's record. One still held after a run has a provider record that disagrees (usually the amount): investigate it, never edit the ledger by hand.
