"""Tenant events (the transactional outbox).

``emit`` must be called on the *same connection and transaction* as the change
it reports. The event row and one delivery row per subscribed webhook endpoint
are then committed atomically with that change: no event without a change, no
change without its event, and exactly one event per change even when a provider
callback and an IPN race on two replicas.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, text

from ..context import Scope
from ..db import utcnow
from ..engine.orders import iso
from ..ids import new_id

# What a webhook endpoint can subscribe to.
EVENT_TYPES = (
    "order.paid",
    "order.failed",
    "order.cancelled",
    "order.expired",
    "order.needs_review",
    "order.partially_refunded",
    "order.refunded",
    "checkout.session.completed",
    "checkout.session.extra_payment",
    "ping",
)

ORDER_STATUS_EVENTS = {
    "paid": "order.paid",
    "failed": "order.failed",
    "cancelled": "order.cancelled",
    "expired": "order.expired",
    "needs_review": "order.needs_review",
    "partially_refunded": "order.partially_refunded",
    "refunded": "order.refunded",
}

_INSERT_EVENT = text("""
    INSERT INTO events (id, workspace_id, mode, type, order_pk, payload)
    VALUES (:id, :ws, :mode, :type, :order_pk, CAST(:payload AS jsonb))
""")

_FAN_OUT = text("""
    INSERT INTO webhook_deliveries (id, event_id, endpoint_id, workspace_id, mode)
    SELECT 'whd_' || replace(gen_random_uuid()::text, '-', ''), :event_id, e.id, e.workspace_id, e.mode
      FROM webhook_endpoints e
     WHERE e.workspace_id = :ws AND e.mode = :mode AND e.enabled
       AND (e.event_types @> ARRAY['*']::text[] OR e.event_types @> ARRAY[:type]::text[])
       AND (CAST(:only_endpoint AS text) IS NULL OR e.id = :only_endpoint)
""")


def emit(
    conn: Connection,
    scope: Scope,
    type_: str,
    data: dict[str, Any],
    *,
    order_pk: int | None = None,
    only_endpoint: str | None = None,
) -> str:
    event_id = new_id("evt", 24)
    payload = {
        "id": event_id,
        "object": "event",
        "type": type_,
        "mode": scope.mode,
        "created_at": iso(utcnow()),
        "data": data,
    }
    conn.execute(
        _INSERT_EVENT,
        {
            "id": event_id,
            **scope.params,
            "type": type_,
            "order_pk": order_pk,
            "payload": json.dumps(payload, default=str),
        },
    )
    conn.execute(_FAN_OUT, {"event_id": event_id, **scope.params, "type": type_, "only_endpoint": only_endpoint})
    return event_id
