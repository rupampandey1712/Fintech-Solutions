"""Provider transactions, chargeback disputes, settlement batches, dead-letter replay.

Revision ID: 0007_provider_settlement
Revises: 0006_outbox_trace_context

Also makes audit_logs append-only at the database level (architecture spec, section 23:
"Audit logs must be append-only"), the same guarantee journal entries already have.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_provider_settlement"
down_revision = "0006_outbox_trace_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The provider simulator's own record of every processed payment. Reconciliation compares
    # these rows against internal payments as two independent systems of record.
    op.create_table(
        "provider_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("reference", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('completed','failed','timeout','duplicate','delayed','partial_failure')",
            name="ck_provider_txn_status",
        ),
        sa.UniqueConstraint("payment_id", name="uq_provider_txn_payment"),
    )

    # Chargeback simulation: a customer disputes a payment, staff collect evidence and decide.
    op.create_table(
        "payment_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"), nullable=False, index=True,
        ),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id"), nullable=False, index=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('open','under_review','won','lost')",
                           name="ck_dispute_status"),
        sa.UniqueConstraint("payment_id", name="uq_dispute_payment"),
    )

    # Settlement: a business day's provider-settled payments grouped into one payout batch.
    op.create_table(
        "settlement_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("total_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fee_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("payment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_content", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.String(300), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id"), nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('pending','processing','completed','failed')",
                           name="ck_settlement_status"),
        sa.CheckConstraint("total_minor >= 0", name="ck_settlement_total_non_negative"),
    )
    op.create_table(
        "settlement_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("settlement_batches.id"), nullable=False, index=True,
        ),
        sa.Column(
            "payment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"), nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("fee_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("payment_id", name="uq_settlement_item_payment"),
    )

    # Manual replay (spec section 19): the time a dead-lettered event was re-queued.
    op.add_column(
        "outbox_dead_letters",
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Provider transactions without an internal payment are counted per run.
    op.add_column(
        "reconciliation_runs",
        sa.Column("missing_internal", sa.Integer(), nullable=False, server_default="0"),
    )

    # Audit logs are append-only: no UPDATE, no DELETE, from any role.
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs are append-only and cannot be modified or deleted'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade() -> None:
    raise NotImplementedError(
        "Financial/audit history is not removed by downgrade. "
        "Restore a disposable snapshot instead."
    )
