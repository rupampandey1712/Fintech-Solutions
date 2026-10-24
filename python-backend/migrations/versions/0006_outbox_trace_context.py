"""Outbox rows carry the W3C trace context of the request that created them.

Revision ID: 0006_outbox_trace_context
Revises: 0005_refunds_reversals
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_outbox_trace_context"
down_revision = "0005_refunds_reversals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The dispatcher publishes events after the request has finished. Storing the request's
    # traceparent lets it continue the same distributed trace instead of starting a new one.
    op.add_column("outbox_events", sa.Column("trace_context", sa.String(64)))


def downgrade() -> None:
    op.drop_column("outbox_events", "trace_context")
