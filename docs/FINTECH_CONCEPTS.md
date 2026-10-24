# FinTech Concepts

**Double-entry ledger:** each financial effect has multiple journal lines whose debits equal credits per currency. History is append-only; corrections use reversal and replacement entries.

**Payment, clearing, settlement, reconciliation:** payment is the instruction and lifecycle. Clearing exchanges and validates instructions; settlement discharges obligations between participants. Reconciliation compares independent records and keeps mismatches visible. Completed payment does not automatically mean externally settled.

**Unknown outcomes:** when a provider call times out, the money may or may not have moved at the provider. The safe response is neither "failed" (the customer might pay twice) nor "completed" (the money might never arrive): keep the payment pending, hold the funds so they cannot be spent again, and let reconciliation settle it from the provider's own record.

**Idempotency and delivery:** a key plus canonical request hash makes retries safe. The transactional outbox closes the database/message failure gap. Delivery is at least once, so consumers deduplicate and make side effects retry-safe.

**KYC, AML, fraud, risk:** KYC is identity verification workflow; AML monitoring flags activity for investigation; fraud rules score behavior and decide allow/review/block. These educational modules are not compliance certification or production decisioning. Document bytes remain in private object storage.

**Limits and concurrency:** configurable limits are checked atomically with fund reservation/posting. Database locking protects concurrent transfers. A stale cache cannot authorize spending.

## Refund vs reversal

Both put money back where it came from, and both are posted as **new** journal entries. A ledger is append-only, so the original is never edited or deleted. They answer different questions.

| | Refund | Reversal |
|---|---|---|
| Meaning | A business decision to give money back: goods returned, goodwill, a cancelled order | A correction: the original posting should never have happened (wrong account, duplicate, fraud found after release) |
| Original transaction | Stays valid; the payment remains `completed` | Declared erroneous; the payment becomes `reversed` |
| Amount | Any amount, partial and repeated, up to the original | Always the full posting, all-or-nothing |
| Ledger | One new journal `refund:<id>`: debit the recipient, credit the payer | A mirror of every original entry (`reversal:<id>`, each linked with `reversal_of_id`; at most one reversal per entry) |
| Recipient's balance | Must cover the refund (`recipient_insufficient_funds` otherwise) | Not required: a correction may leave the recipient negative, to be recovered separately |
| Idempotency | `Idempotency-Key`, replays return the first result | Natural: an entry can only be reversed once (unique index) |
| Combination | Not after a reversal | Not after a refund (`payment_has_refunds`): reverse only untouched postings, refund otherwise |
| Event | `payment.refunded.v1` | `payment.reversed.v1` |

In FinCore both are staff actions (`admin` or `finance_user`), audited with the actor and reason. Payment postings are reversed through `POST /payments/{id}/reversal`, which mirrors every journal of the payment. For a payment released from a fraud hold that means two journals: source → holds and holds → beneficiary. Other postings, such as a deposit credited to the wrong account, use `POST /admin/journal-entries/{id}/reversal`.

A chargeback (dispute raised through the card network) is a third, separate flow and is not implemented yet.
