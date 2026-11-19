"""Two-factor sign-in codes: TOTP (RFC 6238) for authenticator apps, and recovery codes.

Standard library only. The parameters are the ones every authenticator app
supports and most of them assume: HMAC-SHA1, six digits, a 30-second step.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import unicodedata
from urllib.parse import quote, urlencode

from ..ids import sha256_hex

DIGITS = 6
PERIOD = 30
# Steps accepted either side of the current one: a phone clock up to 30 seconds
# out still works, and a code typed just as it changes is not refused.
WINDOW = 1
SECRET_BYTES = 20  # 160 bits, the size RFC 4226 recommends

RECOVERY_CODES = 10
# Crockford base32 (no i, l, o, u), 16 characters: 80 bits. That is enough to
# store a recovery code as a plain SHA-256, like API keys and session tokens.
_RECOVERY_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_RECOVERY_LENGTH = 16
_LOOKALIKES = str.maketrans("oil", "011")


def unix_time() -> float:
    """The clock, in one place so tests can move it."""
    return time.time()


def current_step(at: float | None = None) -> int:
    return int((unix_time() if at is None else at) // PERIOD)


def new_secret() -> str:
    """Base32 text, the form authenticator apps take when typed by hand."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _key(secret: str) -> bytes:
    text = secret.replace(" ", "").upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def clean_code(code: str) -> str:
    """What the person typed, without the spaces and hyphens that apps and
    people add, and with any script's digits (Bangla ones included) as 0-9."""
    out = []
    for ch in code or "":
        if ch.isspace() or ch == "-":
            continue
        out.append(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch)
    return "".join(out)


def looks_like_totp(code: str) -> bool:
    cleaned = clean_code(code)
    return len(cleaned) == DIGITS and cleaned.isascii() and cleaned.isdigit()


def verify(secret: str, code: str, *, last_step: int | None = None, at: float | None = None) -> int | None:
    """The time step ``code`` belongs to, or None if it is not valid now.

    A step at or before ``last_step`` is refused, so each code works once: store
    the returned step and pass it back next time. Every candidate step is
    compared, in constant time, whether or not an earlier one matched."""
    if not looks_like_totp(code):
        return None
    candidate = clean_code(code)
    now = current_step(at)
    matched: int | None = None
    for step in range(now - WINDOW, now + WINDOW + 1):
        same = hmac.compare_digest(code_at(secret, step), candidate)
        if same and matched is None and (last_step is None or step > last_step):
            matched = step
    return matched


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """The ``otpauth://`` link an authenticator app reads from a QR code."""
    label = f"{quote(issuer, safe='')}:{quote(account, safe='')}"
    return f"otpauth://totp/{label}?{urlencode({'secret': secret, 'issuer': issuer}, quote_via=quote)}"


# ------------------------------------------------------------ recovery codes
def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """``xxxx-xxxx-xxxx-xxxx``, shown once and stored only as hashes."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
        codes.append("-".join(raw[i : i + 4] for i in range(0, _RECOVERY_LENGTH, 4)))
    return codes


def normalize_recovery_code(code: str) -> str:
    """Case, spacing and hyphens do not matter, and neither does reading 0 as O or 1 as l."""
    return clean_code(code).lower().translate(_LOOKALIKES)


def recovery_hash(user_id: str, code: str) -> str:
    return sha256_hex(f"recovery-code:{user_id}:{normalize_recovery_code(code)}")
