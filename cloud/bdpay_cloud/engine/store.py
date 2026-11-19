"""PostgreSQL order store for one workspace environment.

Drop-in for ``bdpay.store.Store`` (same method names, same semantics), which
is how the reference ``BdPay`` service object runs unchanged on top of it:

* idempotent ``create_order`` (same id + gateway + amount returns the order,
  anything else raises ``DUPLICATE``);
* forward-only ``transition`` under a row lock, so duplicate callbacks and IPNs
  are harmless even when they land on different replicas at the same instant;
* an append-only audit trail with secrets and phone numbers masked.

On top of the reference it adds what a hosted service needs: every method is
scoped to a tenant, a real state change writes its webhook event in the same
transaction (see ``services.events``), and a pending order is scheduled for
reconciliation.

Each method is one short transaction. Provider HTTP calls always happen
between store calls, never inside one. An instance is cheap and not shared
between threads: build one per request or job.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, text

from bdpay.core import ALLOWED, BdPayError, Order, Status
from bdpay.http import redact

from ..context import Scope
from ..db import try_xact_lock
from ..services import events as outbox
from .orders import CloudOrder, iso, order_json

_COLUMNS = """
    id, order_id, gateway, amount_poisha, currency, status, customer, metadata, provider_payment_id,
    provider_txn_id, refunded_poisha, redirect_url, return_url, source, checkout_session_id,
    extract(epoch FROM created_at)::float8 AS created_ts,
    extract(epoch FROM updated_at)::float8 AS updated_ts,
    extract(epoch FROM paid_at)::float8 AS paid_ts
