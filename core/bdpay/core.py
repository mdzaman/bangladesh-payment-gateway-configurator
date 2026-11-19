"""Core types shared by every gateway driver.

Money is always held as integer poisha (1 BDT = 100 poisha). Amounts are only
formatted as "100.00" strings at the edge, when talking to a provider.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Optional


class BdPayError(Exception):
    """Every error raised by bdpay. `code` is stable and safe to branch on."""

    CODES = {
        "CONFIG", "AUTH", "VALIDATION", "AMOUNT_MISMATCH", "SIGNATURE_INVALID",
        "PROVIDER_DECLINED", "PROVIDER_UNAVAILABLE", "TIMEOUT", "DUPLICATE",
        "NOT_SUPPORTED", "NOT_FOUND",
    }

    def __init__(self, code: str, message: str, provider: str = "core",
                 provider_code: Optional[str] = None, retryable: bool = False):
        assert code in self.CODES, code
        super().__init__(f"[{provider}:{code}] {message}")
        self.code = code
        self.message = message
        self.provider = provider
        self.provider_code = provider_code
        self.retryable = retryable


def to_poisha(amount: Any) -> int:
    """Convert '100', 100, 100.5, '100.50' or Decimal to integer poisha."""
    try:
        d = Decimal(str(amount).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise BdPayError("VALIDATION", f"invalid amount: {amount!r}")
    if not d.is_finite():
        raise BdPayError("VALIDATION", f"invalid amount: {amount!r}")
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(q * 100)


def fmt_amount(poisha: int) -> str:
    """Integer poisha -> provider string, e.g. 10050 -> '100.50'."""
    sign = "-" if poisha < 0 else ""
    p = abs(int(poisha))
    return f"{sign}{p // 100}.{p % 100:02d}"


class Status(str, enum.Enum):
    CREATED = "created"
    PENDING = "pending"              # customer sent to provider, not yet verified
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NEEDS_REVIEW = "needs_review"    # provider says paid but amount/order did not match
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


# Only forward moves are allowed. Anything else is ignored (idempotent).
ALLOWED = {
    Status.CREATED: {Status.PENDING, Status.PAID, Status.FAILED, Status.CANCELLED,
                     Status.EXPIRED, Status.NEEDS_REVIEW},
    Status.PENDING: {Status.PAID, Status.FAILED, Status.CANCELLED, Status.EXPIRED,
                     Status.NEEDS_REVIEW},
    # A late "paid" verification can still rescue a failed/cancelled/expired order.
    Status.FAILED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.CANCELLED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.EXPIRED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.PAID: {Status.PARTIALLY_REFUNDED, Status.REFUNDED},
    Status.PARTIALLY_REFUNDED: {Status.PARTIALLY_REFUNDED, Status.REFUNDED},
    Status.NEEDS_REVIEW: set(),
    Status.REFUNDED: set(),
}


@dataclass
class Capabilities:
    refunds: str = "none"          # none | full | partial
    recurring: bool = False
    payouts: bool = False
    webhooks: str = "requery"      # signed | requery | none


@dataclass
class Order:
    order_id: str
    gateway: str
    amount_poisha: int
    currency: str = "BDT"
    status: Status = Status.CREATED
    customer: dict = field(default_factory=dict)
    provider_payment_id: Optional[str] = None
    provider_txn_id: Optional[str] = None
    refunded_poisha: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def amount(self) -> str:
        return fmt_amount(self.amount_poisha)


@dataclass
class CallbackUrls:
    success: str
    fail: str
    cancel: str
    ipn: str


@dataclass
class PaymentSession:
    provider_payment_id: Optional[str]
    redirect_url: str
    raw: Any = None


@dataclass
class Verified:
    """The provider's server-side answer about one payment."""
    status: Status
    amount_poisha: Optional[int] = None
    currency: Optional[str] = None
    order_ref: Optional[str] = None
    provider_payment_id: Optional[str] = None
    provider_txn_id: Optional[str] = None
    raw: Any = None


@dataclass
class RefundResult:
    ok: bool
    refund_ref: Optional[str] = None
    status: str = "requested"      # requested | processing | completed | failed
    raw: Any = None


class Driver(ABC):
    """Base class for a gateway driver. Subclasses never trust browser data:
    `verify_return` must always confirm the payment server-to-server."""

    id: str = ""
    label: str = ""
    stability: str = "stable"      # stable | beta | experimental
    capabilities = Capabilities()
    required_env: tuple = ()

    def __init__(self, cfg: "GatewayConfig", http: "Http", tokens: "TokenCache"):
        self.cfg = cfg
        self.http = http
        self.tokens = tokens
        self.env = cfg.environment
        cfg.require(*self.required_env)

    @abstractmethod
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession: ...

    @abstractmethod
    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified: ...

    @abstractmethod
    def query(self, order: Order) -> Verified: ...

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        raise BdPayError("NOT_SUPPORTED", "refunds are done in the provider panel", self.id)

    def ipn_lookup(self, params: Mapping[str, str]) -> Optional[tuple]:
        """Return ('order_id', value) or ('provider_payment_id', value) from an IPN body."""
        return None

    def fail(self, code: str, message: str, provider_code: Optional[str] = None,
             retryable: bool = False) -> BdPayError:
        return BdPayError(code, message, self.id, provider_code, retryable)


# Imported late to avoid circular imports in type hints.
from .config import GatewayConfig  # noqa: E402
from .http import Http  # noqa: E402
from .store import TokenCache  # noqa: E402
