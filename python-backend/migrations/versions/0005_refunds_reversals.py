"""Payment refunds, payment reversals, and at most one reversal per journal entry.

Revision ID: 0005_refunds_reversals
Revises: 0004_messaging_inbox
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_refunds_reversals"
down_revision = "0004_messaging_inbox"
branch_labels = None
depends_on = None

OLD_STATUSES = "'initiated','pending','processing','completed','failed','cancelled','rejected','expired'"
NEW_STATUSES = OLD_STATUSES + ",'reversed'"


def upgrade() -> None:
    op.create_table(
        "payment_refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),
    )
    op.create_index("ix_payment_refunds_payment_id", "payment_refunds", ["payment_id"])

    # A journal entry is corrected by exactly one reversal; the database enforces it so two
    # concurrent reversal requests cannot both succeed.
    op.create_index(
        "ux_journal_entries_reversal_of_id", "journal_entries", ["reversal_of_id"],
        unique=True, postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )

    op.drop_constraint("ck_payment_status", "payments", type_="check")
    op.create_check_constraint("ck_payment_status", "payments", f"status IN ({NEW_STATUSES})")


def downgrade() -> None:
    op.drop_constraint("ck_payment_status", "payments", type_="check")
    op.create_check_constraint("ck_payment_status", "payments", f"status IN ({OLD_STATUSES})")
    op.drop_index("ux_journal_entries_reversal_of_id", table_name="journal_entries")
    op.drop_index("ix_payment_refunds_payment_id", table_name="payment_refunds")
    op.drop_table("payment_refunds")
