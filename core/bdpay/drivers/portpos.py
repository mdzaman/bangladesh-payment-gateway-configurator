"""PortPos (formerly PortWallet) invoice API v2 - BETA. Confirm against your
PortPos sandbox panel. Auth: Bearer base64(app_key:md5(secret_key + unix_ts))."""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, fmt_amount, to_poisha)

BASE = {"sandbox": "https://api-sandbox.portpos.com/payment/v2", "live": "https://api.portpos.com/payment/v2"}


class PortposDriver(Driver):
    id = "portpos"
    label = "PortPos"
    stability = "beta"
    capabilities = Capabilities(refunds="partial", recurring=True, webhooks="requery")
    required_env = ("PORTPOS_APP_KEY", "PORTPOS_SECRET_KEY")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("PORTPOS_BASE_URL") or BASE[self.env]).rstrip("/")

    def _headers(self) -> dict:
        ts = str(int(time.time()))
        token = hashlib.md5((self.cfg["PORTPOS_SECRET_KEY"] + ts).encode()).hexdigest()
        auth = base64.b64encode(f"{self.cfg['PORTPOS_APP_KEY']}:{token}".encode()).decode()
        return {"Authorization": f"Bearer {auth}", "Content-Type": "application/json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/invoice", headers=self._headers(), json={
            "order": {"amount": float(order.amount), "currency": "BDT", "redirect_url": urls.success,
                      "ipn_url": urls.ipn, "reference": order.order_id, "validity": 3600},
            "product": {"name": c.get("product") or f"Order {order.order_id}", "description": order.order_id},
            "billing": {"customer": {"name": c.get("name") or "Customer", "email": c.get("email") or "customer@example.com",
                                     "phone": c.get("phone") or "01700000000",
                                     "address": {"street": c.get("address") or "Dhaka", "city": c.get("city") or "Dhaka",
                                                 "state": c.get("city") or "Dhaka", "zipcode": c.get("postcode") or "1000",
                                                 "country": "BGD"}}},
        })
        data = j.get("data") or {}
        url = (data.get("action") or {}).get("url")
        if not url:
            raise self.fail("PROVIDER_DECLINED", str(j.get("error") or j))
        return PaymentSession(data.get("invoice_id"), url, j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        inv = params.get("invoice") or params.get("invoice_id")
        if inv and order.provider_payment_id and inv != order.provider_payment_id:
            raise self.fail("VALIDATION", "invoice does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self.http.json(self.id, "GET", f"{self.base}/invoice/{order.provider_payment_id}",
                           headers=self._headers(), retry=True)
        o = (j.get("data") or {}).get("order") or {}
        st = (o.get("status") or "").upper()
        status = {"ACCEPTED": Status.PAID, "REJECTED": Status.FAILED, "FAILED": Status.FAILED,
                  "CANCELLED": Status.CANCELLED, "EXPIRED": Status.EXPIRED}.get(st, Status.PENDING)
        amount = o.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, o.get("currency"),
                        o.get("reference") or None, order.provider_payment_id, None, j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        j = self.http.json(self.id, "POST", f"{self.base}/invoice/refund/{order.provider_payment_id}",
                           headers=self._headers(),
                           json={"refund": {"amount": float(fmt_amount(amount_poisha)), "currency": "BDT",
                                            "reason": reason}})
        ok = (j.get("result") or "").lower() == "success"
        return RefundResult(ok, ((j.get("data") or {}).get("refund") or {}).get("id"),
                            "processing" if ok else "failed", j)

    def ipn_lookup(self, params):
        inv = params.get("invoice") or params.get("invoice_id")
        return ("provider_payment_id", inv) if inv else None
