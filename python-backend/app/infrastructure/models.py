"""Initial relational schema primitives; domain-specific constraints evolve with migrations."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    roles: Mapped[list[Any] | None] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accounts: Mapped[list["CustomerAccount"]] = relationship(back_populates="customer")


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    __table_args__ = (CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_account_currency"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    account_type: Mapped[str] = mapped_column(String(24), default="wallet")
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    customer: Mapped[Customer] = relationship(back_populates="accounts")


class AccountLimit(Base):
    __tablename__ = "account_limits"
    __table_args__ = (
        CheckConstraint(
            "per_transaction_minor > 0 AND daily_minor > 0 "
            "AND weekly_minor > 0 AND monthly_minor > 0",
            name="ck_account_limits_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"), unique=True)
    per_transaction_minor: Mapped[int] = mapped_column(BigInteger)
    daily_minor: Mapped[int] = mapped_column(BigInteger)
    weekly_minor: Mapped[int] = mapped_column(BigInteger)
    monthly_minor: Mapped[int] = mapped_column(BigInteger)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class Beneficiary(Base):
    __tablename__ = "beneficiaries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_verification','cooling','active','disabled')",
            name="ck_beneficiary_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    destination_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    label: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="pending_verification")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooling_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("customer_accounts.id"), unique=True)
    code: Mapped[str] = mapped_column(String(120), unique=True)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        Index("ix_journal_business_ref", "business_reference"),
        Index("ux_journal_entries_reversal_of_id", "reversal_of_id", unique=True,
              postgresql_where=text("reversal_of_id IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    business_reference: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set on a reversal entry; unique (partial index), so an entry is reversed at most once.
    reversal_of_id: Mapped[UUID | None] = mapped_column(ForeignKey("journal_entries.id"))
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="journal")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_journal_line_positive_amount"),
        CheckConstraint("side IN ('debit', 'credit')", name="ck_journal_line_side"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    journal_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    ledger_account_id: Mapped[UUID] = mapped_column(ForeignKey("ledger_accounts.id"))
    side: Mapped[str] = mapped_column(String(6))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    journal: Mapped[JournalEntry] = relationship(back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payment_positive_amount"),
        CheckConstraint(
            "status IN ('initiated','pending','processing','completed','failed',"
            "'cancelled','rejected','expired','reversed')",
            name="ck_payment_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    source_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    beneficiary_id: Mapped[UUID] = mapped_column(Uuid)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reference: Mapped[str | None] = mapped_column(String(140))
    provider_reference: Mapped[str | None] = mapped_column(String(120))
    provider_status: Mapped[str | None] = mapped_column(String(24))
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    risk_decision: Mapped[str | None] = mapped_column(String(16))
    risk_reasons: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("customer_id", "operation", "key", name="uq_idempotency_scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"))
    operation: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_pending", "published_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_type: Mapped[str] = mapped_column(String(160))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # traceparent of the request that wrote the row; the dispatcher continues that trace.
    trace_context: Mapped[str | None] = mapped_column(String(64))


class KycApplication(Base):
    __tablename__ = "kyc_applications"
    __table_args__ = (CheckConstraint(
        "status IN ('pending','under_review','approved','rejected','expired')", name="ck_kyc_status"
    ),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    documents: Mapped[list["KycDocument"]] = relationship(back_populates="application")


class KycDocument(Base):
    __tablename__ = "kyc_documents"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('passport','national_id','proof_of_address')",
            name="ck_kyc_doc_type",
        ),
        CheckConstraint("status IN ('uploaded','verified','rejected')", name="ck_kyc_doc_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kyc_application_id: Mapped[UUID] = mapped_column(ForeignKey("kyc_applications.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(24))
    blob_path: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    application: Mapped[KycApplication] = relationship(back_populates="documents")


class FraudRule(Base):
    __tablename__ = "fraud_rules"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    threshold_minor: Mapped[int | None] = mapped_column(BigInteger)
    weight: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FraudCase(Base):
    __tablename__ = "fraud_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','investigating','escalated','closed','false_positive')",
            name="ck_fraud_case_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    payment_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("payments.id"))
    score: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="open")
    reasons: Mapped[list[Any] | None] = mapped_column(JSONB)
    resolution: Mapped[str | None] = mapped_column(String(200))
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AmlAlert(Base):
    __tablename__ = "aml_alerts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','investigating','escalated','closed','false_positive')",
            name="ck_aml_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    payment_id: Mapped[UUID | None] = mapped_column(Uuid)
    rule_code: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(24), default="open")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email','sms','push','in_app')", name="ck_notification_channel",
        ),
        CheckConstraint("status IN ('queued','sent','failed')", name="ck_notification_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(12))
    event_type: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched: Mapped[int] = mapped_column(Integer, default=0)
    amount_mismatch: Mapped[int] = mapped_column(Integer, default=0)
    status_mismatch: Mapped[int] = mapped_column(Integer, default=0)
    missing_external: Mapped[int] = mapped_column(Integer, default=0)
    missing_internal: Mapped[int] = mapped_column(Integer, default=0)


class ReconciliationRecord(Base):
    __tablename__ = "reconciliation_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('matched','unmatched','amount_mismatch','status_mismatch',"
            "'missing_internal','missing_external')",
            name="ck_recon_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    payment_id: Mapped[UUID] = mapped_column(Uuid)
    external_reference: Mapped[str | None] = mapped_column(String(120))
    internal_status: Mapped[str] = mapped_column(String(16))
    external_status: Mapped[str | None] = mapped_column(String(24))
    internal_amount_minor: Mapped[int] = mapped_column(BigInteger)
    external_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey("payments.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str | None] = mapped_column(String(200))
    journal_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("customers.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    consumer: Mapped[str] = mapped_column(String(60), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(160))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class OutboxDeadLetter(Base):
    __tablename__ = "outbox_dead_letters"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    outbox_event_id: Mapped[UUID] = mapped_column(Uuid)
    event_type: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str] = mapped_column(String(300))
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderTransaction(Base):
    """The external provider's own record of a processed payment.

    The simulator writes one row per provider call, so reconciliation compares two
    independent records (internal payment vs provider transaction) instead of reading
    the provider's answer out of the payment row.
    """

    __tablename__ = "provider_transactions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed','failed','timeout','duplicate','delayed','partial_failure')",
            name="ck_provider_txn_status",
        ),
        # The provider is another system: one payment id is processed at most once, exactly
        # like a real provider would reject a second submission with the same reference.
        UniqueConstraint("payment_id", name="uq_provider_txn_payment"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    payment_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(24))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    mode: Mapped[str] = mapped_column(String(24))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class PaymentDispute(Base):
    """Chargeback simulation: transaction -> dispute -> evidence -> decision (won/lost)."""

    __tablename__ = "payment_disputes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','under_review','won','lost')", name="ck_dispute_status",
        ),
        # One dispute per payment: a decided chargeback is final, lose or win.
        UniqueConstraint("payment_id", name="uq_dispute_payment"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey("payments.id"), index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    reason: Mapped[str] = mapped_column(String(200))
    evidence: Mapped[list[Any] | None] = mapped_column(JSONB, default=list)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementBatch(Base):
    """A day's provider-settled payments grouped for payout (spec section 58)."""

    __tablename__ = "settlement_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','completed','failed')",
            name="ck_settlement_status",
        ),
        CheckConstraint("total_minor >= 0", name="ck_settlement_total_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    business_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    payment_count: Mapped[int] = mapped_column(Integer, default=0)
    file_content: Mapped[str | None] = mapped_column(String)
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("customers.id"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementBatchItem(Base):
    __tablename__ = "settlement_batch_items"
    __table_args__ = (
        # A settled payment belongs to exactly one batch, like a real payout file.
        UniqueConstraint("payment_id", name="uq_settlement_item_payment"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("settlement_batches.id"), index=True)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey("payments.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
