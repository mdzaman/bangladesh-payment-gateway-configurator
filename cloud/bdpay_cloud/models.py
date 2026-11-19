"""Database schema.

Tenancy model: a *workspace* is one business. Each workspace has two
*environments* (``sandbox`` and ``live``) with separate gateway credentials,
API keys, orders and webhooks, exactly like the ``BDPAY_ENV`` switch of the
reference engine. Every tenant-owned row carries ``(workspace_id, mode)``.

Money is integer poisha everywhere (1 BDT = 100 poisha).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bdpay.core import Status

MODES = ("sandbox", "live")
ROLES = ("owner", "admin", "developer", "analyst")  # strongest first
ORDER_STATUSES = tuple(s.value for s in Status)

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: DateTime(timezone=True), str: Text}


TS = DateTime(timezone=True)
NOW = func.now()
EMPTY_JSON = text("'{}'::jsonb")
EMPTY_ARRAY = text("'{}'::text[]")


def _env_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", "mode"], ["workspace_envs.workspace_id", "workspace_envs.mode"], ondelete="CASCADE"
    )


# --------------------------------------------------------------------- people
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, server_default="")
    password_hash: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(TS)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    failed_logins: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    disabled_at: Mapped[datetime | None] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lower"),)


class UserSession(Base):
    """Dashboard login. ``id`` is the SHA-256 of the cookie value, never the value."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    last_seen_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)


class EmailToken(Base):
    """Single-use links (verify address, reset password). ``id`` is the token hash."""

    __tablename__ = "email_tokens"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint(_in("purpose", ("verify_email", "reset_password")), name="purpose"),)


# ------------------------------------------------------------------- tenancy
class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    # display_name, support_email, support_phone, locale ... shown on hosted pages
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    live_status: Mapped[str] = mapped_column(Text, server_default="inactive")
    live_requested_at: Mapped[datetime | None] = mapped_column(TS)
    live_activated_at: Mapped[datetime | None] = mapped_column(TS)
    # who agreed to the non-custodial attestation, when, from where, which text
    live_attestation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    suspended_at: Mapped[datetime | None] = mapped_column(TS)
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint(_in("live_status", ("inactive", "pending", "active")), name="live_status"),)


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    invited_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(TS)
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role"),)


class WorkspaceEnv(Base):
    """One of the two environments of a workspace."""

    __tablename__ = "workspace_envs"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    # Appears in the callback and IPN URLs given to providers: /p/<public_key>/...
    public_key: Mapped[str] = mapped_column(Text, unique=True)
    # HMAC secret for signed callback links (rule 3 of the build spec), encrypted.
    callback_secret_enc: Mapped[str] = mapped_column(Text)
    # Where customers land after paying when an order sets no return_url.
    result_url: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (CheckConstraint(_in("mode", MODES), name="mode"),)


class GatewayConfig(Base):
    """A workspace's own credentials for one gateway in one environment.
    Secret values live only in ``secrets_enc``; ``secret_hints`` says which
    ones are set without revealing them."""

    __tablename__ = "gateway_configs"
    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    gateway: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    secrets_enc: Mapped[str | None] = mapped_column(Text)
    secret_hints: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="unverified")
    last_test_at: Mapped[datetime | None] = mapped_column(TS)
    last_test_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_success_at: Mapped[datetime | None] = mapped_column(TS)
    last_error_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (
        _env_fk(),
        CheckConstraint(_in("status", ("unverified", "verified", "rejected")), name="status"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    display: Mapped[str] = mapped_column(Text)  # "bdp_test_AbCd…wxyz"
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=EMPTY_ARRAY)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (_env_fk(), Index("ix_api_keys_scope", "workspace_id", "mode"))


# ------------------------------------------------------------------- payments
class PaymentLink(Base):
    __tablename__ = "payment_links"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # public, unguessable
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    amount_poisha: Mapped[int | None] = mapped_column(BigInteger)  # NULL: the customer types it
    min_poisha: Mapped[int | None] = mapped_column(BigInteger)
    max_poisha: Mapped[int | None] = mapped_column(BigInteger)
    allowed_gateways: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=EMPTY_ARRAY)
    collect: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=EMPTY_ARRAY)  # name, phone, email
    success_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    max_payments: Mapped[int | None] = mapped_column(Integer)
    paid_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (
        _env_fk(),
        Index("ix_payment_links_scope", "workspace_id", "mode", "created_at"),
        CheckConstraint("amount_poisha IS NULL OR amount_poisha > 0", name="amount_positive"),
    )


