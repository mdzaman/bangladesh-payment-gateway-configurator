"""Read side of orders: listings, search and dashboard statistics."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from bdpay.core import Status, fmt_amount

from ..context import Scope
from ..db import utcnow
from ..engine.orders import CloudOrder, order_json
from ..models import ORDER_STATUSES, Order

WAS_PAID = ("paid", "partially_refunded", "refunded")
FINISHED_UNPAID = ("failed", "cancelled", "expired")


def to_cloud(row: Order) -> CloudOrder:
    return CloudOrder(
        order_id=row.order_id,
        gateway=row.gateway,
        amount_poisha=row.amount_poisha,
        currency=row.currency,
        status=Status(row.status),
        customer=row.customer or {},
        provider_payment_id=row.provider_payment_id,
        provider_txn_id=row.provider_txn_id,
        refunded_poisha=row.refunded_poisha,
        created_at=row.created_at.timestamp(),
        updated_at=row.updated_at.timestamp(),
        pk=row.id,
        metadata=row.meta or {},
        return_url=row.return_url,
        redirect_url=row.redirect_url,
        source=row.source,
        checkout_session_id=row.checkout_session_id,
        paid_at=row.paid_at.timestamp() if row.paid_at else None,
        mode=row.mode,
    )


def _encode(pk: int) -> str:
    return base64.urlsafe_b64encode(str(pk).encode()).decode().rstrip("=")


def _decode(cursor: str) -> int | None:
    try:
        return int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError):
        return None


def list_orders(
    db: Session,
    scope: Scope,
    *,
    status: str | None = None,
    gateway: str | None = None,
    q: str | None = None,
    source: str | None = None,
    session_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    stmt = select(Order).where(Order.workspace_id == scope.workspace_id, Order.mode == scope.mode)
    if status:
        wanted = [s for s in status.split(",") if s in ORDER_STATUSES]
        stmt = stmt.where(Order.status.in_(wanted)) if wanted else stmt.where(text("false"))
    if gateway:
        stmt = stmt.where(Order.gateway == gateway)
    if source:
        stmt = stmt.where(Order.source == source)
    if session_id:
        stmt = stmt.where(Order.checkout_session_id == session_id)
    if created_from:
        stmt = stmt.where(Order.created_at >= created_from)
    if created_to:
        stmt = stmt.where(Order.created_at < created_to)
    if q:
        q = q.strip()[:80]
        prefix = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(
                Order.order_id.like(prefix, escape="\\"),
                Order.provider_txn_id == q,
                Order.provider_payment_id == q,
                Order.customer["phone"].astext == q,
                Order.customer["email"].astext == q.lower(),
            )
        )
    after = _decode(cursor) if cursor else None
    if after is not None:
        stmt = stmt.where(Order.id < after)
    rows = list(db.scalars(stmt.order_by(Order.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "data": [order_json(to_cloud(r)) for r in rows],
        "has_more": more,
        "next_cursor": _encode(rows[-1].id) if more and rows else None,
    }


def stats(db: Session, scope: Scope, days: int = 30) -> dict[str, Any]:
    """Figures for the overview page. Days are calendar days in Dhaka."""
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        text("""
        SELECT (created_at AT TIME ZONE 'Asia/Dhaka')::date AS day, status, gateway,
               count(*) AS n, COALESCE(sum(amount_poisha), 0) AS amount,
               COALESCE(sum(refunded_poisha), 0) AS refunded
          FROM orders
         WHERE workspace_id = :ws AND mode = :mode AND created_at >= :since
         GROUP BY 1, 2, 3"""),
        {**scope.params, "since": since},
    ).all()
    by_status = dict.fromkeys(ORDER_STATUSES, 0)
    by_gateway: dict[str, dict[str, int]] = {}
    daily: dict[str, dict[str, int]] = {}
    paid_count = paid_amount = refunded = unpaid = 0
    for r in rows:
        by_status[r.status] += r.n
        g = by_gateway.setdefault(r.gateway, {"orders": 0, "paid": 0, "paid_poisha": 0, "unpaid": 0})
        d = daily.setdefault(str(r.day), {"paid": 0, "paid_poisha": 0, "unpaid": 0, "open": 0})
        g["orders"] += r.n
        if r.status in WAS_PAID:
            paid_count += r.n
            paid_amount += r.amount
            refunded += r.refunded
            g["paid"] += r.n
            g["paid_poisha"] += r.amount
            d["paid"] += r.n
            d["paid_poisha"] += r.amount
        elif r.status in FINISHED_UNPAID:
            unpaid += r.n
            g["unpaid"] += r.n
            d["unpaid"] += r.n
        else:
            d["open"] += r.n
    first = (utcnow() + timedelta(hours=6)).date() - timedelta(days=days - 1)
    series = []
    for i in range(days):
        day = str(first + timedelta(days=i))
        d = daily.get(day, {"paid": 0, "paid_poisha": 0, "unpaid": 0, "open": 0})
        series.append({"day": day, **d, "paid_amount": fmt_amount(d["paid_poisha"])})
    finished = paid_count + unpaid
    live_now = db.execute(
        text("""
        SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
               count(*) FILTER (WHERE status = 'needs_review') AS needs_review
          FROM orders WHERE workspace_id = :ws AND mode = :mode AND status IN ('pending', 'needs_review')"""),
        scope.params,
    ).one()
    return {
        "days": days,
        "orders": sum(by_status.values()),
        "paid_count": paid_count,
        "paid_amount": fmt_amount(paid_amount),
        "paid_poisha": paid_amount,
        "refunded_amount": fmt_amount(refunded),
        "success_rate": round(paid_count / finished, 4) if finished else None,
        "by_status": by_status,
        "by_gateway": [
            {
                "gateway": k,
                **v,
                "paid_amount": fmt_amount(v["paid_poisha"]),
                "success_rate": round(v["paid"] / (v["paid"] + v["unpaid"]), 4) if v["paid"] + v["unpaid"] else None,
            }
            for k, v in sorted(by_gateway.items(), key=lambda kv: -kv[1]["paid_poisha"])
        ],
        "daily": series,
        "pending_now": live_now.pending,
        "needs_review_now": live_now.needs_review,
    }
