"""Dashboard API for one workspace environment:
``/api/workspaces/{slug}/{mode}/...`` with mode ``sandbox`` or ``live``.

Reading needs any member. Changing things needs ``developer`` in sandbox and
``admin`` in live (``ctx.need_write()``).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from bdpay.core import fmt_amount

from ..db import session_scope
from ..engine.orders import order_json
from ..engine.runtime import Runtime
from ..ids import new_order_ref
from ..models import WorkspaceEnv
from ..services import apikeys, audit, checkout, gateways, orders, webhooks
from ..services.events import EVENT_TYPES
from ..services.validation import clean_amount, clean_customer, clean_order_id, clean_url
from .deps import WsCtx, env_ctx, get_rt, rate_limit
from .errors import ApiError
from .schemas import (
    ApiKeyIn,
    ChecklistIn,
    ConsoleOrderIn,
    EnvSettingsIn,
    GatewayPatchIn,
    GatewaySaveIn,
    LinkIn,
    LinkPatch,
    RefundIn,
    ReorderIn,
    SessionIn,
    WebhookIn,
    WebhookPatch,
)

router = APIRouter(prefix="/api/workspaces/{slug}/{mode}", tags=["dashboard"], include_in_schema=False)


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt), days: int = Query(30, ge=1, le=90)):
    with session_scope(rt.sessions) as db:
        configured = gateways.listing(db, rt, ctx.env)
        return {
            "stats": orders.stats(db, ctx.scope, days),
            "gateways": [
                {
                    "id": g["id"],
                    "enabled": g["enabled"],
                    "status": g["status"],
                    "last_error": g["last_error"],
                    "last_success_at": g["last_success_at"],
                }
                for g in configured
                if g["configured"]
            ],
            "checklist": gateways.checklist(db, rt, ctx.env),
            "usable": ctx.env.usable,
        }


@router.get("/settings")
def get_settings(ctx: WsCtx = Depends(env_ctx)):
    return {"result_url": ctx.env.result_url, "environment_key": ctx.env.public_key}


@router.patch("/settings")
def update_settings(body: EnvSettingsIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    url = clean_url(rt.settings, ctx.scope.mode, body.result_url, "The after-payment page")
    with session_scope(rt.sessions) as db:
        env = db.get(WorkspaceEnv, (ctx.scope.workspace_id, ctx.scope.mode), with_for_update=True)
        env.result_url = url
        audit.record(
            db,
            ctx.actor,
            "environment.updated",
            workspace_id=ctx.scope.workspace_id,
            mode=ctx.scope.mode,
            detail={"result_url": url},
        )
    return {"result_url": url, "environment_key": ctx.env.public_key}


# ------------------------------------------------------------------ gateways
@router.get("/gateways")
def list_gateways(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return {
            "gateways": gateways.listing(db, rt, ctx.env),
            "egress_ips": rt.settings.egress_ip_list,
            "callback_domain": rt.settings.public_host,
        }


@router.put("/gateways/{gateway}")
def save_gateway(gateway: str, body: GatewaySaveIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        return gateways.save(
            db,
            rt,
            ctx.env,
            gateway,
            values=body.values,
            enabled=body.enabled,
            clear=body.clear,
            actor=ctx.actor,
            user_id=ctx.user.user.id,
        )


@router.patch("/gateways/{gateway}")
def toggle_gateway(gateway: str, body: GatewayPatchIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        return gateways.set_enabled(db, rt, ctx.env, gateway, body.enabled, ctx.actor)


@router.delete("/gateways/{gateway}")
def remove_gateway(gateway: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        gateways.delete(db, rt, ctx.env, gateway, ctx.actor)
    return {"ok": True}


@router.post("/gateways/{gateway}/test")
def test_gateway(gateway: str, request: Request, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    # Each test can be a real token grant at the provider: keep it rare.
    rate_limit(request, "gateway_test", f"{ctx.scope.workspace_id}:{ctx.scope.mode}", 10, 60)
    return gateways.test_connection(rt, ctx.env, gateway)


@router.post("/gateways-order")
def reorder_gateways(body: ReorderIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        gateways.reorder(db, ctx.scope, body.order)
        return {"gateways": gateways.listing(db, rt, ctx.env)}


@router.get("/checklist")
def get_checklist(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return {"items": gateways.checklist(db, rt, ctx.env)}


@router.post("/checklist")
def confirm_checklist(body: ChecklistIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        gateways.confirm_checklist_item(db, ctx.env, body.id, body.done, ctx.actor)
        return {"items": gateways.checklist(db, rt, ctx.env)}


# -------------------------------------------------------------------- orders
@router.get("/orders")
def list_orders(
    ctx: WsCtx = Depends(env_ctx),
    rt: Runtime = Depends(get_rt),
    status: str | None = Query(None, max_length=120),
    gateway: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=80),
    source: str | None = Query(None, max_length=20),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=40),
):
    with session_scope(rt.sessions) as db:
        return orders.list_orders(
            db,
            ctx.scope,
            status=status,
            gateway=gateway,
            q=q,
            source=source,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            cursor=cursor,
        )


@router.post("/orders")
def console_order(body: ConsoleOrderIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    """Create an order and open its payment from the dashboard (the test console)."""
    ctx.need_write()
    if not ctx.env.usable:
        raise ApiError(403, "FORBIDDEN", "This workspace cannot take payments right now")
    pay = rt.pay(ctx.env)
    order_id = clean_order_id(body.order_id) if body.order_id else new_order_ref("DB")
    order = pay.create_order(
        order_id,
        fmt_amount(clean_amount(body.amount)),
        body.gateway,
        clean_customer(body.customer),
        source="dashboard",
        metadata={"started_by": ctx.user.user.email},
    )
    url = pay.start_payment(order.order_id)
    with session_scope(rt.sessions) as db:
        audit.record(
            db,
            ctx.actor,
            "order.started_from_dashboard",
            workspace_id=ctx.scope.workspace_id,
            mode=ctx.scope.mode,
            target_type="order",
            target_id=order.order_id,
            detail={"gateway": body.gateway, "amount": order.amount},
        )
    return {"order": order_json(pay.store.get(order.order_id)), "redirect_url": url}


@router.get("/orders/{order_id}")
def get_order(order_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    pay = rt.pay(ctx.env)
    order = pay.store.get(order_id)
    if order is None:
        raise ApiError(404, "NOT_FOUND", "No such order")
    with session_scope(rt.sessions) as db:
        related = webhooks.list_events(db, ctx.scope, order_id=order_id, limit=20)
    return {
        **order_json(order),
        "events": pay.store.events(order_id),
        "refund_capability": gateways.refund_capability(order.gateway),
        "webhook_events": [{"id": e["id"], "type": e["type"], "created_at": e["created_at"]} for e in related],
    }


@router.post("/orders/{order_id}/requery")
def requery_order(order_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("developer")
    return order_json(rt.pay(ctx.env).requery(order_id))


@router.post("/orders/{order_id}/refund")
def refund_order(order_id: str, body: RefundIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    pay = rt.pay(ctx.env)
    amount = fmt_amount(clean_amount(body.amount)) if body.amount not in (None, "") else None
    result = pay.refund(order_id, amount, body.reason or "customer request")
    with session_scope(rt.sessions) as db:
        audit.record(
            db,
            ctx.actor,
            "order.refunded",
            workspace_id=ctx.scope.workspace_id,
            mode=ctx.scope.mode,
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


# ------------------------------------------------------------------ API keys
@router.get("/api-keys")
def list_api_keys(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("developer")
    with session_scope(rt.sessions) as db:
        return {
            "data": [apikeys.key_json(k) for k in apikeys.list_keys(db, ctx.scope)],
            "scopes": list(apikeys.ALL_SCOPES),
        }


@router.post("/api-keys")
def create_api_key(body: ApiKeyIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        key, plaintext = apikeys.create_key(
            db,
            rt.settings,
            ctx.scope,
            name=body.name,
            scopes=body.scopes,
            created_by=ctx.user.user.id,
            expires_in_days=body.expires_in_days,
        )
        audit.record(
            db,
            ctx.actor,
            "api_key.created",
            workspace_id=ctx.scope.workspace_id,
            mode=ctx.scope.mode,
            target_type="api_key",
            target_id=key.id,
            detail={"name": key.name, "scopes": key.scopes},
        )
        # The only time the full key leaves the server.
        return {**apikeys.key_json(key), "key": plaintext}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        key = apikeys.revoke_key(db, ctx.scope, key_id)
        audit.record(
            db,
            ctx.actor,
            "api_key.revoked",
            workspace_id=ctx.scope.workspace_id,
            mode=ctx.scope.mode,
            target_type="api_key",
            target_id=key.id,
            detail={"name": key.name},
        )
        return apikeys.key_json(key)


# ------------------------------------------------------------------ webhooks
@router.get("/webhooks")
def list_webhooks(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("developer")
    with session_scope(rt.sessions) as db:
        return {
            "data": [webhooks.endpoint_json(e) for e in webhooks.list_endpoints(db, ctx.scope)],
            "event_types": [t for t in EVENT_TYPES if t != "ping"],
        }


@router.post("/webhooks")
def create_webhook(body: WebhookIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        endpoint, secret = webhooks.create_endpoint(
            db,
            rt,
            ctx.scope,
            url=body.url,
            description=body.description,
            event_types=body.event_types,
            actor=ctx.actor,
            user_id=ctx.user.user.id,
        )
        return {**webhooks.endpoint_json(endpoint), "secret": secret}


@router.patch("/webhooks/{endpoint_id}")
def update_webhook(endpoint_id: str, body: WebhookPatch, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        return webhooks.endpoint_json(
            webhooks.update_endpoint(
                db,
                rt,
                ctx.scope,
                endpoint_id,
                url=body.url,
                description=body.description,
                event_types=body.event_types,
                enabled=body.enabled,
                actor=ctx.actor,
            )
        )


@router.delete("/webhooks/{endpoint_id}")
def delete_webhook(endpoint_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        webhooks.delete_endpoint(db, ctx.scope, endpoint_id, ctx.actor)
    return {"ok": True}


@router.post("/webhooks/{endpoint_id}/rotate-secret")
def rotate_webhook_secret(endpoint_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        endpoint, secret = webhooks.rotate_secret(db, rt, ctx.scope, endpoint_id, ctx.actor)
        return {**webhooks.endpoint_json(endpoint), "secret": secret}


@router.post("/webhooks/{endpoint_id}/test")
def test_webhook(endpoint_id: str, request: Request, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    rate_limit(request, "webhook_test", ctx.scope.workspace_id, 20, 60)
    with session_scope(rt.sessions) as db:
        return {"event_id": webhooks.send_test(db, ctx.scope, endpoint_id)}


@router.get("/webhook-deliveries")
def list_deliveries(
    ctx: WsCtx = Depends(env_ctx),
    rt: Runtime = Depends(get_rt),
    endpoint_id: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=20),
    event_id: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=100),
):
    ctx.need("developer")
    with session_scope(rt.sessions) as db:
        return {
            "data": webhooks.list_deliveries(
                db, ctx.scope, endpoint_id=endpoint_id, status=status, event_id=event_id, limit=limit
            )
        }


@router.post("/webhook-deliveries/{delivery_id}/redeliver")
def redeliver(delivery_id: str, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        return {"delivery_id": webhooks.redeliver(db, ctx.scope, delivery_id)}


@router.get("/events")
def list_events(
    ctx: WsCtx = Depends(env_ctx),
    rt: Runtime = Depends(get_rt),
    type: str | None = Query(None, max_length=60),
    limit: int = Query(50, ge=1, le=100),
):
    ctx.need("developer")
    with session_scope(rt.sessions) as db:
        return {"data": webhooks.list_events(db, ctx.scope, type_=type, limit=limit)}


# ------------------------------------------------------- checkout and links
@router.get("/checkout-sessions")
def list_checkout_sessions(
    ctx: WsCtx = Depends(env_ctx),
    rt: Runtime = Depends(get_rt),
    status: str | None = Query(None, max_length=20),
    limit: int = Query(50, ge=1, le=100),
):
    with session_scope(rt.sessions) as db:
        return {
            "data": [
                checkout.session_json(s, rt.settings.public_url)
                for s in checkout.list_sessions(db, ctx.scope, status=status, limit=limit)
            ]
        }


@router.post("/checkout-sessions")
def create_checkout_session(body: SessionIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        s = checkout.create_session(
            db,
            rt,
            ctx.env,
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


@router.get("/checkout-options")
def checkout_options(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return {"data": checkout.options(db, rt, ctx.scope)}


@router.get("/payment-links")
def list_payment_links(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return {
            "data": [checkout.link_json(link, rt.settings.public_url) for link in checkout.list_links(db, ctx.scope)]
        }


@router.post("/payment-links")
def create_payment_link(body: LinkIn, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        link = checkout.create_link(
            db,
            rt,
            ctx.env,
            title=body.title,
            description=body.description,
            amount=body.amount,
            min_amount=body.min_amount,
            max_amount=body.max_amount,
            gateways_=body.gateways,
            collect=body.collect,
            success_url=body.success_url,
            max_payments=body.max_payments,
            actor=ctx.actor,
            user_id=ctx.user.user.id,
        )
        return checkout.link_json(link, rt.settings.public_url)


@router.patch("/payment-links/{link_id}")
def update_payment_link(link_id: str, body: LinkPatch, ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need_write()
    with session_scope(rt.sessions) as db:
        return checkout.link_json(
            checkout.set_link_active(db, ctx.scope, link_id, body.active, ctx.actor), rt.settings.public_url
        )


@router.get("/worker")
def worker_status(ctx: WsCtx = Depends(env_ctx), rt: Runtime = Depends(get_rt)):
    with rt.engine.connect() as conn:
        beat = conn.execute(
            text("SELECT extract(epoch FROM now() - last_beat)::float8 FROM worker_heartbeats WHERE name = 'worker'")
        ).scalar()
    return {"running": beat is not None and beat < 180, "seconds_since_heartbeat": beat}
