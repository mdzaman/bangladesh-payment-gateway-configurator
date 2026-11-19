"""Merchant API (API-key authenticated).

Mounted twice:

* ``/v1``     the full API;
* ``/bdpay``  the order endpoints only, at the exact paths of the self-hosted
  bdpay REST API (build spec part 7.1). Code written against a self-hosted
  bdpay works against this service by changing the base URL and sending the
  API key in ``X-BDPAY-ADMIN``.

Errors use the reference shape ``{"detail": {"code", "provider", "message"}}``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from bdpay.core import BdPayError, fmt_amount

from ..db import session_scope
from ..engine.orders import order_json
from ..engine.runtime import Runtime
from ..errors import ServiceError
from ..services import audit, checkout, gateways, idempotency, orders, webhooks
from ..services.validation import clean_amount, clean_customer, clean_metadata, clean_order_id, clean_url
from .deps import KeyCtx, api_key, get_rt
from .errors import BDPAY_HTTP, ApiError, envelope
from .schemas import LinkIn, OrderIn, RefundIn, SessionIn


def _idempotent(
    request: Request, rt: Runtime, key: KeyCtx, body: Any, fn: Callable[[], dict], *, retry_unknown: bool = True
):
    """Run ``fn`` at most once per ``Idempotency-Key``.

    ``retry_unknown=False`` is for refunds: if the provider timed out, nobody
    knows whether money moved, so the same key must never run it again. The
    stored error is replayed and a person checks the provider panel."""
    idem_key = request.headers.get("idempotency-key")
    if not idem_key:
        return fn()
    request_hash = idempotency.fingerprint(request.method, request.url.path, body)
    replay = idempotency.begin(rt, key.scope, idem_key, request_hash)
    if replay is not None:
        status, stored = replay
        return JSONResponse(stored, status_code=status, headers={"Idempotent-Replayed": "true"})
    try:
        result = fn()
    except BdPayError as e:
        status = BDPAY_HTTP.get(e.code, 400)
        if status >= 500 and retry_unknown:
            idempotency.abandon(rt, key.scope, idem_key)
        else:
            idempotency.finish(rt, key.scope, idem_key, status, envelope(e.code, e.message, e.provider))
        raise
    except (ServiceError, ApiError) as e:
        idempotency.finish(rt, key.scope, idem_key, e.status, envelope(e.code, e.message, "platform"))
        raise
    except BaseException:
        idempotency.abandon(rt, key.scope, idem_key)
        raise
    idempotency.finish(rt, key.scope, idem_key, 200, result)
    return result


def _order(pay, order_id: str):
    order = pay.store.get(order_id)
    if order is None:
        raise ApiError(404, "NOT_FOUND", f"order {order_id} not found", provider="core")
    return order


def build_routers(prefix: str, *, full: bool) -> list[APIRouter]:
    r = APIRouter(prefix=prefix, tags=["Orders"] if full else ["Self-hosted bdpay compatibility"])

    # ------------------------------------------------------------- gateways
    @r.get("/gateways", summary="Gateways enabled for this API key's environment")
    def list_gateways(key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        with session_scope(rt.sessions) as db:
            rows = gateways.enabled_gateways(db, rt, key.scope)
        out = []
        for row in rows:
            spec = gateways.spec_for(row.gateway)
            out.append(
                {
                    "id": spec.id,
                    "label": spec.name,
                    "stability": {"exp": "experimental"}.get(spec.stability, spec.stability),
                    "refunds": gateways.refund_capability(spec.id),
                }
            )
        return out

    # --------------------------------------------------------------- orders
    @r.post("/orders", summary="Create an order")
    def create_order(body: OrderIn, request: Request, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        """Records the order on the server. The amount saved here is the only
        amount that will ever be accepted as paid. Creating the same
        ``order_id`` again with the same gateway and amount returns the
        existing order; different details answer ``409 DUPLICATE``."""
        key.need("orders:write")

        def run() -> dict:
            pay = rt.pay(key.env)
            order = pay.create_order(
                clean_order_id(body.order_id),
                fmt_amount(clean_amount(body.amount)),
                body.gateway,
                clean_customer(body.customer),
                metadata=clean_metadata(body.metadata),
                return_url=clean_url(rt.settings, key.scope.mode, body.return_url, "return_url"),
                source="api",
            )
            return order_json(order)

        return _idempotent(request, rt, key, body.model_dump(), run)

    @r.post("/orders/{order_id}/pay", summary="Start the payment and get the checkout URL")
    def start_payment(order_id: str, request: Request, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        """Returns ``redirect_url``: send the customer there. Calling it again
        for the same order returns the same URL (one payment attempt per order
        id); use a new ``order_id`` for a new attempt."""
        key.need("orders:write")
        return _idempotent(
            request, rt, key, {"order_id": order_id}, lambda: {"redirect_url": rt.pay(key.env).start_payment(order_id)}
        )

    @r.get("/orders/{order_id}", summary="Order status and audit trail")
    def get_order(order_id: str, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("orders:read")
        pay = rt.pay(key.env)
        return {**order_json(_order(pay, order_id)), "events": pay.store.events(order_id)}

    @r.post("/orders/{order_id}/requery", summary="Ask the provider again")
    def requery(order_id: str, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("orders:write")
        return order_json(rt.pay(key.env).requery(order_id))

    @r.post("/orders/{order_id}/refund", summary="Refund a paid order, fully or partly")
    def refund(
        order_id: str, body: RefundIn, request: Request, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)
    ):
        """Send an ``Idempotency-Key`` header: a refund is the one call where
        acting twice costs money."""
        key.need("refunds:write")

        def run() -> dict:
            pay = rt.pay(key.env)
            amount = fmt_amount(clean_amount(body.amount)) if body.amount not in (None, "") else None
            result = pay.refund(order_id, amount, body.reason or "customer request")
            with session_scope(rt.sessions) as db:
                audit.record(
                    db,
                    key.actor,
                    "order.refunded",
                    workspace_id=key.scope.workspace_id,
                    mode=key.scope.mode,
                    target_type="order",
                    target_id=order_id,
                    detail={"amount": amount or "full", "ok": result.ok, "ref": result.refund_ref},
                )
            return {
                "ok": result.ok,
                "refund_ref": result.refund_ref,
                "status": result.status,
                "order": order_json(pay.store.get(order_id)),
            }

        return _idempotent(request, rt, key, {"order_id": order_id, **body.model_dump()}, run, retry_unknown=False)

    if not full:
        return [r]

    @r.get("/orders", summary="List orders, newest first")
    def list_orders(
        key: KeyCtx = Depends(api_key),
        rt: Runtime = Depends(get_rt),
        status: str | None = Query(None, max_length=120, description="One status or several, comma-separated"),
        gateway: str | None = Query(None, max_length=40),
        q: str | None = Query(
            None, max_length=80, description="Order id prefix, provider transaction id, phone or email"
        ),
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = Query(50, ge=1, le=100),
        starting_after: str | None = Query(None, max_length=40, description="`next_cursor` of the previous page"),
    ):
        key.need("orders:read")
        with session_scope(rt.sessions) as db:
            return orders.list_orders(
                db,
                key.scope,
                status=status,
                gateway=gateway,
                q=q,
                created_from=created_from,
                created_to=created_to,
                limit=limit,
                cursor=starting_after,
            )

    # ------------------------------------------------------------- checkout
    c = APIRouter(prefix=prefix, tags=["Hosted checkout"])

    @c.post("/checkout/sessions", summary="Create a hosted checkout page")
    def create_session(
        body: SessionIn, request: Request, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)
    ):
        """Send the customer to ``url``. They choose a payment method there;
        each try is its own order linked to the session. Listen for
        ``checkout.session.completed`` or read the session to learn the result."""
        key.need("checkout:write")

        def run() -> dict:
            with session_scope(rt.sessions) as db:
                s = checkout.create_session(
                    db,
                    rt,
                    key.env,
                    amount=body.amount,
                    reference=body.reference,
                    description=body.description,
                    customer=body.customer,
                    metadata=body.metadata,
                    gateways_=body.gateways,
                    success_url=body.success_url,
                    cancel_url=body.cancel_url,
                    locale=body.locale,
                    expires_in_minutes=body.expires_in_minutes,
                )
                return checkout.session_json(s, rt.settings.public_url)

        return _idempotent(request, rt, key, body.model_dump(), run)

    @c.get("/checkout/sessions/{session_id}", summary="Read a checkout session and its attempts")
    def get_session(session_id: str, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("orders:read")
        with session_scope(rt.sessions) as db:
            s = checkout.get_session(db, key.scope, session_id)
            if s is None:
                raise ApiError(404, "NOT_FOUND", "No such checkout session")
            tries = [order_json(orders.to_cloud(o)) for o in checkout.attempts(db, key.scope, session_id)]
            return {**checkout.session_json(s, rt.settings.public_url), "orders": tries}

    @c.post("/checkout/sessions/{session_id}/expire", summary="Close an open checkout session")
    def expire_session(session_id: str, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("checkout:write")
        with session_scope(rt.sessions) as db:
            s = checkout.cancel_session(db, key.scope, session_id)
            if s is None:
                raise ApiError(404, "NOT_FOUND", "No such checkout session")
            return checkout.session_json(s, rt.settings.public_url)

    @c.post("/payment-links", summary="Create a reusable payment link")
    def create_link(body: LinkIn, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("checkout:write")
        with session_scope(rt.sessions) as db:
            link = checkout.create_link(
                db,
                rt,
                key.env,
                title=body.title,
                description=body.description,
                amount=body.amount,
                min_amount=body.min_amount,
                max_amount=body.max_amount,
                gateways_=body.gateways,
                collect=body.collect,
                success_url=body.success_url,
                max_payments=body.max_payments,
                actor=key.actor,
                user_id=None,
            )
            return checkout.link_json(link, rt.settings.public_url)

    @c.get("/payment-links", summary="List payment links")
    def list_links(key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("orders:read")
        with session_scope(rt.sessions) as db:
            return {"data": [checkout.link_json(x, rt.settings.public_url) for x in checkout.list_links(db, key.scope)]}

    # --------------------------------------------------------------- events
    e = APIRouter(prefix=prefix, tags=["Events"])

    @e.get("/events", summary="Recent events (what webhooks deliver)")
    def list_events(
        key: KeyCtx = Depends(api_key),
        rt: Runtime = Depends(get_rt),
        type: str | None = Query(None, max_length=60),
        limit: int = Query(50, ge=1, le=100),
    ):
        key.need("events:read")
        with session_scope(rt.sessions) as db:
            return {"data": webhooks.list_events(db, key.scope, type_=type, limit=limit)}

    @e.get("/events/{event_id}", summary="One event")
    def get_event(event_id: str, key: KeyCtx = Depends(api_key), rt: Runtime = Depends(get_rt)):
        key.need("events:read")
        with session_scope(rt.sessions) as db:
            event = webhooks.get_event(db, key.scope, event_id)
        if event is None:
            raise ApiError(404, "NOT_FOUND", "No such event")
        return event

    return [r, c, e]
