"""SQLite store: orders (idempotent state machine), audit events, token cache.

SQLite is the zero-setup default. For several app servers behind a load
balancer, point BDPAY_DB at a shared volume or swap this class for Postgres +
Redis using the same method names.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Callable, Optional, Tuple

from .core import ALLOWED, BdPayError, Order, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  gateway TEXT NOT NULL,
  amount_poisha INTEGER NOT NULL CHECK (amount_poisha > 0),
  currency TEXT NOT NULL DEFAULT 'BDT',
  status TEXT NOT NULL,
  customer TEXT NOT NULL DEFAULT '{}',
  provider_payment_id TEXT,
  provider_txn_id TEXT,
  refunded_poisha INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_provider_payment ON orders(gateway, provider_payment_id);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status, updated_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL,
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str = "bdpay.sqlite3"):
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)

    # ---------- orders ----------
    def _row(self, r: sqlite3.Row) -> Order:
        return Order(
            order_id=r["order_id"], gateway=r["gateway"], amount_poisha=r["amount_poisha"],
            currency=r["currency"], status=Status(r["status"]), customer=json.loads(r["customer"]),
            provider_payment_id=r["provider_payment_id"], provider_txn_id=r["provider_txn_id"],
            refunded_poisha=r["refunded_poisha"], created_at=r["created_at"], updated_at=r["updated_at"],
        )

    def create_order(self, order: Order) -> Order:
        """Insert, or return the existing order if the same id, gateway and amount
        were already created (safe to retry). A conflicting duplicate raises."""
        now = time.time()
        with self._lock:
            existing = self.get(order.order_id)
            if existing:
                if (existing.gateway, existing.amount_poisha, existing.currency) == \
                        (order.gateway, order.amount_poisha, order.currency):
                    return existing
                raise BdPayError("DUPLICATE", f"order {order.order_id} already exists with different details")
            self.db.execute(
                "INSERT INTO orders (order_id, gateway, amount_poisha, currency, status, customer,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (order.order_id, order.gateway, order.amount_poisha, order.currency,
                 Status.CREATED.value, json.dumps(order.customer), now, now))
            self.event(order.order_id, "created", None, Status.CREATED.value, {"amount_poisha": order.amount_poisha})
            return self.get(order.order_id)  # type: ignore[return-value]

    def get(self, order_id: str) -> Optional[Order]:
        r = self.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return self._row(r) if r else None

    def find_by_provider_id(self, gateway: str, provider_payment_id: str) -> Optional[Order]:
        r = self.db.execute("SELECT * FROM orders WHERE gateway=? AND provider_payment_id=?",
                            (gateway, provider_payment_id)).fetchone()
        return self._row(r) if r else None

    def set_provider_ids(self, order_id: str, payment_id: Optional[str] = None,
                         txn_id: Optional[str] = None) -> None:
        with self._lock:
            if payment_id:
                self.db.execute("UPDATE orders SET provider_payment_id=?, updated_at=? WHERE order_id=?",
                                (payment_id, time.time(), order_id))
            if txn_id:
                self.db.execute("UPDATE orders SET provider_txn_id=?, updated_at=? WHERE order_id=?",
                                (txn_id, time.time(), order_id))

    def transition(self, order_id: str, to: Status, detail: Optional[dict] = None,
                   refunded_poisha: Optional[int] = None) -> Tuple[Order, bool]:
        """Move an order forward. Returns (order, changed). Backward or repeated
        moves are no-ops, which makes duplicate callbacks and IPNs harmless."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = self.get(order_id)
                if cur is None:
                    raise BdPayError("NOT_FOUND", f"order {order_id} not found")
                if to == cur.status and to != Status.PARTIALLY_REFUNDED:
                    self.db.execute("COMMIT")
                    return cur, False
                if to not in ALLOWED[cur.status]:
                    self.event(order_id, "ignored_transition", cur.status.value, to.value, detail)
                    self.db.execute("COMMIT")
                    return cur, False
                if refunded_poisha is not None:
                    self.db.execute("UPDATE orders SET status=?, refunded_poisha=?, updated_at=? WHERE order_id=?",
                                    (to.value, refunded_poisha, time.time(), order_id))
                else:
                    self.db.execute("UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
                                    (to.value, time.time(), order_id))
                self.event(order_id, "transition", cur.status.value, to.value, detail)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return self.get(order_id), True  # type: ignore[return-value]

    def stale(self, older_than_seconds: float, statuses=(Status.PENDING,)) -> list:
        cutoff = time.time() - older_than_seconds
        marks = ",".join("?" for _ in statuses)
        rows = self.db.execute(f"SELECT * FROM orders WHERE status IN ({marks}) AND updated_at < ?",
                               (*[s.value for s in statuses], cutoff)).fetchall()
        return [self._row(r) for r in rows]

    def event(self, order_id: str, kind: str, from_status: Optional[str], to_status: Optional[str],
              detail: Optional[dict] = None) -> None:
        from .http import redact
        self.db.execute("INSERT INTO events (order_id, at, kind, from_status, to_status, detail) VALUES (?,?,?,?,?,?)",
                        (order_id, time.time(), kind, from_status, to_status,
                         json.dumps(redact(detail or {}), default=str)[:20000]))

    def events(self, order_id: str) -> list:
        rows = self.db.execute("SELECT at, kind, from_status, to_status, detail FROM events WHERE order_id=? ORDER BY id",
                               (order_id,)).fetchall()
        return [dict(r) for r in rows]


class TokenCache:
    """Shared access-token cache with single-flight refresh.

    bKash tokens last 3600 s and bKash may lock merchants that grant a new token
    on every request, so tokens are reused and refreshed 5 minutes early."""

    def __init__(self, store: Store, early_refresh_seconds: int = 300):
        self.store = store
        self.early = early_refresh_seconds
        self._locks: dict = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_or_fetch(self, key: str, fetch: Callable[[], Tuple[str, int]]) -> str:
        cached = self._read(key)
        if cached:
            return cached
        with self._lock_for(key):
            cached = self._read(key)       # another thread may have refreshed meanwhile
            if cached:
                return cached
            value, ttl = fetch()
            self.store.db.execute("INSERT OR REPLACE INTO tokens (key, value, expires_at) VALUES (?,?,?)",
                                  (key, value, time.time() + max(60, int(ttl))))
            return value

    def _read(self, key: str) -> Optional[str]:
        r = self.store.db.execute("SELECT value, expires_at FROM tokens WHERE key=?", (key,)).fetchone()
        if r and r["expires_at"] - self.early > time.time():
            return r["value"]
        return None

    def invalidate(self, key: str) -> None:
        self.store.db.execute("DELETE FROM tokens WHERE key=?", (key,))
