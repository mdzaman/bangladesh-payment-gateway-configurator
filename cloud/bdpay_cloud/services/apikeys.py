"""Merchant API keys. The key is shown once at creation; only its SHA-256 is
stored (the key has ~238 bits of entropy, so a fast hash is the right tool)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..context import Actor, Scope
from ..db import utcnow
from ..engine.orders import iso
from ..engine.runtime import EnvInfo, Runtime
from ..errors import ServiceError
from ..ids import new_api_key, new_id, sha256_hex
from ..models import ApiKey
from ..settings import Settings

ALL_SCOPES = ("orders:read", "orders:write", "refunds:write", "checkout:write", "events:read")


class KeyError_(ServiceError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status
        self.code = "NOT_FOUND" if status == 404 else "VALIDATION"


@dataclass(frozen=True)
class KeyAuth:
    key_id: str
    name: str
    scope: Scope
    scopes: tuple[str, ...]
    env: EnvInfo

    def allows(self, needed: str) -> bool:
        return "*" in self.scopes or needed in self.scopes

    @property
    def actor(self) -> Actor:
        return Actor("api_key", self.key_id, f"API key {self.name}")


def key_json(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "display": k.display,
        "mode": k.mode,
        "scopes": list(k.scopes),
        "created_at": iso(k.created_at),
        "last_used_at": iso(k.last_used_at),
        "expires_at": iso(k.expires_at),
        "revoked_at": iso(k.revoked_at),
    }


def create_key(
    db: Session,
    settings: Settings,
    scope: Scope,
    *,
    name: str,
    scopes: list[str] | None,
    created_by: str | None,
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 60:
        raise KeyError_("Give the key a name (up to 60 characters)")
    scopes = list(dict.fromkeys(scopes or ["*"]))
    if scopes != ["*"] and any(s not in ALL_SCOPES for s in scopes):
        raise KeyError_(f"Unknown scope. Use '*' or any of: {', '.join(ALL_SCOPES)}")
    active = db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.workspace_id == scope.workspace_id, ApiKey.mode == scope.mode, ApiKey.revoked_at.is_(None))
    )
    if active >= settings.max_api_keys_per_env:
        raise KeyError_(f"At most {settings.max_api_keys_per_env} active keys per environment; revoke one first")
    plaintext = new_api_key(scope.mode)
    key = ApiKey(
        id=new_id("key"),
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        name=name,
        key_hash=sha256_hex(plaintext),
        display=f"{plaintext[:13]}…{plaintext[-4:]}",
        scopes=scopes,
        created_by=created_by,
        expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(key)
    db.flush()
    return key, plaintext


def list_keys(db: Session, scope: Scope) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.workspace_id == scope.workspace_id, ApiKey.mode == scope.mode)
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_key(db: Session, scope: Scope, key_id: str) -> ApiKey:
    key = db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == scope.workspace_id, ApiKey.mode == scope.mode)
    )
    if key is None:
        raise KeyError_("No such API key", 404)
    if key.revoked_at is None:
        key.revoked_at = utcnow()
    return key


def authenticate(rt: Runtime, plaintext: str) -> KeyAuth | None:
    """Resolve an API key to its tenant. None for anything that is not a live key."""
    if not plaintext or not plaintext.startswith("bdp_") or len(plaintext) > 100:
        return None
    with rt.engine.connect() as conn:
        r = conn.execute(
            text("""
            SELECT id, workspace_id, mode, name, scopes, last_used_at FROM api_keys
             WHERE key_hash = :h AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())"""),
            {"h": sha256_hex(plaintext)},
        ).one_or_none()
    if r is None:
        return None
    scope = Scope(r.workspace_id, r.mode)
    env = rt.load_env(scope)
    if env is None:
        return None
    if r.last_used_at is None or utcnow() - r.last_used_at > timedelta(seconds=60):
        with rt.engine.begin() as conn:  # at most one write a minute per key
            conn.execute(text("UPDATE api_keys SET last_used_at = now() WHERE id = :id"), {"id": r.id})
    return KeyAuth(r.id, r.name, scope, tuple(r.scopes or ()), env)
