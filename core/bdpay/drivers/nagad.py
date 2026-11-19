"""Nagad merchant checkout (API version v-0.2.0).

Nagad keeps its official spec in the merchant integration pack. This driver
follows the flow several open-source SDKs agree on:
  initialize -> complete -> customer pays on callBackUrl -> verify.
sensitiveData is RSA-encrypted (PKCS#1 v1.5) with Nagad's public key and signed
(SHA256withRSA) with your merchant private key. Production needs your server
IP and callback domain whitelisted by Nagad. Check every value against the pack
Nagad sends you; base URLs are configurable for that reason.
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {
    "sandbox": "http://sandbox.mynagad.com:10080/remote-payment-gateway-1.0/api/dfs",
    "live": "https://api.mynagad.com/api/dfs",
}
DHAKA = timezone(timedelta(hours=6))      # Bangladesh has no daylight saving time


def _pem(text: str, kind: str) -> bytes:
    text = text.strip()
    if "BEGIN" in text:
        return text.encode()
    body = "\n".join(text[i:i + 64] for i in range(0, len(text), 64))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n".encode()


def load_private_key(text: str):
    try:
        return serialization.load_pem_private_key(_pem(text, "PRIVATE KEY"), password=None)
    except ValueError:
        return serialization.load_pem_private_key(_pem(text, "RSA PRIVATE KEY"), password=None)


def load_public_key(text: str):
    return serialization.load_pem_public_key(_pem(text, "PUBLIC KEY"))


def _dumps(d: dict) -> str:
    return json.dumps(d, separators=(",", ":"))


class NagadDriver(Driver):
    id = "nagad"
    label = "Nagad"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("NAGAD_MERCHANT_ID", "NAGAD_PRIVATE_KEY_PATH", "NAGAD_PUBLIC_KEY_PATH")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("NAGAD_BASE_URL") or BASE[self.env]).rstrip("/")
        self.mid = cfg["NAGAD_MERCHANT_ID"]
        self.merchant_number = cfg.get("NAGAD_MERCHANT_NUMBER")
        try:
            self.private_key = load_private_key(Path(cfg["NAGAD_PRIVATE_KEY_PATH"]).read_text())
            self.nagad_public = load_public_key(Path(cfg["NAGAD_PUBLIC_KEY_PATH"]).read_text())
        except (OSError, ValueError) as e:
            raise self.fail("CONFIG", f"cannot load Nagad keys: {e}")
        self.client_ip = cfg.get("NAGAD_CLIENT_IP", "127.0.0.1")

    # ------------------------------------------------------------- crypto
    def encrypt(self, data: dict) -> str:
        return base64.b64encode(self.nagad_public.encrypt(_dumps(data).encode(), padding.PKCS1v15())).decode()

    def sign(self, data: dict) -> str:
        return base64.b64encode(self.private_key.sign(_dumps(data).encode(), padding.PKCS1v15(),
                                                      hashes.SHA256())).decode()

    def decrypt(self, b64: str) -> str:
        return self.private_key.decrypt(base64.b64decode(b64), padding.PKCS1v15()).decode()

    def verify_signature(self, plaintext: str, b64sig: str) -> bool:
        try:
            self.nagad_public.verify(base64.b64decode(b64sig), plaintext.encode(), padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    def _headers(self, order: Order) -> dict:
        return {"Content-Type": "application/json", "X-KM-Api-Version": "v-0.2.0",
                "X-KM-Client-Type": "PC_WEB", "X-KM-IP-V4": order.customer.get("ip") or self.client_ip}

    # ------------------------------------------------------------- payments
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        if not order.order_id.isalnum() or len(order.order_id) > 20:
            raise self.fail("VALIDATION", "Nagad order ids must be letters/digits only, max 20 characters")
        now = datetime.now(DHAKA).strftime("%Y%m%d%H%M%S")
        sensitive = {"merchantId": self.mid, "datetime": now, "orderId": order.order_id,
                     "challenge": secrets.token_hex(20)}
        body = {"dateTime": now, "sensitiveData": self.encrypt(sensitive), "signature": self.sign(sensitive)}
        if self.merchant_number:
            body["accountNumber"] = self.merchant_number
        j = self.http.json(self.id, "POST", f"{self.base}/check-out/initialize/{self.mid}/{order.order_id}",
                           json=body, headers=self._headers(order))
        if not j.get("sensitiveData"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), j.get("reason"))
        plain = self.decrypt(j["sensitiveData"])
        if j.get("signature") and not self.verify_signature(plain, j["signature"]):
            raise self.fail("SIGNATURE_INVALID", "initialize response signature is not from Nagad")
        init = json.loads(plain)
        ref, challenge = init["paymentReferenceId"], init["challenge"]
        sensitive2 = {"merchantId": self.mid, "orderId": order.order_id, "amount": order.amount,
                      "currencyCode": "050", "challenge": challenge}
        body2 = {"sensitiveData": self.encrypt(sensitive2), "signature": self.sign(sensitive2),
                 "merchantCallbackURL": urls.success,
                 "additionalMerchantInfo": {"orderId": order.order_id}}
        j2 = self.http.json(self.id, "POST", f"{self.base}/check-out/complete/{ref}", json=body2,
                            headers=self._headers(order))
        if (j2.get("status") or "").lower() != "success" or not j2.get("callBackUrl"):
            raise self.fail("PROVIDER_DECLINED", j2.get("message") or str(j2), j2.get("reason"))
        return PaymentSession(ref, j2["callBackUrl"], {"initialize": init, "complete": j2})

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        ref = params.get("payment_ref_id") or order.provider_payment_id
        if order.provider_payment_id and ref != order.provider_payment_id:
            raise self.fail("VALIDATION", "payment_ref_id does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self.http.json(self.id, "GET", f"{self.base}/verify/payment/{order.provider_payment_id}",
                           headers=self._headers(order), retry=True)
        st = (j.get("status") or "").lower()
        status = {"success": Status.PAID, "inprogress": Status.PENDING, "ready": Status.PENDING,
                  "cancelled": Status.CANCELLED, "aborted": Status.CANCELLED,
                  "expired": Status.EXPIRED}.get(st, Status.FAILED)
        return Verified(status, to_poisha(j["amount"]) if j.get("amount") else None, None,
                        j.get("orderId"), order.provider_payment_id, j.get("issuerPaymentRefNo"), j)
