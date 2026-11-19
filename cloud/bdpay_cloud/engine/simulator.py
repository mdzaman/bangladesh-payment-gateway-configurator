"""Built-in fake provider for sandbox mode.

Lets a new workspace run the whole flow (order, redirect, signed callback,
server-side verification, webhook, refund) in a minute, with no credentials
and no outside network. It is a real driver: the engine treats it exactly
like bKash or SSLCOMMERZ and never trusts the browser redirect, it asks the
"provider" (a table) what happened.

The hosted page offers the awkward cases on purpose: decline, cancel, pay the
wrong amount (must end in ``needs_review``), and pay but lose the redirect
(the reconcile worker must find it).

Cannot be built in live mode.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy import Engine, text

from bdpay.core import (
    BdPayError,
    CallbackUrls,
    Capabilities,
    Driver,
    Order,
    PaymentSession,
    RefundResult,
    Status,
    Verified,
)

from ..context import Scope
from ..ids import new_public_token, rand_b32

_STATUS = {"created": Status.PENDING, "completed": Status.PAID, "failed": Status.FAILED, "cancelled": Status.CANCELLED}


class SimulatorDriver(Driver):
    id = "simulator"
    label = "Simulator (test gateway)"
    stability = "stable"
    capabilities = Capabilities(refunds="partial", webhooks="requery")
    required_env = ()

    def __init__(self, cfg, http, tokens, *, engine: Engine, scope: Scope, public_url: str):
        if cfg.environment != "sandbox" or scope.mode != "sandbox":
            raise BdPayError("CONFIG", "the simulator exists only in sandbox mode", "simulator")
        super().__init__(cfg, http, tokens)
        self.engine = engine
        self.scope = scope
        self.public_url = public_url.rstrip("/")

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        sim_id = new_public_token("sim")
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO simulator_payments (id, workspace_id, mode, order_ref, amount_poisha, return_urls)
                VALUES (:id, :ws, :mode, :ref, :amount, CAST(:urls AS jsonb))"""),
                {
                    "id": sim_id,
                    **self.scope.params,
                    "ref": order.order_id,
                    "amount": order.amount_poisha,
                    "urls": json.dumps(
                        {"success": urls.success, "fail": urls.fail, "cancel": urls.cancel, "ipn": urls.ipn}
                    ),
                },
            )
        return PaymentSession(sim_id, f"{self.public_url}/simulator/{sim_id}", {"simulator": True})

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        pid = params.get("sim_payment_id") or order.provider_payment_id
        if order.provider_payment_id and pid != order.provider_payment_id:
            raise self.fail("VALIDATION", "sim_payment_id does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        with self.engine.connect() as conn:
            r = conn.execute(
                text("""
                SELECT id, order_ref, status, paid_poisha, txn_id FROM simulator_payments
                 WHERE id = :id AND workspace_id = :ws AND mode = :mode"""),
                {"id": order.provider_payment_id, **self.scope.params},
            ).one_or_none()
        if r is None:
            return Verified(Status.FAILED, provider_payment_id=order.provider_payment_id)
        return Verified(
            _STATUS[r.status], r.paid_poisha, "BDT", r.order_ref, r.id, r.txn_id, {"simulator_status": r.status}
        )

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        with self.engine.begin() as conn:
            ok = conn.execute(
                text("""
                UPDATE simulator_payments SET refunded_poisha = refunded_poisha + :a, updated_at = now()
                 WHERE id = :id AND workspace_id = :ws AND mode = :mode AND status = 'completed'
                   AND refunded_poisha + :a <= paid_poisha
                RETURNING id"""),
                {"a": amount_poisha, "id": order.provider_payment_id, **self.scope.params},
            ).scalar()
        if not ok:
            return RefundResult(False, None, "failed", {"simulator": "refund exceeds the paid amount"})
        return RefundResult(True, "SIMRF" + rand_b32(10).upper(), "completed", {"simulator": True})

    def ipn_lookup(self, params):
        pid = params.get("sim_payment_id")
        return ("provider_payment_id", pid) if pid else None
