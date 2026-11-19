"""Reconciliation: re-check payments left pending.

A customer can pay and then close the tab, lose signal, or the provider's
redirect can simply never arrive. The provider still knows. Every pending
order is therefore asked about again on a schedule until it reaches a final
state; after ``reconcile_expire_hours`` it is marked expired, exactly as the
reference ``reconcile`` does.

Unlike the reference (one tenant, one loop), due orders of *all* workspaces
are claimed with ``FOR UPDATE SKIP LOCKED``, so any number of workers can run
side by side without checking the same order twice.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import text

from bdpay.core import BdPayError, Status

from .. import metrics
from ..context import Scope
from ..engine.runtime import Runtime

log = logging.getLogger("bdpay_cloud.reconcile")

# Wait before the 2nd, 3rd, ... check (the first is scheduled when the order
# becomes pending). Dense early, when a paid-but-lost redirect is most likely.
_BACKOFF = """CASE WHEN o.check_count < 1 THEN interval '3 minutes'
                   WHEN o.check_count < 3 THEN interval '5 minutes'
                   WHEN o.check_count < 6 THEN interval '10 minutes'
                   WHEN o.check_count < 10 THEN interval '30 minutes'
                   ELSE interval '1 hour' END"""


@dataclass
class Due:
    workspace_id: str
    mode: str
    order_id: str
    created_ts: float


def claim(rt: Runtime, limit: int = 25, only_workspace: str | None = None) -> list[Due]:
    with rt.engine.begin() as conn:
        rows = conn.execute(
            text(f"""
            UPDATE orders o
               SET check_count = o.check_count + 1, next_check_at = now() + {_BACKOFF}
             WHERE o.id IN (SELECT id FROM orders
                             WHERE status = 'pending' AND next_check_at IS NOT NULL AND next_check_at <= now()
                               AND (CAST(:only AS text) IS NULL OR workspace_id = :only)
                             ORDER BY next_check_at LIMIT :n FOR UPDATE SKIP LOCKED)
            RETURNING o.workspace_id, o.mode, o.order_id, extract(epoch FROM o.created_at)::float8 AS created_ts"""),
            {"n": limit, "only": only_workspace},
        ).all()
    return [Due(r.workspace_id, r.mode, r.order_id, r.created_ts) for r in rows]


def check(rt: Runtime, due: Due) -> str:
    """Ask the provider about one order. Returns the resulting status (or ``error:CODE``)."""
    try:
        pay = rt.pay(Scope(due.workspace_id, due.mode))
        updated = pay.requery(due.order_id)
        if (
            updated.status == Status.PENDING
            and due.created_ts < time.time() - rt.settings.reconcile_expire_hours * 3600
        ):
            updated, _ = pay.store.transition(due.order_id, Status.EXPIRED, {"reason": "reconcile timeout"})
        result = updated.status.value
    except BdPayError as e:
        log.warning("reconcile %s/%s %s: %s", due.workspace_id, due.mode, due.order_id, e)
        result = f"error:{e.code}"
    except Exception:
        log.exception("reconcile %s/%s %s crashed", due.workspace_id, due.mode, due.order_id)
        result = "error:INTERNAL"
    metrics.RECONCILE_CHECKS.labels(result.split(":")[0]).inc()
    return result


def run_once(rt: Runtime, limit: int = 25, only_workspace: str | None = None, pool=None) -> list[tuple[str, str]]:
    due = claim(rt, limit, only_workspace)
    if pool is not None and len(due) > 1:
        results = list(pool.map(lambda d: check(rt, d), due))
    else:
        results = [check(rt, d) for d in due]
    return [(d.order_id, r) for d, r in zip(due, results, strict=True)]
