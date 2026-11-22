"""Request bodies. Unknown fields are rejected so a typo is an error, not a no-op."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Amount = str | int | float


# ------------------------------------------------------------------- accounts
class SignupIn(Body):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    workspace_name: str | None = Field(default=None, max_length=80)
    invite_token: str | None = Field(default=None, max_length=200)


class LoginIn(Body):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class TwoFactorLoginIn(Body):
    challenge: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=40)


class PasswordIn(Body):
    current_password: str = Field(max_length=200)


class CodeIn(Body):
    code: str = Field(min_length=1, max_length=40)


class PasswordCodeIn(Body):
    current_password: str = Field(max_length=200)
    code: str = Field(min_length=1, max_length=40)


class TokenIn(Body):
    token: str = Field(min_length=1, max_length=200)


class EmailIn(Body):
    email: str = Field(max_length=320)


class ResetIn(Body):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordIn(Body):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class ProfileIn(Body):
    name: str = Field(min_length=1, max_length=80)


# ----------------------------------------------------------------- workspaces
class WorkspaceIn(Body):
    name: str = Field(min_length=2, max_length=80)


class WorkspacePatch(Body):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    display_name: str | None = Field(default=None, max_length=80)
    support_email: str | None = Field(default=None, max_length=200)
    support_phone: str | None = Field(default=None, max_length=30)
    locale: str | None = Field(default=None, pattern="^(en|bn)$")
    # Owners only: members need two-factor sign-in before they can change anything in live mode.
    require_two_factor: bool | None = None


class GoLiveIn(Body):
    accept: bool
    legal_name: str = Field(min_length=2, max_length=120)


class InviteIn(Body):
    email: EmailStr
    role: str


class RoleIn(Body):
    role: str


# ------------------------------------------------------------------- gateways
class GatewaySaveIn(Body):
    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool | None = None
    clear: list[str] = Field(default_factory=list, max_length=20)


class GatewayPatchIn(Body):
    enabled: bool


class ReorderIn(Body):
    order: list[str] = Field(max_length=30)


class ChecklistIn(Body):
    id: str = Field(min_length=1, max_length=80)
    done: bool


class EnvSettingsIn(Body):
    result_url: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- developers
class ApiKeyIn(Body):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] | None = Field(default=None, max_length=10)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class WebhookIn(Body):
    url: str = Field(min_length=1, max_length=2000)
    description: str = Field(default="", max_length=200)
    event_types: list[str] | None = Field(default=None, max_length=20)


class WebhookPatch(Body):
    url: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=200)
    event_types: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None


# ------------------------------------------------------------------- payments
class OrderIn(Body):
    """Same fields as the self-hosted bdpay ``POST /bdpay/orders``, plus two optional ones."""

    order_id: str = Field(min_length=1, max_length=40, examples=["INV1001"])
    amount: Amount = Field(examples=["1250.00"])
    gateway: str = Field(min_length=1, max_length=40, examples=["bkash"])
    customer: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    return_url: str | None = Field(
        default=None, max_length=2000, examples=["https://shop.example/thanks?order={order_id}&status={status}"]
    )


class RefundIn(Body):
    amount: Amount | None = Field(default=None, description="Omit for a full refund", examples=["200.00"])
    reason: str = Field(default="customer request", max_length=250)


class ConsoleOrderIn(Body):
    """Test payment started from the dashboard."""

    gateway: str = Field(min_length=1, max_length=40)
    amount: Amount
    order_id: str | None = Field(default=None, max_length=40)
    customer: dict[str, Any] = Field(default_factory=dict)


class SessionIn(Body):
    amount: Amount = Field(examples=["1250.00"])
    reference: str | None = Field(default=None, max_length=100, examples=["INV1001"])
    description: str = Field(default="", max_length=200)
    customer: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    gateways: list[str] | None = Field(
        default=None, max_length=30, description="Limit the methods offered. Default: every enabled gateway."
    )
    success_url: str | None = Field(default=None, max_length=2000)
    cancel_url: str | None = Field(default=None, max_length=2000)
    locale: str | None = Field(default=None, pattern="^(en|bn)$")
    expires_in_minutes: int | None = Field(default=None, ge=5, le=10080)


class LinkIn(Body):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    amount: Amount | None = None
    min_amount: Amount | None = None
    max_amount: Amount | None = None
    gateways: list[str] | None = Field(default=None, max_length=30)
    collect: list[str] | None = Field(default=None, max_length=3)
    success_url: str | None = Field(default=None, max_length=2000)
    max_payments: int | None = Field(default=None, ge=1, le=1_000_000)


class LinkPatch(Body):
    active: bool


class SuspendIn(Body):
    reason: str = Field(default="", max_length=300)
