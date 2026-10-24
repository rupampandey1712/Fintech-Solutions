"""Account limits and beneficiary lifecycle.

Revision ID: 0002_accounts_beneficiaries
Revises: 0001_initial_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_accounts_beneficiaries"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_limits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer_accounts.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("per_transaction_minor", sa.BigInteger(), nullable=False),
        sa.Column("daily_minor", sa.BigInteger(), nullable=False),
        sa.Column("weekly_minor", sa.BigInteger(), nullable=False),
        sa.Column("monthly_minor", sa.BigInteger(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("per_transaction_minor > 0 AND daily_minor > 0 AND weekly_minor > 0 AND monthly_minor > 0", name="ck_account_limits_positive"),
    )
    op.create_table(
        "beneficiaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_verification"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("cooling_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending_verification','cooling','active','disabled')", name="ck_beneficiary_status"),
    )
    op.create_index("ix_beneficiaries_customer_id", "beneficiaries", ["customer_id"])
    op.create_foreign_key("fk_payments_beneficiary", "payments", "beneficiaries", ["beneficiary_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    raise NotImplementedError("Financial/account history is not removed by downgrade. Restore a disposable snapshot instead.")
