"""Initial FinCore financial schema.

Revision ID: 0001_initial_schema
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_customers_email_normalized", "customers", ["email_normalized"], unique=True)

    op.create_table(
        "customer_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("account_type", sa.String(24), nullable=False, server_default="wallet"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_account_currency"),
    )
    op.create_index("ix_customer_accounts_customer_id", "customer_accounts", ["customer_id"])

    op.create_table(
        "ledger_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer_accounts.id"), unique=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_reference", sa.String(120), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reversal_of_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("journal_entries.id")),
    )
    op.create_index("ix_journal_business_ref", "journal_entries", ["business_reference"])

    op.create_table(
        "journal_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ledger_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("side", sa.String(6), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_journal_line_positive_amount"),
        sa.CheckConstraint("side IN ('debit', 'credit')", name="ck_journal_line_side"),
    )
    op.create_index("ix_journal_lines_journal_id", "journal_lines", ["journal_id"])

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("source_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer_accounts.id"), nullable=False),
        sa.Column("beneficiary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_positive_amount"),
        sa.CheckConstraint("status IN ('initiated','pending','processing','completed','failed','cancelled','rejected','expired')", name="ck_payment_status"),
    )
    op.create_index("ix_payments_customer_id", "payments", ["customer_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("operation", sa.String(120), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("customer_id", "operation", "key", name="uq_idempotency_scope"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_pending", "outbox_events", ["published_at", "created_at"])

    op.execute("""
        CREATE FUNCTION reject_ledger_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Posted ledger records are immutable; create a reversal entry instead'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_journal_entries_immutable
        BEFORE UPDATE OR DELETE ON journal_entries
        FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)
    op.execute("""
        CREATE TRIGGER trg_journal_lines_immutable
        BEFORE UPDATE OR DELETE ON journal_lines
        FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)


def downgrade() -> None:
    raise NotImplementedError(
        "Financial history is not dropped by downgrade. Restore a disposable database snapshot instead."
    )
