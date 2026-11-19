"""PayStation - BETA. Confirm against paystation.com.bd/documentation and the
credentials PayStation issues you."""
from __future__ import annotations

from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandbox.paystation.com.bd", "live": "https://api.paystation.com.bd"}


class PaystationDriver(Driver):
    id = "paystation"
    label = "PayStation"
    stability = "beta"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("PAYSTATION_MERCHANT_ID", "PAYSTATION_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("PAYSTATION_BASE_URL") or BASE[self.env]).rstrip("/")

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/initiate-payment", data={
            "merchantId": self.cfg["PAYSTATION_MERCHANT_ID"], "password": self.cfg["PAYSTATION_PASSWORD"],
            "invoice_number": order.order_id, "currency": "BDT", "payment_amount": order.amount,
            "pay_with_charge": self.cfg.get("PAYSTATION_PAY_WITH_CHARGE", "0"), "reference": order.order_id,
            "cust_name": c.get("name") or "Customer", "cust_phone": c.get("phone") or "01700000000",
            "cust_email": c.get("email") or "customer@example.com", "cust_address": c.get("address") or "Dhaka",
            "callback_url": urls.success, "checkout_items": c.get("product") or f"Order {order.order_id}",
        })
        if str(j.get("status_code")) != "200" or not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), str(j.get("status_code")))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("invoice_number") and params["invoice_number"] != order.order_id:
            raise self.fail("VALIDATION", "invoice_number does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "POST", f"{self.base}/transaction-status", retry=True,
                           headers={"merchantId": self.cfg["PAYSTATION_MERCHANT_ID"]},
                           data={"invoice_number": order.order_id})
        d = j.get("data") or {}
        st = (d.get("trx_status") or "").lower()
        status = {"success": Status.PAID, "successful": Status.PAID, "failed": Status.FAILED,
                  "cancel": Status.CANCELLED, "cancelled": Status.CANCELLED}.get(st, Status.PENDING)
        amount = d.get("payment_amount") or d.get("request_amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        d.get("invoice_number"), None, d.get("trx_id"), j)

    def ipn_lookup(self, params):
        return ("order_id", params["invoice_number"]) if params.get("invoice_number") else None