class CheckoutSession(Base):
    """A hosted checkout page: fixed amount, the customer picks the gateway.
    Each try is its own order (one payment attempt per order id)."""

    __tablename__ = "checkout_sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # public, unguessable
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    amount_poisha: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text, server_default="BDT")
    reference: Mapped[str | None] = mapped_column(Text)  # the merchant's own id
    description: Mapped[str] = mapped_column(Text, server_default="")
    customer: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_JSON)
    allowed_gateways: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=EMPTY_ARRAY)
    success_url: Mapped[str | None] = mapped_column(Text)
    cancel_url: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    paid_order_id: Mapped[str | None] = mapped_column(Text)
    payment_link_id: Mapped[str | None] = mapped_column(ForeignKey("payment_links.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        _env_fk(),
        Index("ix_checkout_sessions_scope", "workspace_id", "mode", "created_at"),
        Index(
            "ix_checkout_sessions_reference",
            "workspace_id",
            "mode",
            "reference",
            postgresql_where=text("reference IS NOT NULL"),
        ),
        Index("ix_checkout_sessions_open_expiry", "expires_at", postgresql_where=text("status = 'open'")),
        CheckConstraint("amount_poisha > 0", name="amount_positive"),
        CheckConstraint(_in("status", ("open", "paid", "expired", "cancelled")), name="status"),
    )


class Order(Base):
    """The engine's order record, per workspace environment. ``order_id`` is the
    merchant's id and is what the provider sees; the amount stored here is the
    only amount ever accepted as paid."""

    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    order_id: Mapped[str] = mapped_column(Text(collation="C"))
    gateway: Mapped[str] = mapped_column(Text)
    amount_poisha: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text, server_default="BDT")
    status: Mapped[str] = mapped_column(Text)
    customer: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_JSON)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    provider_txn_id: Mapped[str | None] = mapped_column(Text)
    refunded_poisha: Mapped[int] = mapped_column(BigInteger, server_default="0")
    redirect_url: Mapped[str | None] = mapped_column(Text)
    return_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="api")
    checkout_session_id: Mapped[str | None] = mapped_column(ForeignKey("checkout_sessions.id", ondelete="SET NULL"))
    start_claimed_at: Mapped[datetime | None] = mapped_column(TS)
    next_check_at: Mapped[datetime | None] = mapped_column(TS)
    check_count: Mapped[int] = mapped_column(Integer, server_default="0")
    paid_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (
        _env_fk(),
        UniqueConstraint("workspace_id", "mode", "order_id", name="uq_orders_scope_order_id"),
        CheckConstraint("amount_poisha > 0", name="amount_positive"),
        CheckConstraint("refunded_poisha >= 0 AND refunded_poisha <= amount_poisha", name="refund_bounds"),
        CheckConstraint(_in("status", ORDER_STATUSES), name="status"),
        Index("ix_orders_scope_recent", "workspace_id", "mode", "id"),
        Index("ix_orders_scope_status", "workspace_id", "mode", "status", "id"),
        Index(
            "ix_orders_provider_payment",
            "workspace_id",
            "mode",
            "gateway",
            "provider_payment_id",
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index(
            "ix_orders_provider_txn",
            "workspace_id",
            "mode",
            "provider_txn_id",
            postgresql_where=text("provider_txn_id IS NOT NULL"),
        ),
        Index(
            "ix_orders_due_check",
            "next_check_at",
            postgresql_where=text("status = 'pending' AND next_check_at IS NOT NULL"),
        ),
        Index(
            "ix_orders_checkout_session",
            "checkout_session_id",
            postgresql_where=text("checkout_session_id IS NOT NULL"),
        ),
    )


class OrderEvent(Base):
    """Append-only audit trail of an order (rule 10 of the build spec)."""

    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_pk: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    kind: Mapped[str] = mapped_column(Text)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    __table_args__ = (Index("ix_order_events_order", "order_pk", "id"),)


class ProviderToken(Base):
    """Cached provider access tokens (bKash, shurjoPay, EPS, Upay), encrypted."""

    __tablename__ = "provider_tokens"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS, index=True)


class SimulatorPayment(Base):
    """State of the built-in fake provider. Sandbox only."""

    __tablename__ = "simulator_payments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    order_ref: Mapped[str] = mapped_column(Text)
    amount_poisha: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="created")
    paid_poisha: Mapped[int | None] = mapped_column(BigInteger)
    txn_id: Mapped[str | None] = mapped_column(Text)
    refunded_poisha: Mapped[int] = mapped_column(BigInteger, server_default="0")
    return_urls: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (
        _env_fk(),
        CheckConstraint("mode = 'sandbox'", name="sandbox_only"),
        CheckConstraint(_in("status", ("created", "completed", "failed", "cancelled")), name="status"),
    )


# ------------------------------------------------------------------- webhooks
class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    secret_enc: Mapped[str] = mapped_column(Text)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{*}'::text[]"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(TS)
    last_failure_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (_env_fk(), Index("ix_webhook_endpoints_scope", "workspace_id", "mode"))


class Event(Base):
    """Something that happened to a tenant's payment. Written in the same
    transaction as the state change it describes (transactional outbox), so a
    change and its event either both exist or neither does."""

    __tablename__ = "events"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    order_pk: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    __table_args__ = (
        _env_fk(),
        Index("ix_events_scope_recent", "workspace_id", "mode", "created_at"),
        Index("ix_events_order", "order_pk", postgresql_where=text("order_pk IS NOT NULL")),
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"))
    workspace_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(TS, server_default=NOW)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TS)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_response: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    delivered_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        CheckConstraint(_in("status", ("pending", "succeeded", "failed")), name="status"),
        Index("ix_webhook_deliveries_due", "next_attempt_at", postgresql_where=text("status = 'pending'")),
        Index("ix_webhook_deliveries_endpoint", "endpoint_id", "created_at"),
        Index("ix_webhook_deliveries_scope", "workspace_id", "mode", "created_at"),
    )


# ------------------------------------------------------------------ operations
class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (_env_fk(),)


class AuditLog(Base):
    """Who changed what in a workspace. Never contains secret values."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    mode: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(Text)  # user | api_key | system
    actor_id: Mapped[str | None] = mapped_column(Text)
    actor_label: Mapped[str] = mapped_column(Text, server_default="")
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    __table_args__ = (Index("ix_audit_log_workspace", "workspace_id", "id"),)


class OutboundEmail(Base):
    __tablename__ = "outbound_emails"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    to_email: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(TS, server_default=NOW)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        CheckConstraint(_in("status", ("pending", "sent", "failed")), name="status"),
        Index("ix_outbound_emails_due", "next_attempt_at", postgresql_where=text("status = 'pending'")),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_beat: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
