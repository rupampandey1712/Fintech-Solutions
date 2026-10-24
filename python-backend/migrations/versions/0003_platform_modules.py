"""Platform modules: KYC, fraud, AML, notifications, audit, reconciliation, refresh tokens.

Revision ID: 0003_platform_modules
Revises: 0002_accounts_beneficiaries
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_platform_modules"
down_revision = "0002_accounts_beneficiaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("roles", postgresql.JSONB(), nullable=False, server_default='["customer"]'))
    op.add_column("payments", sa.Column("reference", sa.String(140)))
    op.add_column("payments", sa.Column("provider_reference", sa.String(120)))
    op.add_column("payments", sa.Column("provider_status", sa.String(24)))
    op.add_column("payments", sa.Column("risk_score", sa.Integer()))
    op.add_column("payments", sa.Column("risk_level", sa.String(16)))
    op.add_column("payments", sa.Column("risk_decision", sa.String(16)))
    op.add_column("payments", sa.Column("risk_reasons", postgresql.JSONB()))
    op.add_column("outbox_events", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))

    op.create_table(
        "kyc_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(300)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending','under_review','approved','rejected','expired')", name="ck_kyc_status"),
    )
    op.create_index("ix_kyc_applications_customer_id", "kyc_applications", ["customer_id"])

    op.create_table(
        "kyc_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kyc_application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("kyc_applications.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("document_type", sa.String(24), nullable=False),
        sa.Column("blob_path", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("document_type IN ('passport','national_id','proof_of_address')", name="ck_kyc_doc_type"),
        sa.CheckConstraint("status IN ('uploaded','verified','rejected')", name="ck_kyc_doc_status"),
    )
    op.create_index("ix_kyc_documents_kyc_application_id", "kyc_documents", ["kyc_application_id"])

    op.create_table(
        "fraud_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("threshold_minor", sa.BigInteger()),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "fraud_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id", ondelete="RESTRICT")),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("reasons", postgresql.JSONB()),
        sa.Column("resolution", sa.String(200)),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('open','investigating','escalated','closed','false_positive')", name="ck_fraud_case_status"),
    )
    op.create_index("ix_fraud_cases_customer_id", "fraud_cases", ["customer_id"])

    op.create_table(
        "aml_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("rule_code", sa.String(60), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('open','investigating','escalated','closed','false_positive')", name="ck_aml_status"),
    )
    op.create_index("ix_aml_alerts_customer_id", "aml_alerts", ["customer_id"])

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel", sa.String(12), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("channel IN ('email','sms','push','in_app')", name="ck_notification_channel"),
        sa.CheckConstraint("status IN ('queued','sent','failed')", name="ck_notification_status"),
    )
    op.create_index("ix_notifications_customer_id", "notifications", ["customer_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_mismatch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_mismatch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_external", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "reconciliation_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reconciliation_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_reference", sa.String(120)),
        sa.Column("internal_status", sa.String(16), nullable=False),
        sa.Column("external_status", sa.String(24)),
        sa.Column("internal_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("external_amount_minor", sa.BigInteger()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('matched','unmatched','amount_mismatch','status_mismatch','missing_internal','missing_external')",
            name="ck_recon_status",
        ),
    )
    op.create_index("ix_reconciliation_records_run_id", "reconciliation_records", ["run_id"])

    op.create_table(
        "outbox_dead_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("outbox_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("failure_reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_refresh_tokens_customer_id", "refresh_tokens", ["customer_id"])


def downgrade() -> None:
    raise NotImplementedError("Platform module history is not removed by downgrade. Restore a disposable snapshot instead.")
