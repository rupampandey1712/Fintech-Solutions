# Security Notes

Educational foundation only; production use requires independent security, privacy, legal, and compliance review.

* Enforce authentication and resource ownership; use role/policy authorization for operational access.
* Validate DTOs and map only allowlisted fields. Use parameterized ORM/SQL operations.
* Hash passwords with a modern scheme. Use a managed secret store and managed identity in production.
* Require TLS in deployed environments. Keep blobs private and upload/download grants narrow and short-lived.
* Never log tokens, credentials, KYC contents, or unnecessary personal/financial data. Redact logs and traces.
* Apply rate limits, payload bounds, timeouts, secure CORS, and safe error messages. Both
  backends limit requests per IP or customer and, separately, login attempts per account
  (`RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_MINUTE`, keyed by a hash of the email), so guessing a
  password from many IPs shares one budget. Request bodies are capped at
  `MAX_REQUEST_BODY_BYTES`. Login runs a full password check even for unknown emails, so
  timing does not reveal which accounts exist.
* Both backends set helmet-equivalent security response headers and hide technology
  fingerprints (no `Server` header, docs gated to non-production, neutral API metadata) —
  see [Security headers and stack hardening](SECURITY_HEADERS.md).
* The fraud investigator agent sends case facts to Google Gemini. Its tools are read-only and scoped to the case being investigated, return aggregates rather than personal data, and label customer-typed text as untrusted; a person always makes the decision, and every run is audited. See [Architecture: Fraud investigator agent](ARCHITECTURE.md#fraud-investigator-agent). Review your data-processing obligations before enabling it with real data.
* Keep audit events append-only with least-privilege access and retention controls.
* The local beneficiary verification endpoint simulates a verification decision for learning; it is not an OTP, identity proof, or production control.
* Test idempotency and ledger consistency under concurrency; cache is never financial evidence.
* Rotate secrets, patch dependencies, scan images, review migrations, and test backup restoration.
