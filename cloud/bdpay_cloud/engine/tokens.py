"""Provider access-token cache on PostgreSQL (rule 5 of the build spec).

bKash, shurjoPay, EPS and Upay hand out bearer tokens, and bKash can lock a
merchant that asks for a new one on every request. Tokens are therefore cached,
refreshed five minutes early, and fetched by at most one caller at a time:

* inside a process, a striped lock keeps threads from stampeding;
* across replicas, a PostgreSQL advisory lock does the same.

Cached values are bearer credentials, so they are encrypted at rest.

Cache keys are namespaced by a hash of the *complete* credential set of the
gateway (see ``drivers.credential_namespace``). Two workspaces share a cached
token only if they hold identical credentials, including the secret parts, so
knowing someone's public app key is never enough to reach their token.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Connection, Engine, text

from ..crypto import Vault, VaultError, aad_provider_token
from ..db import utcnow, xact_lock
from ..ids import sha256_hex

_STRIPES = [threading.Lock() for _ in range(256)]


def _stripe(key: str) -> threading.Lock:
    return _STRIPES[int(key[:8], 16) % len(_STRIPES)]


class PgTokenCache:
    def __init__(self, engine: Engine, vault: Vault, early_refresh_seconds: int = 300, namespace: str = ""):
        self.engine = engine
        self.vault = vault
        self.early = early_refresh_seconds
        self.namespace = namespace

    def scoped(self, namespace: str) -> PgTokenCache:
        return PgTokenCache(self.engine, self.vault, self.early, namespace)

    def _k(self, key: str) -> str:
        return sha256_hex(f"{self.namespace}\x1f{key}")

    def _read(self, conn: Connection, k: str) -> str | None:
        r = conn.execute(
            text("SELECT value_enc, expires_at FROM provider_tokens WHERE key = :k"), {"k": k}
        ).one_or_none()
        if r is None or r.expires_at - timedelta(seconds=self.early) <= utcnow():
            return None
        try:
            return self.vault.decrypt_text(r.value_enc, aad_provider_token(k))
        except VaultError:
            return None  # written with a key no longer in the ring: just fetch again

    def get_or_fetch(self, key: str, fetch: Callable[[], tuple[str, int]]) -> str:
        k = self._k(key)
        with self.engine.connect() as conn:
            cached = self._read(conn, k)
        if cached:
            return cached
        with _stripe(k), self.engine.begin() as conn:
            xact_lock(conn, "provider-token", k)
            cached = self._read(conn, k)  # another thread or replica may have refreshed
            if cached:
                return cached
            value, ttl = fetch()
            conn.execute(
                text("""
                INSERT INTO provider_tokens (key, value_enc, expires_at)
                VALUES (:k, :v, now() + make_interval(secs => :ttl))
                ON CONFLICT (key) DO UPDATE SET value_enc = EXCLUDED.value_enc, expires_at = EXCLUDED.expires_at"""),
                {"k": k, "v": self.vault.encrypt(value, aad_provider_token(k)), "ttl": max(60, int(ttl))},
            )
            return value

    def invalidate(self, key: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM provider_tokens WHERE key = :k"), {"k": self._k(key)})
