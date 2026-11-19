"""Identifiers and random tokens.

Public identifiers carry a short prefix so a value pasted into a ticket or a
log line says what it is (``ws_`` workspace, ``cs_`` checkout session, ...).
Everything here comes from the operating system's CSPRNG.
"""

from __future__ import annotations

import hashlib
import secrets
import string

_B32 = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32: no i, l, o, u
_B62 = string.ascii_letters + string.digits


def rand_b32(n: int) -> str:
    return "".join(secrets.choice(_B32) for _ in range(n))


def rand_b62(n: int) -> str:
    return "".join(secrets.choice(_B62) for _ in range(n))


def new_id(prefix: str, n: int = 20) -> str:
    """``prefix_`` + n base32 characters (5 bits each: 20 chars = 100 bits)."""
    return f"{prefix}_{rand_b32(n)}"


def new_public_token(prefix: str) -> str:
    """Unguessable identifier that is safe to put in a URL (130 bits)."""
    return f"{prefix}_{rand_b32(26)}"


def new_secret(n_bytes: int = 32) -> str:
    return secrets.token_hex(n_bytes)


def new_api_key(mode: str) -> str:
    """``bdp_test_...`` for sandbox, ``bdp_live_...`` for live (about 238 bits)."""
    return f"bdp_{'live' if mode == 'live' else 'test'}_{rand_b62(40)}"


def new_order_ref(prefix: str = "CO") -> str:
    """Generated order id (hosted-checkout attempt, dashboard test payment):
    18 letters and digits, which every gateway accepts (Nagad allows at most
    20, letters and digits only)."""
    return prefix + rand_b32(16).upper()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def lock_key(*parts: str) -> int:
    """Stable signed 64-bit key for PostgreSQL advisory locks."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
