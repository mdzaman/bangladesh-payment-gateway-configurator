"""``Idempotency-Key`` support for the merchant API.

A client that times out cannot know whether its POST took effect. Sending the
same key again returns the stored response instead of acting twice. Order
creation and payment start are idempotent by design anyway; the case this
really protects is a refund.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text

from ..context import Scope
from ..engine.runtime import Runtime
from ..errors import ServiceError

TAKEOVER_SECONDS = 120


class IdempotencyConflict(ServiceError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status
        self.code = "DUPLICATE" if status == 409 else "VALIDATION"


def fingerprint(method: str, path: str, body: Any) -> str:
    return hashlib.sha256(json.dumps([method, path, body], sort_keys=True, default=str).encode()).hexdigest()


def begin(rt: Runtime, scope: Scope, key: str, request_hash: str) -> tuple[int, Any] | None:
    """None: go ahead. Otherwise the (status, body) to replay."""
    if not 1 <= len(key) <= 200:
        raise IdempotencyConflict("Idempotency-Key must be 1 to 200 characters", 400)
    params = {**scope.params, "key": key, "hash": request_hash}
    with rt.engine.begin() as conn:
        inserted = conn.execute(
            text("""
            INSERT INTO idempotency_keys (workspace_id, mode, key, request_hash) VALUES (:ws, :mode, :key, :hash)
            ON CONFLICT DO NOTHING RETURNING key"""),
            params,
        ).scalar()
        if inserted:
            return None
        row = conn.execute(
            text("""
            SELECT request_hash, status_code, response, completed_at,
                   created_at < now() - make_interval(secs => :takeover) AS stale
              FROM idempotency_keys WHERE workspace_id = :ws AND mode = :mode AND key = :key FOR UPDATE"""),
            {**params, "takeover": TAKEOVER_SECONDS},
        ).one_or_none()
        if row is None:
            return None
        if row.request_hash != request_hash:
            raise IdempotencyConflict("This Idempotency-Key was already used for a different request", 422)
        if row.completed_at is not None:
            return row.status_code, row.response
        if not row.stale:
            raise IdempotencyConflict("A request with this Idempotency-Key is still in progress", 409)
        conn.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() "
                "WHERE workspace_id = :ws AND mode = :mode AND key = :key"
            ),
            params,
        )
        return None


def finish(rt: Runtime, scope: Scope, key: str, status_code: int, body: Any) -> None:
    with rt.engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE idempotency_keys SET status_code = :code, response = CAST(:body AS jsonb), completed_at = now()
             WHERE workspace_id = :ws AND mode = :mode AND key = :key"""),
            {**scope.params, "key": key, "code": status_code, "body": json.dumps(body, default=str)},
        )


def abandon(rt: Runtime, scope: Scope, key: str) -> None:
    """Forget a key whose request did not reach a definite outcome (5xx)."""
    with rt.engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM idempotency_keys WHERE workspace_id = :ws AND mode = :mode AND key = :key "
                "AND completed_at IS NULL"
            ),
            {**scope.params, "key": key},
        )
