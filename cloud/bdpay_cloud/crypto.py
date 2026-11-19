"""Encryption of tenant secrets at rest.

Gateway credentials, callback signing secrets, webhook secrets and cached
provider tokens are stored as AES-256-GCM ciphertext. Keys come from a key
ring in ``BDPAY_CLOUD_MASTER_KEYS``: the first key encrypts, every key can
decrypt, which is what makes rotation possible without downtime.

Every ciphertext is bound to where it lives through the AEAD associated data
(for example ``gateway:<workspace>:<mode>:<gateway>``). A ciphertext copied
into another row, or another tenant's row, fails to decrypt.

Token format::

    v1.<key id>.<base64url nonce>.<base64url ciphertext+tag>
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(Exception):
    pass


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_key_ring(spec: str) -> dict[str, bytes]:
    """``"k1:<base64 32 bytes>,k0:<base64 32 bytes>"`` -> ordered ``{id: key}``."""
    ring: dict[str, bytes] = {}
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        key_id, sep, b64 = part.partition(":")
        key_id = key_id.strip()
        if not sep or not key_id or "." in key_id:
            raise VaultError("each master key must look like '<id>:<base64 key>' (id without dots)")
        try:
            key = base64.b64decode(b64.strip() + "=" * (-len(b64.strip()) % 4), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as e:
            raise VaultError(f"master key '{key_id}' is not valid base64") from e
        if len(key) != 32:
            raise VaultError(f"master key '{key_id}' must be 32 bytes, got {len(key)}")
        if key_id in ring:
            raise VaultError(f"master key id '{key_id}' appears twice")
        ring[key_id] = key
    if not ring:
        raise VaultError("no master key configured")
    return ring


def generate_key_entry(key_id: str) -> str:
    return f"{key_id}:{base64.b64encode(os.urandom(32)).decode()}"


class Vault:
    def __init__(self, ring: dict[str, bytes]):
        if not ring:
            raise VaultError("no master key configured")
        self._ring = dict(ring)
        self.primary = next(iter(ring))

    @classmethod
    def from_spec(cls, spec: str) -> Vault:
        return cls(parse_key_ring(spec))

    @property
    def key_ids(self) -> list[str]:
        return list(self._ring)

    # ------------------------------------------------------------------ bytes
    def encrypt(self, plaintext: bytes | str, aad: str) -> str:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        nonce = os.urandom(12)
        ct = AESGCM(self._ring[self.primary]).encrypt(nonce, plaintext, aad.encode("utf-8"))
        return f"v1.{self.primary}.{_b64e(nonce)}.{_b64e(ct)}"

    def decrypt(self, token: str, aad: str) -> bytes:
        try:
            version, key_id, nonce_b64, ct_b64 = token.split(".")
        except (ValueError, AttributeError) as e:
            raise VaultError("malformed ciphertext") from e
        if version != "v1":
            raise VaultError(f"unsupported ciphertext version {version!r}")
        key = self._ring.get(key_id)
        if key is None:
            raise VaultError(f"ciphertext was written with key '{key_id}', which is not in the key ring")
        try:
            return AESGCM(key).decrypt(_b64d(nonce_b64), _b64d(ct_b64), aad.encode("utf-8"))
        except (InvalidTag, ValueError, binascii.Error) as e:
            raise VaultError("ciphertext failed authentication (wrong key, wrong record, or tampered)") from e

    def decrypt_text(self, token: str, aad: str) -> str:
        return self.decrypt(token, aad).decode("utf-8")

    # ------------------------------------------------------------------- json
    def encrypt_json(self, value: Any, aad: str) -> str:
        return self.encrypt(json.dumps(value, separators=(",", ":"), sort_keys=True), aad)

    def decrypt_json(self, token: str | None, aad: str) -> Any:
        if not token:
            return {}
        return json.loads(self.decrypt(token, aad))

    # --------------------------------------------------------------- rotation
    def key_id_of(self, token: str) -> str:
        parts = token.split(".")
        return parts[1] if len(parts) == 4 else ""

    def needs_rotation(self, token: str | None) -> bool:
        return bool(token) and self.key_id_of(token) != self.primary

    def rotate(self, token: str, aad: str) -> str:
        return self.encrypt(self.decrypt(token, aad), aad)


# Associated-data builders: one place decides how a ciphertext is bound to its row.
def aad_gateway(workspace_id: str, mode: str, gateway: str) -> str:
    return f"gateway:{workspace_id}:{mode}:{gateway}"


def aad_callback_secret(workspace_id: str, mode: str) -> str:
    return f"callback-secret:{workspace_id}:{mode}"


def aad_webhook_secret(endpoint_id: str) -> str:
    return f"webhook-secret:{endpoint_id}"


def aad_provider_token(key: str) -> str:
    return f"provider-token:{key}"
