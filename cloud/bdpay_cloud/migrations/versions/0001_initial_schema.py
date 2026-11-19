"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-18

Generated from bdpay_cloud.models and reviewed. tests/test_migrations.py fails
if the models and the migrated schema ever drift apart.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_emails",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name=op.f("ck_outbound_emails_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbound_emails")),
    )
    op.create_index(
        "ix_outbound_emails_due",
        "outbound_emails",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "provider_tokens",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_provider_tokens")),
    )
    op.create_index(op.f("ix_provider_tokens_expires_at"), "provider_tokens", ["expires_at"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), server_default="", nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_platform_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lower")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("last_beat", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_worker_heartbeats")),
    )
    op.create_table(
        "email_tokens",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')", name=op.f("ck_email_tokens_purpose")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
    )
    op.create_index(op.f("ix_email_tokens_user_id"), "email_tokens", ["user_id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("live_status", sa.Text(), server_default="inactive", nullable=False),
        sa.Column("live_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_attestation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("live_status IN ('inactive', 'pending', 'active')", name=op.f("ck_workspaces_live_status")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_workspaces_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_label", sa.Text(), server_default="", nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_audit_log_workspace_id_workspaces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_workspace", "audit_log", ["workspace_id", "id"], unique=False)
    op.create_table(
        "invitations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'developer', 'analyst')", name=op.f("ck_invitations_role")),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_invitations_invited_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_invitations_workspace_id_workspaces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_workspace_id"), "invitations", ["workspace_id"], unique=False)
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'developer', 'analyst')", name=op.f("ck_memberships_role")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_memberships_workspace_id_workspaces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_memberships")),
    )
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_table(
        "workspace_envs",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("callback_secret_enc", sa.Text(), nullable=False),
        sa.Column("result_url", sa.Text(), nullable=True),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('sandbox', 'live')", name=op.f("ck_workspace_envs_mode")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_envs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "mode", name=op.f("pk_workspace_envs")),
        sa.UniqueConstraint("public_key", name=op.f("uq_workspace_envs_public_key")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("display", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_api_keys_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_api_keys_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index("ix_api_keys_scope", "api_keys", ["workspace_id", "mode"], unique=False)
    op.create_table(
        "gateway_configs",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("gateway", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("secrets_enc", sa.Text(), nullable=True),
        sa.Column(
            "secret_hints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.Text(), server_default="unverified", nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('unverified', 'verified', 'rejected')", name=op.f("ck_gateway_configs_status")),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_gateway_configs_updated_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_gateway_configs_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "mode", "gateway", name=op.f("pk_gateway_configs")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_idempotency_keys_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "mode", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"], unique=False)
    op.create_table(
        "payment_links",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("amount_poisha", sa.BigInteger(), nullable=True),
        sa.Column("min_poisha", sa.BigInteger(), nullable=True),
        sa.Column("max_poisha", sa.BigInteger(), nullable=True),
        sa.Column(
            "allowed_gateways", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("collect", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("success_url", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_payments", sa.Integer(), nullable=True),
        sa.Column("paid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_poisha IS NULL OR amount_poisha > 0", name=op.f("ck_payment_links_amount_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_payment_links_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_payment_links_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_links")),
    )
    op.create_index("ix_payment_links_scope", "payment_links", ["workspace_id", "mode", "created_at"], unique=False)
    op.create_table(
        "simulator_payments",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("order_ref", sa.Text(), nullable=False),
        sa.Column("amount_poisha", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="created", nullable=False),
        sa.Column("paid_poisha", sa.BigInteger(), nullable=True),
        sa.Column("txn_id", sa.Text(), nullable=True),
        sa.Column("refunded_poisha", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "return_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode = 'sandbox'", name=op.f("ck_simulator_payments_sandbox_only")),
        sa.CheckConstraint(
            "status IN ('created', 'completed', 'failed', 'cancelled')", name=op.f("ck_simulator_payments_status")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_simulator_payments_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulator_payments")),
    )
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{*}'::text[]"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_webhook_endpoints_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_webhook_endpoints_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
    )
    op.create_index("ix_webhook_endpoints_scope", "webhook_endpoints", ["workspace_id", "mode"], unique=False)
    op.create_table(
        "checkout_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("amount_poisha", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), server_default="BDT", nullable=False),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "customer", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "allowed_gateways", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("success_url", sa.Text(), nullable=True),
        sa.Column("cancel_url", sa.Text(), nullable=True),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("paid_order_id", sa.Text(), nullable=True),
        sa.Column("payment_link_id", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'paid', 'expired', 'cancelled')", name=op.f("ck_checkout_sessions_status")
        ),
        sa.CheckConstraint("amount_poisha > 0", name=op.f("ck_checkout_sessions_amount_positive")),
        sa.ForeignKeyConstraint(
            ["payment_link_id"],
            ["payment_links.id"],
            name=op.f("fk_checkout_sessions_payment_link_id_payment_links"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_checkout_sessions_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkout_sessions")),
    )
    op.create_index(
        "ix_checkout_sessions_open_expiry",
        "checkout_sessions",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_checkout_sessions_reference",
        "checkout_sessions",
        ["workspace_id", "mode", "reference"],
        unique=False,
        postgresql_where=sa.text("reference IS NOT NULL"),
    )
    op.create_index(
        "ix_checkout_sessions_scope", "checkout_sessions", ["workspace_id", "mode", "created_at"], unique=False
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(collation="C"), nullable=False),
        sa.Column("gateway", sa.Text(), nullable=False),
        sa.Column("amount_poisha", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), server_default="BDT", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "customer", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("provider_payment_id", sa.Text(), nullable=True),
        sa.Column("provider_txn_id", sa.Text(), nullable=True),
        sa.Column("refunded_poisha", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("redirect_url", sa.Text(), nullable=True),
        sa.Column("return_url", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="api", nullable=False),
        sa.Column("checkout_session_id", sa.Text(), nullable=True),
        sa.Column("start_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('created', 'pending', 'paid', 'failed', 'cancelled', 'expired', 'needs_review', 'partially_refunded', 'refunded')",
            name=op.f("ck_orders_status"),
        ),
        sa.CheckConstraint("amount_poisha > 0", name=op.f("ck_orders_amount_positive")),
        sa.CheckConstraint(
            "refunded_poisha >= 0 AND refunded_poisha <= amount_poisha", name=op.f("ck_orders_refund_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["checkout_session_id"],
            ["checkout_sessions.id"],
            name=op.f("fk_orders_checkout_session_id_checkout_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_orders_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("workspace_id", "mode", "order_id", name="uq_orders_scope_order_id"),
    )
    op.create_index(
        "ix_orders_checkout_session",
        "orders",
        ["checkout_session_id"],
        unique=False,
        postgresql_where=sa.text("checkout_session_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_due_check",
        "orders",
        ["next_check_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending' AND next_check_at IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_provider_payment",
        "orders",
        ["workspace_id", "mode", "gateway", "provider_payment_id"],
        unique=False,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_provider_txn",
        "orders",
        ["workspace_id", "mode", "provider_txn_id"],
        unique=False,
        postgresql_where=sa.text("provider_txn_id IS NOT NULL"),
    )
    op.create_index("ix_orders_scope_recent", "orders", ["workspace_id", "mode", "id"], unique=False)
    op.create_index("ix_orders_scope_status", "orders", ["workspace_id", "mode", "status", "id"], unique=False)
    op.create_table(
        "events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("order_pk", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_pk"], ["orders.id"], name=op.f("fk_events_order_pk_orders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mode"],
            ["workspace_envs.workspace_id", "workspace_envs.mode"],
            name=op.f("fk_events_workspace_id_mode_workspace_envs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_order", "events", ["order_pk"], unique=False, postgresql_where=sa.text("order_pk IS NOT NULL")
    )
    op.create_index("ix_events_scope_recent", "events", ["workspace_id", "mode", "created_at"], unique=False)
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_pk", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=True),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["order_pk"], ["orders.id"], name=op.f("fk_order_events_order_pk_orders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index("ix_order_events_order", "order_events", ["order_pk", "id"], unique=False)
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("endpoint_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_response", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name=op.f("ck_webhook_deliveries_status")),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["webhook_endpoints.id"],
            name=op.f("fk_webhook_deliveries_endpoint_id_webhook_endpoints"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name=op.f("fk_webhook_deliveries_event_id_events"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_webhook_deliveries_endpoint", "webhook_deliveries", ["endpoint_id", "created_at"], unique=False)
    op.create_index(op.f("ix_webhook_deliveries_event_id"), "webhook_deliveries", ["event_id"], unique=False)
    op.create_index(
        "ix_webhook_deliveries_scope", "webhook_deliveries", ["workspace_id", "mode", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_scope", table_name="webhook_deliveries")
    op.drop_index(op.f("ix_webhook_deliveries_event_id"), table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint", table_name="webhook_deliveries")
    op.drop_index(
        "ix_webhook_deliveries_due", table_name="webhook_deliveries", postgresql_where=sa.text("status = 'pending'")
    )
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_order_events_order", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_events_scope_recent", table_name="events")
    op.drop_index("ix_events_order", table_name="events", postgresql_where=sa.text("order_pk IS NOT NULL"))
    op.drop_table("events")
    op.drop_index("ix_orders_scope_status", table_name="orders")
    op.drop_index("ix_orders_scope_recent", table_name="orders")
    op.drop_index(
        "ix_orders_provider_txn", table_name="orders", postgresql_where=sa.text("provider_txn_id IS NOT NULL")
    )
    op.drop_index(
        "ix_orders_provider_payment", table_name="orders", postgresql_where=sa.text("provider_payment_id IS NOT NULL")
    )
    op.drop_index(
        "ix_orders_due_check",
        table_name="orders",
        postgresql_where=sa.text("status = 'pending' AND next_check_at IS NOT NULL"),
    )
    op.drop_index(
        "ix_orders_checkout_session", table_name="orders", postgresql_where=sa.text("checkout_session_id IS NOT NULL")
    )
    op.drop_table("orders")
    op.drop_index("ix_checkout_sessions_scope", table_name="checkout_sessions")
    op.drop_index(
        "ix_checkout_sessions_reference",
        table_name="checkout_sessions",
        postgresql_where=sa.text("reference IS NOT NULL"),
    )
    op.drop_index(
        "ix_checkout_sessions_open_expiry", table_name="checkout_sessions", postgresql_where=sa.text("status = 'open'")
    )
    op.drop_table("checkout_sessions")
    op.drop_index("ix_webhook_endpoints_scope", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
    op.drop_table("simulator_payments")
    op.drop_index("ix_payment_links_scope", table_name="payment_links")
    op.drop_table("payment_links")
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_table("gateway_configs")
    op.drop_index("ix_api_keys_scope", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("workspace_envs")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_invitations_workspace_id"), table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_audit_log_workspace", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("workspaces")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_email_tokens_user_id"), table_name="email_tokens")
    op.drop_table("email_tokens")
    op.drop_table("worker_heartbeats")
    op.drop_table("users")
    op.drop_index(op.f("ix_provider_tokens_expires_at"), table_name="provider_tokens")
    op.drop_table("provider_tokens")
    op.drop_index(
        "ix_outbound_emails_due", table_name="outbound_emails", postgresql_where=sa.text("status = 'pending'")
    )
    op.drop_table("outbound_emails")
