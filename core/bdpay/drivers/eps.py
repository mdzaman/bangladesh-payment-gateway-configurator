"""EPS (Easy Payment System) - BETA. Check field names against the EPS merchant
portal / official SDKs (github.com/EPS-PG) before going live.

GetToken (JWT) -> InitializeEPS (RedirectURL) -> CheckMerchantTransactionStatus.
Every call carries x-hash = Base64(HMAC-SHA512(hash_key, <value>))."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandboxpgapi.eps.com.bd", "live": "https://pgapi.eps.com.bd"}


class EpsDriver(Driver):
    id = "eps"
    label = "EPS"
    stability = "beta"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("EPS_USERNAME", "EPS_PASSWORD", "EPS_HASH_KEY", "EPS_MERCHANT_ID", "EPS_STORE_ID")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("EPS_BASE_URL") or BASE[self.env]).rstrip("/")
        self.token_key = f"eps:{self.env}:{cfg['EPS_USERNAME']}"

    def _hash(self, value: str) -> str:
        return base64.b64encode(hmac.new(self.cfg["EPS_HASH_KEY"].encode(), value.encode(),
                                         hashlib.sha512).digest()).decode()

    def _grant(self):
        user = self.cfg["EPS_USERNAME"]
        j = self.http.json(self.id, "POST", f"{self.base}/v1/Auth/GetToken", headers={"x-hash": self._hash(user)},
                           json={"userName": user, "password": self.cfg["EPS_PASSWORD"]})
        if not j.get("token"):
            raise self.fail("AUTH", j.get("errorMessage") or str(j))
        return j["token"], 1800

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        token, c = self.tokens.get_or_fetch(self.token_key, self._grant), order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/v1/EPSEngine/InitializeEPS",
                           headers={"x-hash": self._hash(order.order_id), "Authorization": f"Bearer {token}"}, json={
            "merchantId": self.cfg["EPS_MERCHANT_ID"], "storeId": self.cfg["EPS_STORE_ID"],
            "CustomerOrderId": order.order_id, "merchantTransactionId": order.order_id,
            "transactionTypeId": 1, "totalAmount": float(order.amount),
            "successUrl": urls.success, "failUrl": urls.fail, "cancelUrl": urls.cancel,
            "customerName": c.get("name") or "Customer", "customerEmail": c.get("email") or "customer@example.com",
            "customerAddress": c.get("address") or "Dhaka", "customerCity": c.get("city") or "Dhaka",
            "customerState": c.get("city") or "Dhaka", "customerPostcode": c.get("postcode") or "1000",
            "customerCountry": "BD", "customerPhone": c.get("phone") or "01700000000",
            "productName": c.get("product") or f"Order {order.order_id}",
        })
        if not j.get("RedirectURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("ErrorMessage") or str(j), str(j.get("ErrorCode")))
        return PaymentSession(j.get("TransactionId"), j["RedirectURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        return self.query(order)

    def query(self, order: Order) -> Verified:
        token = self.tokens.get_or_fetch(self.token_key, self._grant)
        j = self.http.json(self.id, "GET", f"{self.base}/v1/EPSEngine/CheckMerchantTransactionStatus", retry=True,
                           params={"merchantTransactionId": order.order_id},
                           headers={"x-hash": self._hash(order.order_id), "Authorization": f"Bearer {token}"})
        st = (j.get("Status") or "").lower()
        status = {"success": Status.PAID, "failed": Status.FAILED, "cancel": Status.CANCELLED,
                  "cancelled": Status.CANCELLED, "pending": Status.PENDING}.get(st, Status.PENDING)
        amount = j.get("TotalAmount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        j.get("MerchantTransactionId"), None, j.get("EpsTransactionId"), j)
