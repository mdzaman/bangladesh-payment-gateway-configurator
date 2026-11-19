"""shurjoPay v2.1. get_token -> secret-pay -> customer pays -> verification.
sp_code 1000 = success."""
from __future__ import annotations

import json
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandbox.shurjopayment.com", "live": "https://engine.shurjopayment.com"}


class ShurjopayDriver(Driver):
    id = "shurjopay"
    label = "shurjoPay"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("SHURJOPAY_USERNAME", "SHURJOPAY_PASSWORD", "SHURJOPAY_PREFIX")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("SHURJOPAY_BASE_URL") or BASE[self.env]).rstrip("/")
        self.token_key = f"shurjopay:{self.env}:{cfg['SHURJOPAY_USERNAME']}"

    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.base}/api/get_token", json={
            "username": self.cfg["SHURJOPAY_USERNAME"], "password": self.cfg["SHURJOPAY_PASSWORD"]})
        if not j.get("token"):
            raise self.fail("AUTH", j.get("message") or str(j), str(j.get("sp_code")))
        return json.dumps({"token": j["token"], "store_id": j.get("store_id")}), int(j.get("expires_in") or 3000)

    def _auth(self) -> dict:
        return json.loads(self.tokens.get_or_fetch(self.token_key, self._grant))

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        a, c = self._auth(), order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/api/secret-pay",
                           headers={"Authorization": f"Bearer {a['token']}"}, json={
            "prefix": self.cfg["SHURJOPAY_PREFIX"], "token": a["token"], "store_id": a["store_id"],
            "return_url": urls.success, "cancel_url": urls.cancel,
            "amount": order.amount, "order_id": order.order_id, "currency": "BDT",
            "customer_name": c.get("name") or "Customer", "customer_phone": c.get("phone") or "01700000000",
            "customer_email": c.get("email") or "customer@example.com",
            "customer_address": c.get("address") or "Dhaka", "customer_city": c.get("city") or "Dhaka",
            "customer_post_code": c.get("postcode") or "1000", "client_ip": c.get("ip") or "127.0.0.1",
            "value1": order.order_id,
        })
        if not j.get("checkout_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), str(j.get("sp_code")))
        return PaymentSession(j.get("sp_order_id"), j["checkout_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("order_id") and order.provider_payment_id and params["order_id"] != order.provider_payment_id:
            raise self.fail("VALIDATION", "order_id does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        resp = self.http.request(self.id, "POST", f"{self.base}/api/verification", retry=True,
                                 headers={"Authorization": f"Bearer {self._auth()['token']}"},
                                 json={"order_id": order.provider_payment_id})
        if resp.status_code == 401:
            self.tokens.invalidate(self.token_key)
            raise self.fail("AUTH", "token expired; retry", retryable=True)
        j = resp.json()
        rec = j[0] if isinstance(j, list) and j else j if isinstance(j, dict) else {}
        code = str(rec.get("sp_code", ""))
        status = {"1000": Status.PAID, "1001": Status.FAILED, "1002": Status.CANCELLED}.get(code, Status.PENDING)
        amount = rec.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, rec.get("currency"),
                        rec.get("customer_order_id"), order.provider_payment_id, rec.get("bank_trx_id"), rec)