"""
_SCOPE = "workspace_id = :ws AND mode = :mode"

# Seconds until the first status check of a pending order. Short, so a customer
# who paid and then lost the redirect is confirmed quickly.
FIRST_CHECK_SECONDS = 120


def clean_detail(detail: dict | None) -> str:
    """Audit detail as JSON text: secrets and phone numbers masked, size capped."""
    s = json.dumps(redact(detail or {}), default=str)
    if len(s) > 20000:
        s = json.dumps({"truncated": True, "preview": s[:8000]})
    return s


class PgStore:
    def __init__(self, engine: Engine, scope: Scope, public_url: str = ""):
        self.engine = engine
        self.scope = scope
        self.public_url = public_url
        self._extras: dict[str, Any] | None = None

    # ------------------------------------------------------------------ rows
    def _row(self, r) -> CloudOrder:
        return CloudOrder(
            order_id=r.order_id,
            gateway=r.gateway,
            amount_poisha=r.amount_poisha,
            currency=r.currency,
            status=Status(r.status),
            customer=r.customer or {},
            provider_payment_id=r.provider_payment_id,
            provider_txn_id=r.provider_txn_id,
            refunded_poisha=r.refunded_poisha,
            created_at=r.created_ts,
            updated_at=r.updated_ts,
            pk=r.id,
            metadata=r.metadata or {},
            return_url=r.return_url,
            redirect_url=r.redirect_url,
            source=r.source,
            checkout_session_id=r.checkout_session_id,
            paid_at=r.paid_ts,
            mode=self.scope.mode,
        )

    def _get(self, conn: Connection, order_id: str, lock: bool = False) -> CloudOrder | None:
        r = conn.execute(
            text(f"SELECT {_COLUMNS} FROM orders WHERE {_SCOPE} AND order_id = :oid" + (" FOR UPDATE" if lock else "")),
            {**self.scope.params, "oid": order_id},
        ).one_or_none()
        return self._row(r) if r else None

    def _event(
        self,
        conn: Connection,
        order_pk: int,
        kind: str,
        from_status: str | None,
        to_status: str | None,
        detail: dict | None,
    ) -> None:
        conn.execute(
            text("""
            INSERT INTO order_events (order_pk, workspace_id, mode, kind, from_status, to_status, detail)
            VALUES (:pk, :ws, :mode, :kind, :f, :t, CAST(:detail AS jsonb))"""),
            {
                "pk": order_pk,
                **self.scope.params,
                "kind": kind,
                "f": from_status,
                "t": to_status,
                "detail": clean_detail(detail),
            },
        )

    # ----------------------------------------------- reference Store interface
    @contextmanager
    def creating(self, **extras: Any) -> Iterator[None]:
        """Fields the reference ``create_order`` does not know about (metadata,
        return_url, source, checkout session) for the order created inside."""
        self._extras = extras
        try:
            yield
        finally:
            self._extras = None

    def create_order(self, order: Order) -> CloudOrder:
        x = self._extras or {}
        with self.engine.begin() as conn:
            pk = conn.execute(
                text("""
                INSERT INTO orders (workspace_id, mode, order_id, gateway, amount_poisha, currency, status,
                                    customer, metadata, return_url, source, checkout_session_id)
                VALUES (:ws, :mode, :oid, :gateway, :amount, :currency, 'created',
                        CAST(:customer AS jsonb), CAST(:metadata AS jsonb), :return_url, :source, :session)
                ON CONFLICT (workspace_id, mode, order_id) DO NOTHING
                RETURNING id"""),
                {
                    **self.scope.params,
                    "oid": order.order_id,
                    "gateway": order.gateway,
                    "amount": order.amount_poisha,
                    "currency": order.currency,
                    "customer": json.dumps(order.customer or {}),
                    "metadata": json.dumps(x.get("metadata") or {}),
                    "return_url": x.get("return_url"),
                    "source": x.get("source") or "api",
                    "session": x.get("checkout_session_id"),
                },
            ).scalar()
            if pk is None:
                existing = self._get(conn, order.order_id)
                if existing and (existing.gateway, existing.amount_poisha, existing.currency) == (
                    order.gateway,
                    order.amount_poisha,
                    order.currency,
                ):
                    return existing
                raise BdPayError("DUPLICATE", f"order {order.order_id} already exists with different details")
            self._event(conn, pk, "created", None, Status.CREATED.value, {"amount_poisha": order.amount_poisha})
            created = self._get(conn, order.order_id)
            assert created is not None
            return created

    def get(self, order_id: str) -> CloudOrder | None:
        with self.engine.connect() as conn:
            return self._get(conn, order_id)

    def find_by_provider_id(self, gateway: str, provider_payment_id: str) -> CloudOrder | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM orders WHERE {_SCOPE} AND gateway = :g "
                    "AND provider_payment_id = :pid ORDER BY id DESC LIMIT 1"
                ),
                {**self.scope.params, "g": gateway, "pid": provider_payment_id},
            ).one_or_none()
            return self._row(r) if r else None

    def set_provider_ids(self, order_id: str, payment_id: str | None = None, txn_id: str | None = None) -> None:
        if not payment_id and not txn_id:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(f"""
                UPDATE orders SET provider_payment_id = COALESCE(:pid, provider_payment_id),
                                  provider_txn_id = COALESCE(:txn, provider_txn_id), updated_at = now()
                 WHERE {_SCOPE} AND order_id = :oid"""),
                {**self.scope.params, "oid": order_id, "pid": payment_id or None, "txn": txn_id or None},
            )

    def transition(
        self, order_id: str, to: Status, detail: dict | None = None, refunded_poisha: int | None = None
    ) -> tuple[CloudOrder, bool]:
        """Move an order forward. Returns (order, changed). Backward or repeated
        moves are no-ops. The row lock makes this safe across replicas."""
        with self.engine.begin() as conn:
            cur = self._get(conn, order_id, lock=True)
            if cur is None:
                raise BdPayError("NOT_FOUND", f"order {order_id} not found")
            if to == cur.status and to != Status.PARTIALLY_REFUNDED:
                return cur, False
            if to not in ALLOWED[cur.status]:
                self._event(conn, cur.pk, "ignored_transition", cur.status.value, to.value, detail)
                return cur, False
            conn.execute(
                text("""
                UPDATE orders
                   SET status = :to, updated_at = now(),
                       refunded_poisha = COALESCE(:refunded, refunded_poisha),
                       paid_at = CASE WHEN :to = 'paid' THEN now() ELSE paid_at END,
                       next_check_at = CASE WHEN :to = 'pending'
                                            THEN now() + make_interval(secs => :first_check) END,
                       check_count = CASE WHEN :to = 'pending' THEN 0 ELSE check_count END
                 WHERE id = :pk"""),
                {"to": to.value, "refunded": refunded_poisha, "first_check": FIRST_CHECK_SECONDS, "pk": cur.pk},
            )
            self._event(conn, cur.pk, "transition", cur.status.value, to.value, detail)
            after = self._get(conn, order_id)
            assert after is not None
            self._after_transition(conn, after, cur.status, detail)
            return after, True

    def stale(self, older_than_seconds: float, statuses=(Status.PENDING,)) -> list[CloudOrder]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                SELECT {_COLUMNS} FROM orders
                 WHERE {_SCOPE} AND status = ANY(:statuses)
                   AND updated_at < now() - make_interval(secs => :age)
                 ORDER BY id"""),
                {**self.scope.params, "statuses": [s.value for s in statuses], "age": float(older_than_seconds)},
            ).all()
            return [self._row(r) for r in rows]

    def event(
        self, order_id: str, kind: str, from_status: str | None, to_status: str | None, detail: dict | None = None
    ) -> None:
        with self.engine.begin() as conn:
            pk = conn.execute(
                text(f"SELECT id FROM orders WHERE {_SCOPE} AND order_id = :oid"),
                {**self.scope.params, "oid": order_id},
            ).scalar()
            if pk is not None:
                self._event(conn, pk, kind, from_status, to_status, detail)

    def events(self, order_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                SELECT extract(epoch FROM e.at)::float8 AS at, e.kind, e.from_status, e.to_status, e.detail
                  FROM order_events e JOIN orders o ON o.id = e.order_pk
                 WHERE o.workspace_id = :ws AND o.mode = :mode AND o.order_id = :oid
                 ORDER BY e.id"""),
                {**self.scope.params, "oid": order_id},
            ).all()
            return [
                {
                    "at": r.at,
                    "kind": r.kind,
                    "from_status": r.from_status,
                    "to_status": r.to_status,
                    "detail": r.detail or {},
                }
                for r in rows
            ]

    # ------------------------------------------------- beyond the reference
    def claim_start(self, order_id: str) -> bool:
        """Take the right to open the provider session for a ``created`` order.
        At most one caller wins; a claim abandoned by a crash frees itself."""
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    text(f"""
                UPDATE orders SET start_claimed_at = now()
                 WHERE {_SCOPE} AND order_id = :oid AND status = 'created'
                   AND (start_claimed_at IS NULL OR start_claimed_at < now() - interval '2 minutes')
                RETURNING id"""),
                    {**self.scope.params, "oid": order_id},
                ).scalar()
                is not None
            )

    def release_start(self, order_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE orders SET start_claimed_at = NULL WHERE {_SCOPE} AND order_id = :oid "
                    "AND status = 'created'"
                ),
                {**self.scope.params, "oid": order_id},
            )

    def save_start(self, order_id: str, payment_id: str | None, redirect_url: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(f"""
                UPDATE orders SET provider_payment_id = COALESCE(:pid, provider_payment_id),
                                  redirect_url = :url, updated_at = now()
                 WHERE {_SCOPE} AND order_id = :oid"""),
                {**self.scope.params, "oid": order_id, "pid": payment_id or None, "url": redirect_url},
            )

    @contextmanager
    def exclusive(self, order_id: str, what: str) -> Iterator[None]:
        """Hold a per-order lock for the body (used so two refunds of one order
        cannot overlap). The lock lives in an open transaction, so it also works
        through PgBouncer and is released if this process dies."""
        with self.engine.begin() as conn:
            if not try_xact_lock(conn, "order", self.scope.workspace_id, self.scope.mode, order_id, what):
                raise BdPayError("DUPLICATE", f"another {what} for order {order_id} is in progress")
            yield

    # ----------------------------------------------------------- side effects
    def _after_transition(self, conn: Connection, order: CloudOrder, before: Status, detail: dict | None) -> None:
        etype = outbox.ORDER_STATUS_EVENTS.get(order.status.value)
        if etype:
            data: dict[str, Any] = {"order": order_json(order), "previous_status": before.value}
            if order.status in (Status.PARTIALLY_REFUNDED, Status.REFUNDED) and detail:
                data["refund"] = {"ref": detail.get("refund_ref")}
            if order.status == Status.NEEDS_REVIEW and detail:
                data["problems"] = detail.get("problems") or []
            outbox.emit(conn, self.scope, etype, data, order_pk=order.pk)
        if order.status == Status.PAID and order.checkout_session_id:
            self._complete_session(conn, order)

    def _complete_session(self, conn: Connection, order: CloudOrder) -> None:
        r = conn.execute(
            text(f"""
            UPDATE checkout_sessions SET status = 'paid', paid_order_id = :oid, completed_at = now()
             WHERE id = :sid AND {_SCOPE} AND status <> 'paid'
            RETURNING id, reference, amount_poisha, currency, description, metadata, payment_link_id,
                      status, paid_order_id, created_at, completed_at"""),
            {**self.scope.params, "sid": order.checkout_session_id, "oid": order.order_id},
        ).one_or_none()
        if r is None:
            # A second attempt of an already-paid session was also paid (the customer
            # paid twice). Nothing is lost: the merchant is told so they can refund it.
            outbox.emit(
                conn,
                self.scope,
                "checkout.session.extra_payment",
                {"session_id": order.checkout_session_id, "order": order_json(order)},
                order_pk=order.pk,
            )
            return
        if r.payment_link_id:
            conn.execute(
                text("UPDATE payment_links SET paid_count = paid_count + 1, updated_at = now() WHERE id = :id"),
                {"id": r.payment_link_id},
            )
        outbox.emit(
            conn,
            self.scope,
            "checkout.session.completed",
            {
                "session": {
                    "id": r.id,
                    "object": "checkout.session",
                    "reference": r.reference,
                    "amount_poisha": r.amount_poisha,
                    "currency": r.currency,
                    "description": r.description,
                    "metadata": r.metadata or {},
                    "payment_link_id": r.payment_link_id,
                    "status": r.status,
                    "paid_order_id": r.paid_order_id,
                    "created_at": iso(r.created_at),
                    "completed_at": iso(r.completed_at),
                },
                "order": order_json(order),
            },
            order_pk=order.pk,
        )
