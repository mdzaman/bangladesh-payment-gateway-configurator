"""SSLCOMMERZ v4 (developer.sslcommerz.com/doc/v4). Cards, internet banking and
wallets (incl. bKash, Nagad, Rocket, Upay, OK Wallet...) through one gateway.

The IPN is not trusted on its own: verify_sign is checked, then the Order
Validation API is called, then tran_id/amount/currency are compared with the
stored order. VALIDATED means "already validated" and is treated as paid.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, fmt_amount, to_poisha)

HOST = {"sandbox": "https://sandbox.sslcommerz.com", "live": "https://securepay.sslcommerz.com"}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def check_verify_sign(params: Mapping[str, str], store_passwd: str) -> bool:
    keys = [k for k in (params.get("verify_key") or "").split(",") if k]
    if not keys or not params.get("verify_sign"):
        return False
    data = {k: params.get(k, "") for k in keys}
    data["store_passwd"] = _md5(store_passwd)
    s = "&".join(f"{k}={data[k]}" for k in sorted(data))
    return _md5(s) == params["verify_sign"]


class SslcommerzDriver(Driver):
    id = "sslcommerz"
    label = "SSLCOMMERZ"
    capabilities = Capabilities(refunds="partial", webhooks="requery")
    required_env = ("SSLCOMMERZ_STORE_ID", "SSLCOMMERZ_STORE_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.host = (cfg.get("SSLCOMMERZ_BASE_URL") or HOST[self.env]).rstrip("/")
        self.store_id = cfg["SSLCOMMERZ_STORE_ID"]
        self.passwd = cfg["SSLCOMMERZ_STORE_PASSWORD"]

    def _auth(self) -> dict:
        return {"store_id": self.store_id, "store_passwd": self.passwd, "format": "json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        data = {
            "store_id": self.store_id, "store_passwd": self.passwd,
            "total_amount": order.amount, "currency": "BDT", "tran_id": order.order_id,
            "success_url": urls.success, "fail_url": urls.fail, "cancel_url": urls.cancel, "ipn_url": urls.ipn,
            "cus_name": c.get("name") or "Customer", "cus_email": c.get("email") or "customer@example.com",
            "cus_add1": c.get("address") or "Dhaka", "cus_city": c.get("city") or "Dhaka",
            "cus_postcode": c.get("postcode") or "1000", "cus_country": "Bangladesh",
            "cus_phone": c.get("phone") or "01700000000",
            "shipping_method": "NO", "num_of_item": "1",
            "product_name": c.get("product") or "Order " + order.order_id,
            "product_category": c.get("category") or "general", "product_profile": "general",
            "value_a": order.order_id,
        }
        j = self.http.json(self.id, "POST", f"{self.host}/gwprocess/v4/api.php", data=data)
        if (j.get("status") or "").upper() != "SUCCESS" or not j.get("GatewayPageURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("failedreason") or str(j))
        return PaymentSession(j.get("sessionkey"), j["GatewayPageURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("verify_sign") and not check_verify_sign(params, self.passwd):
            raise self.fail("SIGNATURE_INVALID", "verify_sign does not match")
        if params.get("tran_id") and params["tran_id"] != order.order_id:
            raise self.fail("VALIDATION", "tran_id does not belong to this order")
        val_id = params.get("val_id")
        if not val_id:
            return self.query(order)              # fail/cancel posts carry no val_id
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/validationserverAPI.php",
                           params={"val_id": val_id, **self._auth()}, retry=True)
        return self._from_record(j)

    def _from_record(self, j: dict) -> Verified:
        st = (j.get("status") or "").upper()
        status = {"VALID": Status.PAID, "VALIDATED": Status.PAID, "PENDING": Status.PENDING,
                  "FAILED": Status.FAILED, "CANCELLED": Status.CANCELLED,
                  "UNATTEMPTED": Status.PENDING, "EXPIRED": Status.EXPIRED}.get(st, Status.FAILED)
        currency = j.get("currency_type") or j.get("currency")
        amount = j.get("currency_amount") if j.get("currency_type") else j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, currency,
                        j.get("tran_id"), j.get("sessionkey"), j.get("bank_tran_id"), j)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/merchantTransIDvalidationAPI.php",
                           params={"tran_id": order.order_id, **self._auth()}, retry=True)
        if j.get("APIConnect") not in (None, "DONE"):
            raise self.fail("PROVIDER_UNAVAILABLE", f"APIConnect={j.get('APIConnect')}")
        elements = j.get("element") or []
        for e in elements:
            if (e.get("status") or "").upper() in ("VALID", "VALIDATED"):
                return self._from_record(e)
        if any((e.get("status") or "").upper() == "PENDING" for e in elements):
            return Verified(Status.PENDING, raw=j)
        if elements:
            return self._from_record(elements[0])
        return Verified(Status.PENDING, raw=j)    # not attempted yet

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        if not order.provider_txn_id:
            raise self.fail("VALIDATION", "no bank_tran_id stored for this order")
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/merchantTransIDvalidationAPI.php",
                           params={"bank_tran_id": order.provider_txn_id, "refund_amount": fmt_amount(amount_poisha),
                                   "refund_remarks": (reason or "refund")[:250], **self._auth()})
        ok = j.get("APIConnect") == "DONE" and (j.get("status") or "").lower() in ("success", "processing")
        return RefundResult(ok, j.get("refund_ref_id"), "processing" if ok else "failed", j)

    def ipn_lookup(self, params):
        return ("order_id", params["tran_id"]) if params.get("tran_id") else None
