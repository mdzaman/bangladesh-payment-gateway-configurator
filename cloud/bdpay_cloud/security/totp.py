"""Time-based one-time passwords (RFC 6238) for authenticator apps.

Six digits, 30-second steps, HMAC-SHA1: the one profile every authenticator
app implements, and the default of the ``otpauth://`` key format. Standard
library only.

A code is the HOTP value (RFC 4226) of the counter ``floor(unix time / 30)``.
``match`` accepts the current step and one either side, to allow for a phone
whose clock is a little off and for the seconds spent typing. It reports
*which* step matched, so the caller can refuse a step it has already accepted
(a code is good once).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
import unicodedata
from urllib.parse import quote

DIGITS = 6
PERIOD = 30  # seconds per step
WINDOW = 1  # steps accepted either side of the current one
SECRET_BYTES = 20  # 160 bits, the length RFC 4226 recommends


def now() -> float:
    """The clock codes are checked against. A function so tests can move it."""
    return time.time()


def new_secret() -> str:
    """A fresh shared secret in base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _key(secret: str) -> bytes:
    cleaned = "".join(secret.split()).replace("-", "").upper()
    try:
        return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError) as e:
        raise ValueError("the secret is not valid base32") from e


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226: HMAC the counter, take 31 bits at an offset the digest itself
    names (dynamic truncation), keep the low decimal digits."""
    # HMAC-SHA1 is what RFC 6238 specifies and what authenticator apps compute.
    # The known weaknesses of SHA-1 are collisions, which HMAC does not rely on.
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def step_at(at: float | None = None) -> int:
    return int((now() if at is None else at) // PERIOD)


def code_at(secret: str, at: float | None = None) -> str:
    """The code an authenticator app shows for this secret at that moment."""
    return hotp(_key(secret), step_at(at))


def clean_code(code: str) -> str | None:
    """What someone typed -> six ASCII digits, or None if it cannot be a code.
    Spaces are dropped ("123 456") and digits of any script are accepted
    (a Bangla keyboard types ১২৩৪৫৬)."""
    out = []
    for ch in code or "":
        if ch.isspace():
            continue
        if not ch.isdecimal():
            return None
        out.append(str(unicodedata.decimal(ch)))
    return "".join(out) if len(out) == DIGITS else None


def match(secret: str, code: str, at: float | None = None) -> int | None:
    """The time-step whose code this is, looking one step either side of ``at``,
    or None. Every candidate is compared, in constant time, so the answer takes
    as long whichever step matches or none."""
    key, current = _key(secret), step_at(at)
    found: int | None = None
    for step in range(max(0, current - WINDOW), current + WINDOW + 1):
        if hmac.compare_digest(hotp(key, step), code):
            found = step  # the latest one, in the unlikely case two steps share a code
    return found


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://`` link an authenticator app reads from a QR code
    (the "Key URI Format" that Google Authenticator introduced)."""
    # The label is "issuer:account"; a colon inside the issuer would split it in the wrong place.
    issuer = quote(" ".join(issuer.replace(":", " ").split()) or "bdpay cloud", safe="")
    return (
        f"otpauth://totp/{issuer}:{quote(account, safe='')}?secret={secret}&issuer={issuer}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )
