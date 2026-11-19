"""Workspace audit log: who changed what. Detail is redacted before it is stored."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdpay.http import redact

from ..context import Actor
from ..engine.orders import iso
from ..models import AuditLog


def record(
    db: Session,
    actor: Actor,
    action: str,
    *,
    workspace_id: str | None = None,
    mode: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    clean = json.loads(json.dumps(redact(detail or {}), default=str))
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            mode=mode,
            actor_type=actor.type,
            actor_id=actor.id,
            actor_label=actor.label[:200],
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip=actor.ip,
            detail=clean,
        )
    )


def listing(db: Session, workspace_id: str, *, limit: int = 50, before: int | None = None) -> dict[str, Any]:
    q = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if before:
        q = q.where(AuditLog.id < before)
    rows = list(db.scalars(q.order_by(AuditLog.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "data": [
            {
                "id": r.id,
                "at": iso(r.at),
                "mode": r.mode,
                "actor_type": r.actor_type,
                "actor": r.actor_label,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "ip": r.ip,
                "detail": r.detail,
            }
            for r in rows
        ],
        "has_more": more,
        "next_cursor": rows[-1].id if more and rows else None,
    }
