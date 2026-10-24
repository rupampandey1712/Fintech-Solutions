"""Ledger code length fix and the message inbox used for idempotent event consumption.

Revision ID: 0004_messaging_inbox
Revises: 0003_platform_modules
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_messaging_inbox"
down_revision = "0003_platform_modules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "customer:<uuid>:account:<uuid>" is 90 characters; varchar(80) rejected every new account.
    op.alter_column("ledger_accounts", "code", type_=sa.String(120), existing_nullable=False)

    # Service Bus delivers at least once. A consumer records each message id here in the same
    # transaction as its side effects, so a redelivered message is acknowledged without
    # repeating them.
    op.create_table(
        "inbox_messages",
        sa.Column("consumer", sa.String(60), primary_key=True),
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("inbox_messages")
    op.alter_column("ledger_accounts", "code", type_=sa.String(80), existing_nullable=False)
