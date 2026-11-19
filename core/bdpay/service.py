"""BdPay: the one object your application talks to.

    pay = BdPay.from_env()
    pay.create_order("INV-1001", "1250.00", "bkash", {"name": "Rahim", "phone": "01700000000"})
    url = pay.start_payment("INV-1001")          # redirect the customer here
    # ...provider sends the customer back to /bdpay/callback/... which calls:
    order = pay.handle_callback(gateway, order_id, token, outcome, params)
    if order.status == Status.PAID: fulfil()
"""
from __future__ import annotations

import logging
from typing import Mapping, Optional

from .config import Settings
from .core import (BdPayError, CallbackUrls, Driver, Order, RefundResult, Status,
                   Verified, fmt_amount, to_poisha)
from .http import Http
from .registry import load_driver_class
from .security import check_callback, sign_callback
from .store import Store, TokenCache

log = logging.getLogger("bdpay")


class BdPay:
    def __init__(self, settings: Settings, store: Optional[Store] = None, transport=None,
                 callback_prefix: str = "/bdpay"):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.tokens = TokenCache(self.store)
        self.http = Http(settings.environment, transport=transport)
        self.callback_prefix = callback_prefix.rstrip("/")
        self.drivers: dict = {}
        for name in settings.gateways:
            cls = load_driver_class(name)
            self.drivers[name] = cls(settings.gateway(name), self.http, self.tokens)
        if not self.drivers:
            log.warning("no gateways enabled: set BDPAY_GATEWAYS, e.g. BDPAY_GATEWAYS=bkash,sslcommerz")

    @classmethod
    def from_env(cls, **kwargs) -> "BdPay":
        return cls(Settings.from_env(), **kwargs)

    # ------------------------------------------------------------------ helpers
    def driver(self, gateway: str) -> Driver:
        d = self.drivers.get(gateway)
        if not d:
            raise BdPayError("CONFIG", f"gateway '{gateway}' is not enabled (BDPAY_GATEWAYS)")
        return d

    def _order(self, order_id: str) -> Order:
        o = self.store.get(order_id)
        if not o:
            raise BdPayError("NOT_FOUND", f"order {order_id} not found")
        return o

    def callback_urls(self, order: Order) -> CallbackUrls:
        tok = sign_callback(self.settings.secret, order.gateway, order.order_id)
        base = f"{self.settings.public_url}{self.callback_prefix}/callback/{order.gateway}/{order.order_id}/{tok}"
        return CallbackUrls(success=f"{base}/success", fail=f"{base}/fail", cancel=f"{base}/cancel",
                            ipn=f"{self.settings.public_url}{self.callback_prefix}/ipn/{order.gateway}")

    # ------------------------------------------------------------------ API
    def create_order(self, order_id: str, amount, gateway: str, customer: Optional[dict] = None,
                     currency: str = "BDT") -> Order:
        """Record the order on the server. The amount saved here is the only
        amount bdpay will ever accept as paid."""
        self.driver(gateway)
        if currency != "BDT":
            raise BdPayError("VALIDATION", "only BDT is supported")
        if not order_id or len(order_id) > 40 or not all(c.isalnum() or c in "-_" for c in order_id):
            raise BdPayError("VALIDATION", "order_id must be 1-40 chars of letters, digits, - or _")
        poisha = to_poisha(amount)
        if poisha <= 0:
            raise BdPayError("VALIDATION", "amount must be greater than zero")
        return self.store.create_order(Order(order_id=order_id, gateway=gateway, amount_poisha=poisha,
                                             currency=currency, customer=customer or {}))

    def start_payment(self, order_id: str) -> str:
        order = self._order(order_id)
        if order.status not in (Status.CREATED, Status.PENDING):
            raise BdPayError("VALIDATION", f"order is {order.status.value}; cannot start payment")
        session = self.driver(order.gateway).create_payment(order, self.callback_urls(order))
        self.store.set_provider_ids(order.order_id, payment_id=session.provider_payment_id)
        self.store.transition(order.order_id, Status.PENDING, {"provider_payment_id": session.provider_payment_id})
        return session.redirect_url

    def handle_callback(self, gateway: str, order_id: str, token: str, outcome: str,
                        params: Mapping[str, str]) -> Order:
        """Customer came back from the provider. Never trusted: re-verified server-side."""
        if not check_callback(self.settings.secret, gateway, order_id, token):
            raise BdPayError("SIGNATURE_INVALID", "callback link is invalid or expired")
        order = self._order(order_id)
        if order.gateway != gateway:
            raise BdPayError("VALIDATION", "gateway does not match the order")
        if order.status in (Status.PAID, Status.REFUNDED, Status.PARTIALLY_REFUNDED, Status.NEEDS_REVIEW):
            return order                              # already settled: duplicate return is a no-op
        params = dict(params)
        params.setdefault("_outcome", outcome)
        verified = self.driver(gateway).verify_return(order, params)
        return self._apply(order, verified, source=f"callback:{outcome}")

    def handle_ipn(self, gateway: str, params: Mapping[str, str]) -> Optional[Order]:
        """Server-to-server notification. Used only as a hint to re-verify."""
        d = self.driver(gateway)
        ref = d.ipn_lookup(params)
        if not ref:
            raise BdPayError("VALIDATION", "IPN does not identify an order")
        kind, value = ref
        order = self.store.get(value) if kind == "order_id" else self.store.find_by_provider_id(gateway, value)
        if not order or order.gateway != gateway:
            raise BdPayError("NOT_FOUND", "IPN refers to an unknown order")
        if order.status in (Status.PAID, Status.REFUNDED, Status.PARTIALLY_REFUNDED, Status.NEEDS_REVIEW):
            return order
        verified = d.verify_return(order, {**dict(params), "_outcome": "ipn"})
        return self._apply(order, verified, source="ipn")

    def requery(self, order_id: str) -> Order:
        order = self._order(order_id)
        if order.status in (Status.REFUNDED, Status.NEEDS_REVIEW):
            return order
        verified = self.driver(order.gateway).query(order)
        return self._apply(order, verified, source="requery")

    def refund(self, order_id: str, amount=None, reason: str = "customer request") -> RefundResult:
        order = self._order(order_id)
        if order.status not in (Status.PAID, Status.PARTIALLY_REFUNDED):
            raise BdPayError("VALIDATION", f"order is {order.status.value}; only paid orders can be refunded")
        d = self.driver(order.gateway)
        refundable = order.amount_poisha - order.refunded_poisha
        poisha = refundable if amount is None else to_poisha(amount)
        if poisha <= 0 or poisha > refundable:
            raise BdPayError("VALIDATION", f"refund must be between 0.01 and {fmt_amount(refundable)}")
        if d.capabilities.refunds == "none":
            raise BdPayError("NOT_SUPPORTED", "this gateway refunds through its merchant panel", d.id)
        if d.capabilities.refunds == "full" and poisha != refundable:
            raise BdPayError("NOT_SUPPORTED", "this gateway only supports full refunds by API", d.id)
        result = d.refund(order, poisha, reason)
        self.store.event(order_id, "refund", order.status.value, None,
                         {"amount_poisha": poisha, "ok": result.ok, "ref": result.refund_ref, "status": result.status})
        if result.ok:
            total = order.refunded_poisha + poisha
            to = Status.REFUNDED if total >= order.amount_poisha else Status.PARTIALLY_REFUNDED
            self.store.transition(order_id, to, {"refund_ref": result.refund_ref}, refunded_poisha=total)
        return result

    def reconcile(self, older_than_minutes: int = 10, expire_after_hours: int = 24) -> list:
        """Re-check every payment left pending (customer closed the tab, callback lost...)."""
        out = []
        for order in self.store.stale(older_than_minutes * 60):
            try:
                updated = self.requery(order.order_id)
                if updated.status == Status.PENDING and order.created_at < __import__("time").time() - expire_after_hours * 3600:
                    updated, _ = self.store.transition(order.order_id, Status.EXPIRED, {"reason": "reconcile timeout"})
                out.append((order.order_id, updated.status.value))
            except BdPayError as e:
                out.append((order.order_id, f"error: {e.code}"))
        return out

    # ------------------------------------------------------------------ core rule
    def _apply(self, order: Order, v: Verified, source: str) -> Order:
        if v.provider_payment_id or v.provider_txn_id:
            self.store.set_provider_ids(order.order_id, payment_id=None if order.provider_payment_id else v.provider_payment_id,
                                        txn_id=v.provider_txn_id)
        detail = {"source": source, "provider_status": v.status.value, "amount_poisha": v.amount_poisha,
                  "currency": v.currency, "order_ref": v.order_ref, "txn": v.provider_txn_id}
        if v.status == Status.PAID:
            problems = []
            if v.amount_poisha is None or v.amount_poisha != order.amount_poisha:
                problems.append(f"amount {v.amount_poisha} != {order.amount_poisha}")
            if v.currency and v.currency.upper() not in ("BDT", "050"):
                problems.append(f"currency {v.currency}")
            if v.order_ref and str(v.order_ref) != order.order_id:
                problems.append(f"order ref {v.order_ref} != {order.order_id}")
            if problems:
                detail["problems"] = problems
                updated, _ = self.store.transition(order.order_id, Status.NEEDS_REVIEW, detail)
                log.error("payment %s needs review: %s", order.order_id, "; ".join(problems))
                return updated
        if v.status == Status.PENDING and order.status == Status.PENDING:
            self.store.event(order.order_id, "still_pending", order.status.value, None, detail)
            return self._order(order.order_id)
        updated, _ = self.store.transition(order.order_id, v.status, detail)
        return updated
