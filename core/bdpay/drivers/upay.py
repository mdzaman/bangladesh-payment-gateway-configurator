"""Upay (UCB Fintech) - BETA. Credentials, base URL and the v4.x API PDF come
only through Upay merchant onboarding; set UPAY_BASE_URL from that pack.
Production accepts IPv4 only. Refunds by API are full refunds only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, to_poisha)

DHAKA = timezone(timedelta(hours=6))


class UpayDriver(Driver):
    id = "upay"
    label = "Upay"
    stability = "beta"
    capabilities = Capabilities(refunds="full", webhooks="requery")
    required_env = ("UPAY_BASE_URL", "UPAY_MERCHANT_ID", "UPAY_MERCHANT_KEY", "UPAY_MERCHANT_CODE",
                    "UPAY_MERCHANT_NAME", "UPAY_MERCHANT_MOBILE")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = cfg["UPAY_BASE_URL"].rstrip("/")
        self.token_key = f"upay:{self.env}:{cfg['UPAY_MERCHANT_ID']}"

    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.base}/payment/merchant-auth/", json={
            "merchant_id": self.cfg["UPAY_MERCHANT_ID"], "merchant_key": self.cfg["UPAY_MERCHANT_KEY"]})
        token = (j.get("data") or {}).get("token")
        if not token:
            raise self.fail("AUTH", j.get("message") or str(j), j.get("code"))
        return token, 3000

    def _h(self) -> dict:
        return {"Authorization": f"UPAY {self.tokens.get_or_fetch(self.token_key, self._grant)}"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        j = self.http.json(self.id, "POST", f"{self.base}/payment/merchant-payment-init/", headers=self._h(), json={
            "date": datetime.now(DHAKA).strftime("%Y-%m-%d"),       # must be today's date in Asia/Dhaka
            "txn_id": order.order_id, "invoice_id": order.order_id, "amount": order.amount,
            "merchant_id": self.cfg["UPAY_MERCHANT_ID"], "merchant_name": self.cfg["UPAY_MERCHANT_NAME"],
            "merchant_code": self.cfg["UPAY_MERCHANT_CODE"], "merchant_mobile": self.cfg["UPAY_MERCHANT_MOBILE"],
            "merchant_country_code": "BD", "merchant_city": self.cfg.get("UPAY_MERCHANT_CITY", "Dhaka"),
            "merchant_category_code": self.cfg.get("UPAY_MERCHANT_CATEGORY_CODE", "5999"),
            "transaction_currency_code": "BDT", "redirect_url": urls.success,
        })
        url = (j.get("data") or {}).get("gateway_url")
        if not url:
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), j.get("code"))
        return PaymentSession(order.order_id, url, j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.base}/payment/single-payment-status/{order.order_id}/",
                           headers=self._h(), retry=True)
        d = j.get("data") or {}
        st = (d.get("status") or "").lower()
        status = {"success": Status.PAID, "successful": Status.PAID, "failed": Status.FAILED,
                  "cancelled": Status.CANCELLED, "expired": Status.EXPIRED}.get(st, Status.PENDING)
        amount = d.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        d.get("txn_id") or d.get("invoice_id"), order.order_id, d.get("trx_id"), j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        j = self.http.json(self.id, "POST", f"{self.base}/payment/refund/", headers=self._h(),
                           json={"txn_id": order.order_id, "reason": reason})
        ok = str(j.get("code", "")).upper().endswith("200") or (j.get("data") or {}).get("status") == "success"
        return RefundResult(ok, (j.get("data") or {}).get("refund_id"), "processing" if ok else "failed", j)
