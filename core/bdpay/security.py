"""Signed, expiring callback URLs. A forged or replayed return URL for another
order fails here before any provider call is made."""
from __future__ import annotations

import hashlib
import hmac
import time


def sign_callback(secret: str, gateway: str, order_id: str, ttl_seconds: int = 86400) -> str:
    exp = int(time.time()) + ttl_seconds
    mac = hmac.new(secret.encode(), f"{gateway}|{order_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{mac}"


def check_callback(secret: str, gateway: str, order_id: str, token: str) -> bool:
    try:
        exp_s, mac = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    good = hmac.new(secret.encode(), f"{gateway}|{order_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, mac)
