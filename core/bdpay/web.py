"""FastAPI router. Mount it in any FastAPI app:

    from fastapi import FastAPI
    from bdpay import BdPay
    from bdpay.web import build_router

    pay = BdPay.from_env()
    app = FastAPI()
    app.include_router(build_router(pay, on_paid=lambda order: print("fulfil", order.order_id)))
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .core import BdPayError, Order, Status
from .service import BdPay

log = logging.getLogger("bdpay.web")

HTTP = {"VALIDATION": 400, "AMOUNT_MISMATCH": 409, "DUPLICATE": 409, "NOT_FOUND": 404, "CONFIG": 500,
        "SIGNATURE_INVALID": 403, "NOT_SUPPORTED": 400, "AUTH": 502, "PROVIDER_DECLINED": 402,
        "PROVIDER_UNAVAILABLE": 503, "TIMEOUT": 504}


class CreateOrder(BaseModel):
    order_id: str = Field(..., examples=["INV1001"])
    amount: str = Field(..., examples=["10.00"])
    gateway: str = Field(..., examples=["bkash"])
    customer: dict = Field(default_factory=dict)


class RefundIn(BaseModel):
    amount: Optional[str] = None
    reason: str = "customer request"


def order_json(o: Order) -> dict:
    return {"order_id": o.order_id, "gateway": o.gateway, "amount": o.amount, "currency": o.currency,
            "status": o.status.value, "provider_payment_id": o.provider_payment_id,
            "provider_txn_id": o.provider_txn_id, "refunded": f"{o.refunded_poisha / 100:.2f}"}


def _err(e: BdPayError) -> HTTPException:
    return HTTPException(HTTP.get(e.code, 400), {"code": e.code, "provider": e.provider, "message": e.message})


async def _params(request: Request) -> dict:
    params = dict(request.query_params)
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "json" in ctype:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            except ValueError:
                pass
        else:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
    return params


def build_router(pay: BdPay, on_paid: Optional[Callable[[Order], None]] = None,
                 result_url: Optional[str] = None, admin_token: Optional[str] = None) -> APIRouter:
    """result_url: where customers land after paying, e.g. "https://shop.example/thanks?order={order_id}".
    admin_token: if set, refund/requery/order APIs require header X-BDPAY-ADMIN."""
    r = APIRouter(prefix=pay.callback_prefix, tags=["bdpay"])

    def guard(request: Request):
        if admin_token and request.headers.get("x-bdpay-admin") != admin_token:
            raise HTTPException(401, "missing or wrong X-BDPAY-ADMIN header")

    def notify(o: Order, before: Status):
        if on_paid and o.status == Status.PAID and before != Status.PAID:
            try:
                on_paid(o)
            except Exception:
                log.exception("on_paid hook failed for %s", o.order_id)

    @r.get("/gateways")
    def gateways():
        return [{"id": d.id, "label": d.label, "stability": d.stability,
                 "refunds": d.capabilities.refunds} for d in pay.drivers.values()]

    @r.post("/orders")
    def create(body: CreateOrder, request: Request):
        guard(request)
        try:
            return order_json(pay.create_order(body.order_id, body.amount, body.gateway, body.customer))
        except BdPayError as e:
            raise _err(e)

    @r.post("/orders/{order_id}/pay")
    def start(order_id: str, request: Request):
        guard(request)
        try:
            return {"redirect_url": pay.start_payment(order_id)}
        except BdPayError as e:
            raise _err(e)

    @r.get("/orders/{order_id}")
    def get(order_id: str, request: Request):
        guard(request)
        o = pay.store.get(order_id)
        if not o:
            raise HTTPException(404, "not found")
        return {**order_json(o), "events": pay.store.events(order_id)}

    @r.post("/orders/{order_id}/requery")
    def requery(order_id: str, request: Request):
        guard(request)
        try:
            before = pay.store.get(order_id)
            o = pay.requery(order_id)
            if before:
                notify(o, before.status)
            return order_json(o)
        except BdPayError as e:
            raise _err(e)

    @r.post("/orders/{order_id}/refund")
    def refund(order_id: str, body: RefundIn, request: Request):
        guard(request)
        try:
            res = pay.refund(order_id, body.amount, body.reason)
            return {"ok": res.ok, "refund_ref": res.refund_ref, "status": res.status,
                    "order": order_json(pay.store.get(order_id))}
        except BdPayError as e:
            raise _err(e)

    @r.api_route("/callback/{gateway}/{order_id}/{token}/{outcome}", methods=["GET", "POST"])
    async def callback(gateway: str, order_id: str, token: str, outcome: str, request: Request):
        params = await _params(request)
        before = pay.store.get(order_id)
        try:
            o = pay.handle_callback(gateway, order_id, token, outcome, params)
        except BdPayError as e:
            log.warning("callback rejected: %s", e)
            if e.code == "SIGNATURE_INVALID":
                return HTMLResponse("<h1>Invalid or expired payment link</h1>", status_code=403)
            o = pay.store.get(order_id)
            if not o:
                return HTMLResponse("<h1>Unknown order</h1>", status_code=404)
        if before:
            notify(o, before.status)
        if result_url:
            return RedirectResponse(result_url.format(order_id=o.order_id, status=o.status.value), status_code=303)
        return RedirectResponse(f"{pay.callback_prefix}/result/{o.order_id}", status_code=303)

    @r.api_route("/ipn/{gateway}", methods=["POST", "GET"])
    async def ipn(gateway: str, request: Request):
        params = await _params(request)
        try:
            lookup = pay.driver(gateway).ipn_lookup(params)
            before = None
            if lookup and lookup[0] == "order_id":
                before = pay.store.get(lookup[1])
            elif lookup:
                before = pay.store.find_by_provider_id(gateway, lookup[1])
            o = pay.handle_ipn(gateway, params)
            if o and before:
                notify(o, before.status)
            return {"ok": True, "status": o.status.value if o else None}
        except BdPayError as e:
            log.warning("ipn rejected: %s", e)
            return JSONResponse({"ok": False, "code": e.code}, status_code=HTTP.get(e.code, 400))

    @r.get("/result/{order_id}", response_class=HTMLResponse)
    def result(order_id: str):
        o = pay.store.get(order_id)
        if not o:
            return HTMLResponse("<h1>Unknown order</h1>", status_code=404)
        ok = o.status == Status.PAID
        color = "#0a7d38" if ok else "#b42318"
        return HTMLResponse(f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Payment {o.status.value}</title><body style="font-family:system-ui;max-width:520px;margin:40px auto;padding:0 16px">
<h1 style="color:{color}">{'Payment successful' if ok else 'Payment ' + o.status.value.replace('_', ' ')}</h1>
<p>Order <b>{o.order_id}</b> · ৳{o.amount} · {o.gateway}</p>
<p><a href="/">Back to test console</a></p></body>""")

    return r
