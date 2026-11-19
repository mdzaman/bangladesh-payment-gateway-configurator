"""Webhook endpoints and delivery.

Events are created by ``services.events.emit`` inside the transaction that
changed something. This module delivers them: at least once, signed, with
exponential backoff, from any number of workers (``FOR UPDATE SKIP LOCKED``).

Signature (the same scheme as most payment APIs, so existing verification
code fits)::

    BDPay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>

Receivers must verify the signature against the raw body, reject timestamps
older than five minutes, and treat the event id as an idempotency key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..context import Actor, Scope
from ..crypto import aad_webhook_secret
from ..db import utcnow
from ..engine.orders import iso
from ..engine.runtime import Runtime
from ..errors import ServiceError
from ..ids import new_id, new_secret
from ..models import Event, WebhookDelivery, WebhookEndpoint
from ..security.netguard import GuardedTransport, check_public_url
from . import audit, events

log = logging.getLogger("bdpay_cloud.webhooks")

# Seconds to wait after the 1st, 2nd, ... failed attempt. 9 attempts over ~46 hours.
RETRY_SCHEDULE = (10, 60, 300, 1800, 7200, 21600, 43200, 86400)
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1
LEASE_SECONDS = 90
TOLERANCE_SECONDS = 300
USER_AGENT = "bdpay-cloud-webhooks/1.0"


class WebhookError(ServiceError):
    def __init__(self, message: str, status: int = 400, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


# ------------------------------------------------------------------ signing
def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(
    secret: str, header: str, body: bytes, tolerance: int = TOLERANCE_SECONDS, now: float | None = None
) -> bool:
    """Reference verification, the logic a receiver should implement."""
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        timestamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


# ---------------------------------------------------------------- endpoints
def endpoint_json(e: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": e.id,
        "url": e.url,
        "description": e.description,
        "event_types": list(e.event_types),
        "enabled": e.enabled,
        "consecutive_failures": e.consecutive_failures,
        "last_success_at": iso(e.last_success_at),
        "last_failure_at": iso(e.last_failure_at),
        "created_at": iso(e.created_at),
    }


def _clean_types(event_types: list[str] | None) -> list[str]:
    types = list(dict.fromkeys(event_types or ["*"]))
    if "*" in types:
        return ["*"]
    unknown = [t for t in types if t not in events.EVENT_TYPES]
    if unknown:
        raise WebhookError(f"Unknown event type: {', '.join(unknown)}")
    return types


def _clean_url(rt: Runtime, scope: Scope, url: str) -> str:
    try:
        return check_public_url(
            url, allow_http=(scope.mode == "sandbox"), allow_private=rt.settings.allow_private_targets
        )
    except ValueError as e:
        raise WebhookError(f"Endpoint URL {e}") from None


def _get(db: Session, scope: Scope, endpoint_id: str, lock: bool = False) -> WebhookEndpoint:
    q = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.workspace_id == scope.workspace_id,
        WebhookEndpoint.mode == scope.mode,
    )
    e = db.scalar(q.with_for_update() if lock else q)
    if e is None:
        raise WebhookError("No such webhook endpoint", 404, "NOT_FOUND")
    return e


def list_endpoints(db: Session, scope: Scope) -> list[WebhookEndpoint]:
    return list(
        db.scalars(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.workspace_id == scope.workspace_id, WebhookEndpoint.mode == scope.mode)
            .order_by(WebhookEndpoint.created_at)
        )
    )


def create_endpoint(
    db: Session,
    rt: Runtime,
    scope: Scope,
    *,
    url: str,
    description: str,
    event_types: list[str] | None,
    actor: Actor,
    user_id: str | None,
) -> tuple[WebhookEndpoint, str]:
    count = db.scalar(
        select(func.count())
        .select_from(WebhookEndpoint)
        .where(WebhookEndpoint.workspace_id == scope.workspace_id, WebhookEndpoint.mode == scope.mode)
    )
    if count >= rt.settings.max_webhook_endpoints_per_env:
        raise WebhookError(f"At most {rt.settings.max_webhook_endpoints_per_env} endpoints per environment")
    endpoint_id, secret = new_id("wh"), "whsec_" + new_secret(24)
    e = WebhookEndpoint(
        id=endpoint_id,
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        url=_clean_url(rt, scope, url),
        description=(description or "").strip()[:200],
        secret_enc=rt.vault.encrypt(secret, aad_webhook_secret(endpoint_id)),
        event_types=_clean_types(event_types),
        created_by=user_id,
    )
    db.add(e)
    db.flush()
    audit.record(
        db,
        actor,
        "webhook.created",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="webhook_endpoint",
        target_id=e.id,
        detail={"url": e.url},
    )
    return e, secret


def update_endpoint(
    db: Session,
    rt: Runtime,
    scope: Scope,
    endpoint_id: str,
    *,
    url: str | None,
    description: str | None,
    event_types: list[str] | None,
    enabled: bool | None,
    actor: Actor,
) -> WebhookEndpoint:
    e = _get(db, scope, endpoint_id, lock=True)
    if url is not None:
        e.url = _clean_url(rt, scope, url)
    if description is not None:
        e.description = description.strip()[:200]
    if event_types is not None:
        e.event_types = _clean_types(event_types)
    if enabled is not None:
        e.enabled = enabled
        if enabled:
            e.consecutive_failures = 0
    e.updated_at = utcnow()
    db.flush()
    audit.record(
        db,
        actor,
        "webhook.updated",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="webhook_endpoint",
        target_id=e.id,
    )
    return e


def rotate_secret(
    db: Session, rt: Runtime, scope: Scope, endpoint_id: str, actor: Actor
) -> tuple[WebhookEndpoint, str]:
    e = _get(db, scope, endpoint_id, lock=True)
    secret = "whsec_" + new_secret(24)
    e.secret_enc, e.updated_at = rt.vault.encrypt(secret, aad_webhook_secret(e.id)), utcnow()
    db.flush()
    audit.record(
        db,
        actor,
        "webhook.secret_rotated",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="webhook_endpoint",
        target_id=e.id,
    )
    return e, secret


def delete_endpoint(db: Session, scope: Scope, endpoint_id: str, actor: Actor) -> None:
    e = _get(db, scope, endpoint_id)
    db.delete(e)
    db.flush()
    audit.record(
        db,
        actor,
        "webhook.deleted",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="webhook_endpoint",
        target_id=endpoint_id,
    )


def send_test(db: Session, scope: Scope, endpoint_id: str) -> str:
    """Queue a ``ping`` event for one endpoint. Returns the event id."""
    e = _get(db, scope, endpoint_id)
    return events.emit(
        db.connection(),
        scope,
        "ping",
        {"message": "Test event from the dashboard", "endpoint_id": e.id},
        only_endpoint=e.id,
    )


def redeliver(db: Session, scope: Scope, delivery_id: str) -> str:
    d = db.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.workspace_id == scope.workspace_id,
            WebhookDelivery.mode == scope.mode,
        )
    )
    if d is None:
        raise WebhookError("No such delivery", 404, "NOT_FOUND")
    new = WebhookDelivery(
        id=new_id("whd", 24), event_id=d.event_id, endpoint_id=d.endpoint_id, workspace_id=d.workspace_id, mode=d.mode
    )
    db.add(new)
    db.flush()
    return new.id


# ------------------------------------------------------------------ listings
def delivery_json(d: WebhookDelivery, event_type: str | None = None) -> dict[str, Any]:
    return {
        "id": d.id,
        "event_id": d.event_id,
        "event_type": event_type,
        "endpoint_id": d.endpoint_id,
        "status": d.status,
        "attempts": d.attempts,
        "next_attempt_at": iso(d.next_attempt_at),
        "last_attempt_at": iso(d.last_attempt_at),
        "last_status_code": d.last_status_code,
        "last_error": d.last_error,
        "last_response": d.last_response,
        "duration_ms": d.duration_ms,
        "created_at": iso(d.created_at),
        "delivered_at": iso(d.delivered_at),
    }


def list_deliveries(
    db: Session,
    scope: Scope,
    *,
    endpoint_id: str | None = None,
    status: str | None = None,
    event_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = (
        select(WebhookDelivery, Event.type)
        .join(Event, Event.id == WebhookDelivery.event_id)
        .where(WebhookDelivery.workspace_id == scope.workspace_id, WebhookDelivery.mode == scope.mode)
    )
    if endpoint_id:
        q = q.where(WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    if event_id:
        q = q.where(WebhookDelivery.event_id == event_id)
    rows = db.execute(q.order_by(WebhookDelivery.created_at.desc()).limit(limit)).all()
    return [delivery_json(r[0], r[1]) for r in rows]


def list_events(
    db: Session, scope: Scope, *, type_: str | None = None, order_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    q = select(Event).where(Event.workspace_id == scope.workspace_id, Event.mode == scope.mode)
    if type_:
        q = q.where(Event.type == type_)
    if order_id:
        q = q.where(Event.payload["data"]["order"]["order_id"].astext == order_id)
    return [e.payload for e in db.scalars(q.order_by(Event.created_at.desc()).limit(limit))]


def get_event(db: Session, scope: Scope, event_id: str) -> dict[str, Any] | None:
    e = db.scalar(
        select(Event).where(Event.id == event_id, Event.workspace_id == scope.workspace_id, Event.mode == scope.mode)
    )
    return e.payload if e else None


# ------------------------------------------------------------------ delivery
@dataclass
class Claimed:
    id: str
    event_id: str
    endpoint_id: str
    attempts: int


class Dispatcher:
    """Sends due deliveries. One per worker process."""

    def __init__(self, rt: Runtime, transport: httpx.BaseTransport | None = None):
        self.rt = rt
        self.client = httpx.Client(
            transport=transport or GuardedTransport(allow_private=rt.settings.allow_private_targets),
            timeout=httpx.Timeout(rt.settings.webhook_timeout_seconds, connect=5.0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self.client.close()

    def claim(self, limit: int = 20, only_workspace: str | None = None) -> list[Claimed]:
        """Take up to ``limit`` due deliveries. The claim is a lease: if this
        worker dies mid-send, the row becomes due again after LEASE_SECONDS."""
        with self.rt.engine.begin() as conn:
            rows = conn.execute(
                text("""
                UPDATE webhook_deliveries d
                   SET attempts = d.attempts + 1, last_attempt_at = now(),
                       next_attempt_at = now() + make_interval(secs => :lease)
                 WHERE d.id IN (SELECT id FROM webhook_deliveries
                                 WHERE status = 'pending' AND next_attempt_at <= now()
                                   AND (CAST(:only AS text) IS NULL OR workspace_id = :only)
                                 ORDER BY next_attempt_at LIMIT :n FOR UPDATE SKIP LOCKED)
                RETURNING d.id, d.event_id, d.endpoint_id, d.attempts"""),
                {"lease": LEASE_SECONDS, "n": limit, "only": only_workspace},
            ).all()
        return [Claimed(r.id, r.event_id, r.endpoint_id, r.attempts) for r in rows]

    def attempt(self, c: Claimed) -> bool:
        with self.rt.engine.connect() as conn:
            r = conn.execute(
                text("""
                SELECT e.url, e.secret_enc, e.enabled, v.payload, v.type
                  FROM webhook_endpoints e, events v WHERE e.id = :ep AND v.id = :ev"""),
                {"ep": c.endpoint_id, "ev": c.event_id},
            ).one_or_none()
        if r is None or not r.enabled:
            self._finish(c, ok=False, final=True, error="endpoint is disabled or was deleted")
            return False
        body = json.dumps(r.payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        secret = self.rt.vault.decrypt_text(r.secret_enc, aad_webhook_secret(c.endpoint_id))
        headers = {
            "Content-Type": "application/json",
            "BDPay-Signature": sign(secret, int(time.time()), body),
            "BDPay-Event-Id": c.event_id,
            "BDPay-Event-Type": r.type,
            "BDPay-Delivery-Id": c.id,
            "BDPay-Delivery-Attempt": str(c.attempts),
        }
        started = time.monotonic()
        code: int | None = None
        error: str | None = None
        snippet: str | None = None
        try:
            resp = self.client.post(r.url, content=body, headers=headers)
            code, snippet = resp.status_code, resp.text[:500]
            if not 200 <= code < 300:
                error = f"HTTP {code}"
        except httpx.TimeoutException:
            error = "timed out"
        except httpx.HTTPError as e:
            error = f"{e.__class__.__name__}: {e}"[:300]
        ok = error is None
        self._finish(
            c,
            ok=ok,
            final=ok or c.attempts >= MAX_ATTEMPTS,
            error=error,
            code=code,
            snippet=snippet,
            ms=int((time.monotonic() - started) * 1000),
        )
        return ok

    def _finish(
        self,
        c: Claimed,
        *,
        ok: bool,
        final: bool,
        error: str | None = None,
        code: int | None = None,
        snippet: str | None = None,
        ms: int | None = None,
    ) -> None:
        delay = None
        if not ok and not final:
            base = RETRY_SCHEDULE[min(c.attempts, len(RETRY_SCHEDULE)) - 1]
            delay = base * random.uniform(0.9, 1.1)  # noqa: S311 - jitter, not a secret
        with self.rt.engine.begin() as conn:
            conn.execute(
                text("""
                UPDATE webhook_deliveries
                   SET status = :status, last_status_code = :code, last_error = :error, last_response = :snippet,
                       duration_ms = :ms, delivered_at = CASE WHEN :ok THEN now() END,
                       next_attempt_at = CASE WHEN CAST(:delay AS float8) IS NULL THEN NULL
                                              ELSE now() + make_interval(secs => CAST(:delay AS float8)) END
                 WHERE id = :id"""),
                {
                    "id": c.id,
                    "status": "succeeded" if ok else ("failed" if final else "pending"),
                    "code": code,
                    "error": error,
                    "snippet": snippet,
                    "ms": ms,
                    "ok": ok,
                    "delay": delay,
                },
            )
            if ok:
                conn.execute(
                    text(
                        "UPDATE webhook_endpoints SET consecutive_failures = 0, last_success_at = now() WHERE id = :id"
                    ),
                    {"id": c.endpoint_id},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE webhook_endpoints SET consecutive_failures = consecutive_failures + 1, "
                        "last_failure_at = now() WHERE id = :id"
                    ),
                    {"id": c.endpoint_id},
                )

    def run_once(self, limit: int = 20, only_workspace: str | None = None, pool=None) -> int:
        claimed = self.claim(limit, only_workspace)
        if pool is not None and len(claimed) > 1:
            list(pool.map(self._safe_attempt, claimed))
        else:
            for c in claimed:
                self._safe_attempt(c)
        return len(claimed)

    def _safe_attempt(self, c: Claimed) -> None:
        try:
            self.attempt(c)
        except Exception:
            log.exception("webhook delivery %s crashed; it will be retried after its lease", c.id)
