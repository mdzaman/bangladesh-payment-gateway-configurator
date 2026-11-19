"""Development helpers. Nothing here runs in production."""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import text

from bdpay.core import BdPayError, Status

from .context import Scope
from .db import utcnow
from .engine.runtime import Runtime

_NAMES = [
    "Rahim Uddin", "Karim Hossain", "Nusrat Jahan", "Tanvir Ahmed", "Sadia Islam", "Mehedi Hasan",
    "Farzana Akter", "Arif Chowdhury", "Sumaiya Rahman", "Imran Khan", "Jannatul Ferdous", "Shakil Mia",
]  # fmt: skip
_AMOUNTS = ["250", "480", "650", "990", "1250", "1500", "1890.50", "2200", "3500", "5200", "780", "12900"]
# outcome, weight
_OUTCOMES = [("paid", 76), ("failed", 8), ("cancelled", 9), ("expired", 4), ("needs_review", 1), ("pending", 2)]


def seed_demo(rt: Runtime, slug: str, count: int = 140, days: int = 30, seed: int = 7) -> dict[str, int]:
    """Fill a workspace's *sandbox* with simulator payments spread over the
    last ``days`` days, so the dashboard has something to show. Every order goes
    through the real engine (create, start, verify, refund); only the timestamps
    are moved back afterwards."""
    if rt.settings.is_production:
        raise RuntimeError("seed-demo is a development tool and refuses to run in production")
    if not rt.settings.simulator:
        raise RuntimeError("seed-demo needs the simulator (BDPAY_CLOUD_SIMULATOR=true)")
    with rt.engine.connect() as conn:
        ws_id = conn.execute(text("SELECT id FROM workspaces WHERE slug = :s"), {"s": slug}).scalar()
    if ws_id is None:
        raise RuntimeError(f"no workspace with the address '{slug}'")
    scope = Scope(ws_id, "sandbox")
    with rt.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO gateway_configs (workspace_id, mode, gateway, enabled, status)
            VALUES (:ws, 'sandbox', 'simulator', true, 'verified')
            ON CONFLICT (workspace_id, mode, gateway) DO UPDATE SET enabled = true"""),
            {"ws": ws_id},
        )
    rng = random.Random(seed)  # noqa: S311 - reproducible demo data, not a secret
    pay = rt.pay(scope)
    now = utcnow()
    made = dict.fromkeys(("paid", "failed", "cancelled", "expired", "needs_review", "pending", "refunded"), 0)
    stamp = now.strftime("%m%d%H%M%S")
    for i in range(count):
        outcome = rng.choices([o for o, _ in _OUTCOMES], weights=[w for _, w in _OUTCOMES])[0]
        # Busier in recent days, quieter on Fridays (the weekend in Bangladesh).
        age = timedelta(days=(rng.random() ** 1.4) * days, seconds=rng.randrange(0, 3600))
        created = now - age
        if created.weekday() == 4 and rng.random() < 0.5:
            created -= timedelta(days=1)
        if outcome == "pending":
            created = now - timedelta(minutes=rng.randrange(1, 40))
        order_id = f"DEMO{stamp}{i:04d}"
        name = rng.choice(_NAMES)
        amount = rng.choice(_AMOUNTS)
        try:
            pay.create_order(
                order_id,
                amount,
                "simulator",
                {"name": name, "phone": f"017{rng.randrange(10_000_000, 99_999_999)}"},
                metadata={"demo": "1", "cart": str(rng.randrange(1000, 9999))},
                source=rng.choice(["api", "api", "api", "checkout", "link"]),
            )
            url = pay.start_payment(order_id)
        except BdPayError:
            continue
        sim_id = url.rsplit("/", 1)[1]
        if outcome in ("paid", "needs_review", "failed", "cancelled"):
            state = {"paid": "completed", "needs_review": "completed", "failed": "failed", "cancelled": "cancelled"}[
                outcome
            ]
            with rt.engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE simulator_payments
                       SET status = :st, txn_id = CASE WHEN :st = 'completed' THEN :txn END,
                           paid_poisha = CASE WHEN :st = 'completed' THEN amount_poisha - :short END
                     WHERE id = :id"""),
                    {
                        "st": state,
                        "id": sim_id,
                        "txn": f"SIMTX{stamp}{i:04d}",
                        "short": 100 if outcome == "needs_review" else 0,
                    },
                )
            pay.requery(order_id)
        elif outcome == "expired":
            pay.store.transition(order_id, Status.EXPIRED, {"reason": "reconcile timeout"})
        if outcome == "paid" and rng.random() < 0.07:
            try:
                pay.refund(order_id, None if rng.random() < 0.5 else "100", "demo refund")
                made["refunded"] += 1
            except BdPayError:
                pass
        made[outcome] += 1
        if outcome != "pending":
            settled = created + timedelta(seconds=rng.randrange(25, 240))
            with rt.engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE orders SET created_at = :c, updated_at = :u, next_check_at = NULL,
                                      paid_at = CASE WHEN paid_at IS NOT NULL THEN :u END
                     WHERE workspace_id = :ws AND mode = 'sandbox' AND order_id = :oid"""),
                    {"c": created, "u": settled, "ws": ws_id, "oid": order_id},
                )
                conn.execute(
                    text("""
                    UPDATE order_events e SET at = CASE WHEN e.kind = 'created' THEN :c ELSE :u END
                      FROM orders o WHERE o.id = e.order_pk AND o.workspace_id = :ws AND o.mode = 'sandbox'
                       AND o.order_id = :oid"""),
                    {"c": created, "u": settled, "ws": ws_id, "oid": order_id},
                )
                conn.execute(
                    text("""
                    UPDATE events v SET created_at = :u FROM orders o
                     WHERE v.order_pk = o.id AND o.workspace_id = :ws AND o.mode = 'sandbox' AND o.order_id = :oid"""),
                    {"u": settled, "ws": ws_id, "oid": order_id},
                )
    return made
