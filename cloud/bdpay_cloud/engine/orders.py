"""The order record as the service sees it, and its one JSON shape.

``CloudOrder`` extends the reference ``Order`` with the fields a hosted service
needs. The same ``order_json`` is used for API responses and webhook payloads,
so what a merchant reads from the API is exactly what a webhook delivers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bdpay.core import Order, Status, fmt_amount


@dataclass
class CloudOrder(Order):
    pk: int = 0
    metadata: dict = field(default_factory=dict)
    return_url: str | None = None
    redirect_url: str | None = None
    source: str = "api"
    checkout_session_id: str | None = None
    paid_at: float | None = None
    mode: str = "sandbox"


def iso(ts: float | datetime | None) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def order_json(o: Order) -> dict[str, Any]:
    """Superset of the reference ``order_json``: the first eight keys are
    identical, so clients written for self-hosted bdpay keep working."""
    out: dict[str, Any] = {
        "order_id": o.order_id,
        "gateway": o.gateway,
        "amount": o.amount,
        "currency": o.currency,
        "status": o.status.value,
        "provider_payment_id": o.provider_payment_id,
        "provider_txn_id": o.provider_txn_id,
        "refunded": fmt_amount(o.refunded_poisha),
    }
    if isinstance(o, CloudOrder):
        out.update(
            {
                "object": "order",
                "mode": o.mode,
                "amount_poisha": o.amount_poisha,
                "refunded_poisha": o.refunded_poisha,
                "customer": o.customer,
                "metadata": o.metadata,
                "source": o.source,
                "checkout_session_id": o.checkout_session_id,
                "return_url": o.return_url,
                "created_at": iso(o.created_at),
                "updated_at": iso(o.updated_at),
                "paid_at": iso(o.paid_at),
            }
        )
    return out


SETTLED = (Status.PAID, Status.REFUNDED, Status.PARTIALLY_REFUNDED, Status.NEEDS_REVIEW)
