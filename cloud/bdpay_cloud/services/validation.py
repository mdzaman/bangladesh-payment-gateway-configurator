"""Input rules shared by the merchant API, the dashboard and hosted checkout."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import quote

from bdpay.core import BdPayError, to_poisha

from ..security.netguard import check_public_url
from ..settings import Settings

MAX_AMOUNT_POISHA = 100_000_000_00  # BDT 100 million: far above any gateway limit
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_META_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
CUSTOMER_KEYS = ("name", "email", "phone", "address", "city", "postcode", "product", "category", "ip")


def invalid(message: str) -> BdPayError:
    return BdPayError("VALIDATION", message)


def clean_amount(amount: Any) -> int:
    if isinstance(amount, bool):
        raise invalid("invalid amount")
    poisha = to_poisha(amount)
    if poisha <= 0:
        raise invalid("amount must be greater than zero")
    if poisha > MAX_AMOUNT_POISHA:
        raise invalid("amount is larger than this service accepts")
    return poisha


def clean_order_id(order_id: str) -> str:
    if not isinstance(order_id, str) or not ORDER_ID_RE.match(order_id):
        raise invalid("order_id must be 1-40 characters: letters, digits, - or _")
    return order_id


def clean_metadata(metadata: Any) -> dict[str, str]:
    if metadata in (None, ""):
        return {}
    if not isinstance(metadata, dict):
        raise invalid("metadata must be an object of string values")
    if len(metadata) > 20:
        raise invalid("metadata can hold at most 20 keys")
    out: dict[str, str] = {}
    for k, v in metadata.items():
        if not isinstance(k, str) or not _META_KEY.match(k):
            raise invalid("metadata keys must be 1-40 characters: letters, digits, _ . -")
        if isinstance(v, (dict, list)):
            raise invalid(f"metadata value for '{k}' must be text")
        text = "" if v is None else str(v)
        if len(text) > 500:
            raise invalid(f"metadata value for '{k}' is longer than 500 characters")
        out[k] = text
    return out


def clean_customer(customer: Any) -> dict[str, str]:
    if customer in (None, ""):
        return {}
    if not isinstance(customer, dict):
        raise invalid("customer must be an object")
    unknown = sorted(set(customer) - set(CUSTOMER_KEYS))
    if unknown:
        raise invalid(f"unknown customer field: {', '.join(map(str, unknown))} (allowed: {', '.join(CUSTOMER_KEYS)})")
    out: dict[str, str] = {}
    for k in CUSTOMER_KEYS:
        v = customer.get(k)
        if v in (None, ""):
            continue
        if isinstance(v, (dict, list)):
            raise invalid(f"customer.{k} must be text")
        text = str(v).strip()
        if len(text) > 200:
            raise invalid(f"customer.{k} is longer than 200 characters")
        out[k] = text
    if "ip" in out:
        try:
            ipaddress.ip_address(out["ip"])
        except ValueError:
            raise invalid("customer.ip is not an IP address") from None
    if "phone" in out and not re.fullmatch(r"[0-9+ ()-]{6,20}", out["phone"]):
        raise invalid("customer.phone does not look like a phone number")
    if "email" in out and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}", out["email"]):
        raise invalid("customer.email does not look like an email address")
    return out


def clean_url(settings: Settings, mode: str, url: Any, what: str) -> str | None:
    """A URL the customer's browser will be sent to. https in live mode."""
    if url in (None, ""):
        return None
    if not isinstance(url, str):
        raise invalid(f"{what} must be a URL")
    try:
        # Browser redirects are not server-side requests, so only the scheme is policed here.
        return check_public_url(url, allow_http=(mode == "sandbox"), allow_private=True)
    except ValueError as e:
        raise invalid(f"{what} {e}") from None


def fill_url(template: str, **values: str) -> str:
    """Substitute ``{order_id}`` style placeholders. Deliberately not str.format,
    which would let a stored template reach attributes of the values."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", quote(str(value), safe=""))
    return template
