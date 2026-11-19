"""aamarPay JSON API (developer.aamarpay.com). Success/fail posts are only a
hint; the Search Transaction API decides. status_code: 0 initiated,
2 successful, 3 expired, 7 failed."""
from __future__ import annotations

from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

HOST = {"sandbox": "https://sandbox.aamarpay.com", "live": "https://secure.aamarpay.com"}


class AamarpayDriver(Driver):
    id = "aamarpay"
    label = "aamarPay"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("AAMARPAY_STORE_ID", "AAMARPAY_SIGNATURE_KEY")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.host = (cfg.get("AAMARPAY_BASE_URL") or HOST[self.env]).rstrip("/")

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.host}/jsonpost.php", json={
            "store_id": self.cfg["AAMARPAY_STORE_ID"], "signature_key": self.cfg["AAMARPAY_SIGNATURE_KEY"],
            "tran_id": order.order_id, "amount": order.amount, "currency": "BDT",
            "desc": c.get("product") or f"Order {order.order_id}",
            "cus_name": c.get("name") or "Customer", "cus_email": c.get("email") or "customer@example.com",
            "cus_phone": c.get("phone") or "01700000000", "cus_add1": c.get("address") or "Dhaka",
            "cus_city": c.get("city") or "Dhaka", "cus_country": "Bangladesh",
            "success_url": urls.success, "fail_url": urls.fail, "cancel_url": urls.cancel,
            "opt_a": order.order_id, "type": "json",
        })
        if str(j.get("result")).lower() != "true" or not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", str(j))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("mer_txnid") and params["mer_txnid"] != order.order_id:
            raise self.fail("VALIDATION", "mer_txnid does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.host}/api/v1/trxcheck/request.php", retry=True, params={
            "request_id": order.order_id, "store_id": self.cfg["AAMARPAY_STORE_ID"],
            "signature_key": self.cfg["AAMARPAY_SIGNATURE_KEY"], "type": "json"})
        code = str(j.get("status_code", ""))
        status = {"2": Status.PAID, "0": Status.PENDING, "3": Status.EXPIRED, "7": Status.FAILED}.get(code)
        if status is None:
            status = Status.PAID if (j.get("pay_status") or "").lower() == "successful" else Status.PENDING
        amount = j.get("amount_original") or j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None,
                        j.get("currency_merchant") or j.get("currency"), j.get("mer_txnid"),
                        None, j.get("pg_txnid"), j)

    def ipn_lookup(self, params):
        return ("order_id", params["mer_txnid"]) if params.get("mer_txnid") else None
