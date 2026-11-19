"""bKash Tokenized Checkout (developer.bka.sh).

Flow: grant token (cached ~55 min) -> create (mode 0011) -> customer pays on
bkashURL -> callback ?paymentID&status -> execute -> if execute fails or times
out, query payment status. Only a Completed status with the right amount and
invoice number marks the order paid.
"""
from __future__ import annotations

from typing import Mapping

from ..core import (BdPayError, Capabilities, CallbackUrls, Driver, Order, PaymentSession,
                    RefundResult, Status, Verified, fmt_amount, to_poisha)

BASE = {"sandbox": "https://tokenized.sandbox.bka.sh", "live": "https://tokenized.pay.bka.sh"}


class BkashDriver(Driver):
    id = "bkash"
    label = "bKash Tokenized Checkout"
    capabilities = Capabilities(refunds="partial", recurring=True, webhooks="requery")
    required_env = ("BKASH_APP_KEY", "BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        version = cfg.get("BKASH_API_VERSION", "v1.2.0-beta")
        base = (cfg.get("BKASH_BASE_URL") or BASE[self.env]).rstrip("/")
        self.url = f"{base}/{version}/tokenized/checkout"
        self.app_key = cfg["BKASH_APP_KEY"]
        self.token_key = f"bkash:{self.env}:{self.app_key}"

    # ---------------------------------------------------------------- auth
    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.url}/token/grant",
                           headers={"username": self.cfg["BKASH_USERNAME"], "password": self.cfg["BKASH_PASSWORD"],
                                    "Accept": "application/json"},
                           json={"app_key": self.app_key, "app_secret": self.cfg["BKASH_APP_SECRET"]})
        if not j.get("id_token"):
            raise self.fail("AUTH", f"token grant failed: {j.get('statusMessage') or j.get('msg') or j}",
                            j.get("statusCode"))
        return j["id_token"], int(j.get("expires_in") or 3600)

    def _call(self, path: str, body: dict, retry: bool = False) -> dict:
        for attempt in (1, 2):
            token = self.tokens.get_or_fetch(self.token_key, self._grant)
            resp = self.http.request(self.id, "POST", f"{self.url}{path}", retry=retry, json=body,
                                     headers={"Authorization": token, "X-APP-Key": self.app_key,
                                              "Accept": "application/json"})
            if resp.status_code == 401 and attempt == 1:
                self.tokens.invalidate(self.token_key)   # token revoked early: get a fresh one once
                continue
            if resp.status_code >= 500:
                raise self.fail("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", retryable=True)
            try:
                return resp.json()
            except ValueError:
                raise self.fail("PROVIDER_UNAVAILABLE", f"non-JSON response: {resp.text[:200]!r}")
        raise self.fail("AUTH", "bKash rejected a fresh token (401)")

    # ---------------------------------------------------------------- payments
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        payer = (order.customer.get("phone") or order.order_id)[:255]
        j = self._call("/create", {
            "mode": "0011",
            "payerReference": payer,
            "callbackURL": urls.success,           # bKash appends ?paymentID=..&status=success|failure|cancel
            "amount": order.amount,
            "currency": "BDT",
            "intent": "sale",
            "merchantInvoiceNumber": order.order_id,
        })
        if j.get("statusCode") != "0000" or not j.get("bkashURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("statusMessage") or str(j), j.get("statusCode"))
        return PaymentSession(j["paymentID"], j["bkashURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        pid = params.get("paymentID") or order.provider_payment_id
        if order.provider_payment_id and pid != order.provider_payment_id:
            raise self.fail("VALIDATION", "paymentID does not belong to this order")
        status = (params.get("status") or "").lower()
        if params.get("_outcome") == "ipn":
            return self.query(order)
        if status == "cancel":
            return Verified(Status.CANCELLED, provider_payment_id=pid, raw=dict(params))
        if status != "success":
            return Verified(Status.FAILED, provider_payment_id=pid, raw=dict(params))
        try:
            j = self._call("/execute", {"paymentID": pid})
        except BdPayError as e:
            if e.code not in ("TIMEOUT", "PROVIDER_UNAVAILABLE"):
                raise
            j = None
        if not j or j.get("statusCode") != "0000":
            # Execute timed out, errored, or was already done (e.g. 2062): the query is authoritative.
            return self.query(order)
        return self._verified(j, pid)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self._call("/payment/status", {"paymentID": order.provider_payment_id}, retry=True)
        if j.get("statusCode") != "0000":
            raise self.fail("PROVIDER_UNAVAILABLE", j.get("statusMessage") or str(j), j.get("statusCode"))
        return self._verified(j, order.provider_payment_id)

    def _verified(self, j: dict, pid: str) -> Verified:
        ts = (j.get("transactionStatus") or "").lower()
        status = {"completed": Status.PAID, "initiated": Status.PENDING, "authorized": Status.PENDING,
                  "pending authorized": Status.PENDING, "cancelled": Status.CANCELLED,
                  "expired": Status.EXPIRED}.get(ts, Status.FAILED)
        return Verified(status, to_poisha(j["amount"]) if j.get("amount") else None, j.get("currency"),
                        j.get("merchantInvoiceNumber"), pid, j.get("trxID"), j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        if not order.provider_txn_id:
            raise self.fail("VALIDATION", "no bKash trxID stored for this order")
        j = self._call("/payment/refund", {
            "paymentID": order.provider_payment_id, "trxID": order.provider_txn_id,
            "amount": fmt_amount(amount_poisha), "sku": "bdpay", "reason": (reason or "refund")[:255],
        })
        ok = bool(j.get("refundTrxID")) and (j.get("transactionStatus") or "").lower() == "completed"
        if not ok and j.get("statusCode") not in (None, "0000"):
            raise self.fail("PROVIDER_DECLINED", j.get("statusMessage") or str(j), j.get("statusCode"))
        return RefundResult(ok, j.get("refundTrxID"), "completed" if ok else "failed", j)
