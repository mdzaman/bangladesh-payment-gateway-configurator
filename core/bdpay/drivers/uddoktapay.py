"""UddoktaPay - EXPERIMENTAL.

COMPLIANCE WARNING: UddoktaPay-style tools verify payments sent to personal or
agent wallet numbers. MFS terms generally ban commercial collection into
personal accounts, and the Payment and Settlement Systems Act 2024 requires a
Bangladesh Bank licence to provide payment services. Use only with a merchant
wallet and legal advice. Disabled unless BDPAY_ALLOW_EXPERIMENTAL=1."""
from __future__ import annotations

from typing import Mapping

from ..core import (BdPayError, Capabilities, CallbackUrls, Driver, Order, PaymentSession,
                    Status, Verified, to_poisha)


class UddoktapayDriver(Driver):
    id = "uddoktapay"
    label = "UddoktaPay (experimental)"
    stability = "experimental"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("UDDOKTAPAY_BASE_URL", "UDDOKTAPAY_API_KEY")

    def __init__(self, cfg, http, tokens):
        if cfg.get("BDPAY_ALLOW_EXPERIMENTAL") != "1":
            raise BdPayError("CONFIG", "uddoktapay is experimental: set BDPAY_ALLOW_EXPERIMENTAL=1 after reading "
                                       "the compliance warning in bdpay/drivers/uddoktapay.py", "uddoktapay")
        super().__init__(cfg, http, tokens)
        self.base = cfg["UDDOKTAPAY_BASE_URL"].rstrip("/")

    def _h(self) -> dict:
        return {"RT-UDDOKTAPAY-API-KEY": self.cfg["UDDOKTAPAY_API_KEY"], "Accept": "application/json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/api/checkout-v2", headers=self._h(), json={
            "full_name": c.get("name") or "Customer", "email": c.get("email") or "customer@example.com",
            "amount": order.amount, "metadata": {"order_id": order.order_id},
            "redirect_url": urls.success, "return_type": "GET", "cancel_url": urls.cancel, "webhook_url": urls.ipn,
        })
        if not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        inv = params.get("invoice_id")
        if not inv:
            return Verified(Status.CANCELLED if params.get("_outcome") == "cancel" else Status.PENDING)
        j = self.http.json(self.id, "POST", f"{self.base}/api/verify-payment", headers=self._h(),
                           json={"invoice_id": inv}, retry=True)
        st = (j.get("status") or "").upper()
        status = {"COMPLETED": Status.PAID, "PENDING": Status.PENDING, "ERROR": Status.FAILED}.get(st, Status.FAILED)
        ref = (j.get("metadata") or {}).get("order_id")
        amount = j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None, ref,
                        inv, j.get("transaction_id"), j)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        return self.verify_return(order, {"invoice_id": order.provider_payment_id})

    def ipn_lookup(self, params):
        meta = params.get("metadata") or {}
        oid = meta.get("order_id") if isinstance(meta, dict) else None
        return ("order_id", oid) if oid else None
