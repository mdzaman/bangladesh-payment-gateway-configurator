"""Endpoints the payment providers (and the customer's browser, sent back by
a provider) call. They carry no API key and no session: a callback is trusted
only as far as its HMAC-signed path, and even then it is just a hint that
triggers server-to-server verification (rules 2 and 3 of the build spec).

    /p/{environment key}/callback/{gateway}/{order_id}/{exp}.{hmac}/{outcome}
    /p/{environment key}/ipn/{gateway}

Callbacks and IPNs are processed even for a suspended workspace: a customer
who has paid must still be recorded as paid.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from bdpay.core import BdPayError

from ..engine.runtime import EnvInfo, Runtime, TenantPay
from ..services.validation import fill_url
from . import hosted
from .deps import client_ip, get_rt, rate_limit
from .errors import BDPAY_HTTP

log = logging.getLogger("bdpay_cloud.provider")
router = APIRouter(prefix="/p", include_in_schema=False)


async def _params(request: Request) -> dict:
    """Query string plus form or JSON body, like the reference router."""
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


def after_payment_url(rt: Runtime, pay: TenantPay, env: EnvInfo, order) -> str:
    """Where the customer goes once the callback has been verified."""
    session_id = getattr(order, "checkout_session_id", None)
    if session_id:
        return f"/checkout/{session_id}?attempt={order.order_id}"
    template = getattr(order, "return_url", None) or env.result_url
    if template:
        return fill_url(template, order_id=order.order_id, status=order.status.value)
    return f"/p/{env.public_key}/result/{order.order_id}/{pay.result_token(order.order_id)}"


def _callback(
    request: Request, rt: Runtime, env_key: str, gateway: str, order_id: str, token: str, outcome: str, params: dict
):
    rate_limit(request, "callback", client_ip(request), 1200, 60)
    # Each callback for an unsettled order is a call to the provider with the
    # merchant's credentials: nobody gets to replay one link endlessly.
    rate_limit(request, "callback_order", f"{env_key}:{order_id}", 30, 600)
    env = rt.load_env_by_public_key(env_key)
    lang = hosted.pick_lang(request, env)
    if env is None:
        return hosted.message_page(request, None, lang, "invalid_link", status_code=404)
    pay = rt.pay(env)
    try:
        order = pay.handle_callback(gateway, order_id, token, outcome, params)
    except BdPayError as e:
        log.warning("callback rejected: %s", e)
        if e.code == "SIGNATURE_INVALID":
            return hosted.message_page(request, env, lang, "invalid_link", status_code=403)
        # The provider said something we could not use. Show the order as it stands.
        order = pay.store.get(order_id)
        if order is None or order.gateway != gateway:
            return hosted.message_page(request, env, lang, "unknown_order", status_code=404)
    return RedirectResponse(after_payment_url(rt, pay, env, order), status_code=303)


@router.api_route("/{env_key}/callback/{gateway}/{order_id}/{token}/{outcome}", methods=["GET", "POST"])
async def callback(
    env_key: str, gateway: str, order_id: str, token: str, outcome: str, request: Request, rt: Runtime = Depends(get_rt)
):
    params = await _params(request)
    return await run_in_threadpool(_callback, request, rt, env_key, gateway, order_id, token, outcome, params)


def _ipn(request: Request, rt: Runtime, env_key: str, gateway: str, params: dict):
    # An IPN makes this service call the provider, so cap how often a stranger can trigger that.
    rate_limit(request, "ipn", f"{env_key}:{client_ip(request)}", 120, 60)
    env = rt.load_env_by_public_key(env_key)
    if env is None:
        return JSONResponse({"ok": False, "code": "NOT_FOUND"}, status_code=404)
    try:
        pay = rt.pay(env)
        ref = pay.driver(gateway).ipn_lookup(params)
        if ref:
            # Order ids can be guessed; without this anyone could make us query the
            # provider about a pending order as often as they like.
            rate_limit(request, "ipn_order", f"{env_key}:{gateway}:{ref[1]}", 12, 600)
        order = pay.handle_ipn(gateway, params)
        return {"ok": True, "status": order.status.value if order else None}
    except BdPayError as e:
        log.warning("ipn rejected: %s", e)
        return JSONResponse({"ok": False, "code": e.code}, status_code=BDPAY_HTTP.get(e.code, 400))


@router.api_route("/{env_key}/ipn/{gateway}", methods=["POST", "GET"])
async def ipn(env_key: str, gateway: str, request: Request, rt: Runtime = Depends(get_rt)):
    params = await _params(request)
    return await run_in_threadpool(_ipn, request, rt, env_key, gateway, params)


@router.get("/{env_key}/result/{order_id}/{sig}", response_class=HTMLResponse)
def result(env_key: str, order_id: str, sig: str, request: Request, rt: Runtime = Depends(get_rt)):
    """Built-in result page, for orders with no return URL. The signature in
    the path stops anyone reading an order's status by guessing its id."""
    env = rt.load_env_by_public_key(env_key)
    lang = hosted.pick_lang(request, env)
    if env is None:
        return hosted.message_page(request, None, lang, "unknown_order", status_code=404)
    pay = rt.pay(env)
    order = pay.store.get(order_id) if pay.check_result_token(order_id, sig) else None
    if order is None:
        return hosted.message_page(request, env, lang, "unknown_order", status_code=404)
    return hosted.render(
        request,
        "result.html",
        env=env,
        lang=lang,
        order=order,
        paid=order.status in hosted.PAID_STATES,
        amount=hosted.money(order.amount_poisha, lang),
    )
