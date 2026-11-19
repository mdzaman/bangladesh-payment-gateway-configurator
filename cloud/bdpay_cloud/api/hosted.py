"""Customer-facing pages, rendered on the server so they open fast on a slow
phone and work without JavaScript: hosted checkout, payment links, the result
page and the sandbox simulator. English and Bangla.

These pages hold no secrets and take no card or wallet details. The customer
always finishes the payment on the provider's own page.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from bdpay.core import BdPayError, Status

from .. import catalog
from ..context import Scope
from ..db import session_scope
from ..engine.runtime import EnvInfo, Runtime
from ..ids import rand_b32
from ..services import checkout
from ..services.validation import fill_url
from .deps import client_ip, get_rt, rate_limit

log = logging.getLogger("bdpay_cloud.hosted")
router = APIRouter(include_in_schema=False)

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Strict: no scripts at all, styles only from this origin. ``form-action`` is
# left out on purpose, because paying is a form post that redirects to the provider.
HOSTED_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

PAID_STATES = (Status.PAID, Status.PARTIALLY_REFUNDED, Status.REFUNDED)
_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "test_mode": "Test mode: no real money moves",
        "amount_due": "Amount to pay",
        "choose_method": "Choose how to pay",
        "reference": "Reference",
        "cancel": "Cancel and go back",
        "secure_note": "You finish the payment on the provider's own secure page. {platform} never sees your PIN, "
        "password or card number.",
        "paid_title": "Payment successful",
        "paid_body": "Thank you. Your payment of {amount} to {brand} was received.",
        "continue": "Continue",
        "redirecting": "Taking you back to {brand}…",
        "failed_note": "That payment did not go through and you have not been charged. Try again or choose "
        "another method.",
        "cancelled_note": "You cancelled that payment. You have not been charged. Choose a method to try again.",
        "pending_note": "We have not received confirmation of that payment yet. If you completed it, wait a "
        "moment and refresh this page before trying again.",
        "review_note": "Your payment was received but needs a manual check by {brand}. Please contact them and "
        "quote the reference below.",
        "expired_title": "This checkout has expired",
        "expired_body": "Ask {brand} for a new payment link.",
        "cancelled_title": "Checkout cancelled",
        "cancelled_body": "No payment was taken.",
        "unavailable_title": "Payments are unavailable",
        "unavailable_body": "{brand} cannot take payments right now. Please try again later.",
        "no_methods": "No payment method is available right now.",
        "invalid_link_title": "This payment link is not valid",
        "invalid_link_body": "It may have expired or been copied incorrectly. Go back to the shop and start the "
        "payment again.",
        "unknown_order_title": "We could not find that payment",
        "unknown_order_body": "Go back to the shop and try again.",
        "method_error": "That payment method is not available right now. Please choose another.",
        "method_busy": "That payment method is not responding. Wait a moment and try again, or choose another.",
        "order": "Order",
        "amount": "Amount",
        "method": "Method",
        "status": "Status",
        "your_name": "Your name",
        "your_phone": "Mobile number",
        "your_email": "Email",
        "amount_label": "Amount (BDT)",
        "amount_between": "Between {low} and {high}",
        "link_closed_title": "This payment link is closed",
        "link_closed_body": "It is no longer accepting payments.",
        "support": "Need help? Contact {brand}",
        "powered": "Secure checkout by {platform}",
        "refresh": "Refresh",
        "switch_lang": "বাংলা",
        "result_other": "Payment {status}",
        "s_created": "not started",
        "s_pending": "pending",
        "s_paid": "successful",
        "s_failed": "failed",
        "s_cancelled": "cancelled",
        "s_expired": "expired",
        "s_needs_review": "under review",
        "s_partially_refunded": "partly refunded",
        "s_refunded": "refunded",
    },
    "bn": {
        "test_mode": "টেস্ট মোড: কোনো আসল টাকা লেনদেন হবে না",
        "amount_due": "পরিশোধের পরিমাণ",
        "choose_method": "পেমেন্টের মাধ্যম বেছে নিন",
        "reference": "রেফারেন্স",
        "cancel": "বাতিল করে ফিরে যান",
        "secure_note": "আপনি পেমেন্ট সম্পন্ন করবেন প্রোভাইডারের নিজস্ব নিরাপদ পেজে। {platform} কখনো আপনার পিন, "
        "পাসওয়ার্ড বা কার্ড নম্বর দেখে না।",
        "paid_title": "পেমেন্ট সফল হয়েছে",
        "paid_body": "ধন্যবাদ। {brand}-কে আপনার {amount} পেমেন্ট পাওয়া গেছে।",
        "continue": "এগিয়ে যান",
        "redirecting": "আপনাকে {brand}-এ ফিরিয়ে নেওয়া হচ্ছে…",
        "failed_note": "পেমেন্টটি সম্পন্ন হয়নি এবং আপনার কাছ থেকে কোনো টাকা কাটা হয়নি। আবার চেষ্টা করুন অথবা অন্য মাধ্যম বেছে নিন।",
        "cancelled_note": "আপনি পেমেন্টটি বাতিল করেছেন। কোনো টাকা কাটা হয়নি। আবার চেষ্টা করতে একটি মাধ্যম বেছে নিন।",
        "pending_note": "এই পেমেন্টের নিশ্চয়তা এখনো পাওয়া যায়নি। পেমেন্ট সম্পন্ন করে থাকলে কিছুক্ষণ অপেক্ষা করে "
        "পেজটি রিফ্রেশ করুন, তারপর প্রয়োজনে আবার চেষ্টা করুন।",
        "review_note": "আপনার পেমেন্ট পাওয়া গেছে, তবে {brand}-এর পক্ষ থেকে যাচাই প্রয়োজন। অনুগ্রহ করে নিচের "
        "রেফারেন্স উল্লেখ করে তাদের সাথে যোগাযোগ করুন।",
        "expired_title": "এই চেকআউটের মেয়াদ শেষ হয়ে গেছে",
        "expired_body": "নতুন পেমেন্ট লিংকের জন্য {brand}-এর সাথে যোগাযোগ করুন।",
        "cancelled_title": "চেকআউট বাতিল করা হয়েছে",
        "cancelled_body": "কোনো টাকা কাটা হয়নি।",
        "unavailable_title": "এই মুহূর্তে পেমেন্ট নেওয়া যাচ্ছে না",
        "unavailable_body": "{brand} এখন পেমেন্ট নিতে পারছে না। অনুগ্রহ করে পরে আবার চেষ্টা করুন।",
        "no_methods": "এই মুহূর্তে কোনো পেমেন্ট মাধ্যম চালু নেই।",
        "invalid_link_title": "এই পেমেন্ট লিংকটি সঠিক নয়",
        "invalid_link_body": "লিংকটির মেয়াদ শেষ হয়ে থাকতে পারে অথবা ভুলভাবে কপি করা হয়েছে। দোকানে ফিরে গিয়ে আবার পেমেন্ট শুরু করুন।",
        "unknown_order_title": "পেমেন্টটি খুঁজে পাওয়া যায়নি",
        "unknown_order_body": "দোকানে ফিরে গিয়ে আবার চেষ্টা করুন।",
        "method_error": "এই পেমেন্ট মাধ্যমটি এখন ব্যবহার করা যাচ্ছে না। অনুগ্রহ করে অন্য একটি বেছে নিন।",
        "method_busy": "এই পেমেন্ট মাধ্যমটি সাড়া দিচ্ছে না। কিছুক্ষণ পর আবার চেষ্টা করুন অথবা অন্য একটি বেছে নিন।",
        "order": "অর্ডার",
        "amount": "পরিমাণ",
        "method": "মাধ্যম",
        "status": "অবস্থা",
        "your_name": "আপনার নাম",
        "your_phone": "মোবাইল নম্বর",
        "your_email": "ইমেইল",
        "amount_label": "পরিমাণ (টাকা)",
        "amount_between": "{low} থেকে {high}-এর মধ্যে",
        "link_closed_title": "এই পেমেন্ট লিংকটি বন্ধ আছে",
        "link_closed_body": "এই লিংকে আর পেমেন্ট নেওয়া হচ্ছে না।",
        "support": "সাহায্য প্রয়োজন? {brand}-এর সাথে যোগাযোগ করুন",
        "powered": "{platform}-এর নিরাপদ চেকআউট",
        "refresh": "রিফ্রেশ করুন",
        "switch_lang": "English",
        "result_other": "পেমেন্ট {status}",
        "s_created": "শুরু হয়নি",
        "s_pending": "অপেক্ষমাণ",
        "s_paid": "সফল",
        "s_failed": "ব্যর্থ",
        "s_cancelled": "বাতিল",
        "s_expired": "মেয়াদোত্তীর্ণ",
        "s_needs_review": "যাচাই চলছে",
        "s_partially_refunded": "আংশিক ফেরত",
        "s_refunded": "ফেরত দেওয়া হয়েছে",
    },
}


# ------------------------------------------------------------------ helpers
def money(poisha: int, lang: str = "en") -> str:
    s = f"৳{poisha // 100:,}.{poisha % 100:02d}"
    return s.translate(_BN_DIGITS) if lang == "bn" else s


def pick_lang(request: Request, env: EnvInfo | None, preferred: str | None = None) -> str:
    asked = request.query_params.get("lang")
    if asked in STRINGS:
        return asked
    if preferred in STRINGS:
        return preferred
    if env is not None and env.workspace_settings.get("locale") in STRINGS:
        return env.workspace_settings["locale"]
    return "bn" if request.headers.get("accept-language", "").lower().startswith("bn") else "en"


def render(
    request: Request, template: str, *, env: EnvInfo | None, lang: str, status_code: int = 200, **ctx: Any
) -> HTMLResponse:
    settings = request.app.state.rt.settings
    brand = env.display_name if env else settings.brand_name
    strings = STRINGS[lang]

    def t(key: str, **kw: Any) -> str:
        return strings.get(key, STRINGS["en"].get(key, key)).format(brand=brand, platform=settings.brand_name, **kw)

    other = "en" if lang == "bn" else "bn"
    html = _templates.get_template(template).render(
        t=t,
        lang=lang,
        brand=brand,
        platform=settings.brand_name,
        sandbox=bool(env and env.scope.mode == "sandbox"),
        support_email=(env.workspace_settings.get("support_email") if env else "") or "",
        support_phone=(env.workspace_settings.get("support_phone") if env else "") or "",
        switch_url=f"{request.url.path}?lang={other}",
        **ctx,
    )
    return HTMLResponse(html, status_code=status_code, headers=HOSTED_HEADERS)


def message_page(
    request: Request,
    env: EnvInfo | None,
    lang: str,
    key: str,
    status_code: int = 200,
    link: tuple[str, str] | None = None,
) -> HTMLResponse:
    """A page with a title and a sentence, from the ``<key>_title`` / ``<key>_body`` strings."""
    return render(request, "message.html", env=env, lang=lang, status_code=status_code, key=key, link=link)


def _method_label(option: dict[str, str], lang: str) -> dict[str, str]:
    return {
        "id": option["id"],
        "label": option["label_bn"] if lang == "bn" else option["label"],
        "hint": option["hint_bn"] if lang == "bn" else option["hint"],
    }


def _limit(request: Request, rt: Runtime, page_id: str) -> None:
    """Many customers can share one address (carrier NAT), so the tight limit is
    per address *and* page; the per-address one is only a ceiling."""
    ip = client_ip(request)
    rate_limit(request, "hosted_page", f"{ip}:{page_id}", 20, 60)
    rate_limit(request, "hosted", ip, rt.settings.hosted_rate_per_minute, 60)


def _friendly(e: BdPayError) -> str:
    return "method_busy" if e.code in ("TIMEOUT", "PROVIDER_UNAVAILABLE") else "method_error"


# ----------------------------------------------------------------- checkout
def _load_checkout(rt: Runtime, session_id: str):
    with session_scope(rt.sessions) as db:
        s = checkout.find_public(db, session_id)
        if s is None:
            return None
        env = rt.load_env(Scope(s.workspace_id, s.mode))
        if env is None:
            return None
        return {
            "session": s,
            "env": env,
            "options": checkout.options(db, rt, env.scope, list(s.allowed_gateways)),
            "attempts": [(o.order_id, o.status, o.gateway) for o in checkout.attempts(db, env.scope, s.id)],
        }


def _checkout_view(
    request: Request, rt: Runtime, session_id: str, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    loaded = _load_checkout(rt, session_id)
    if loaded is None:
        return message_page(request, None, pick_lang(request, None), "invalid_link", status_code=404)
    s, env = loaded["session"], loaded["env"]
    lang = pick_lang(request, env, s.locale)
    amount = money(s.amount_poisha, lang)
    if s.status == "paid":
        target = (
            fill_url(
                s.success_url,
                session_id=s.id,
                order_id=s.paid_order_id or "",
                status="paid",
                reference=s.reference or "",
            )
            if s.success_url
            else None
        )
        return render(request, "done.html", env=env, lang=lang, session=s, amount=amount, target=target)
    if not checkout.is_open(s):
        key = "cancelled" if s.status == "cancelled" else "expired"
        back = (
            fill_url(s.cancel_url, session_id=s.id, reference=s.reference or "", status=key) if s.cancel_url else None
        )
        return message_page(request, env, lang, key, link=(back, "continue") if back else None)
    if not env.usable:
        return message_page(request, env, lang, "unavailable", status_code=503)
    # Tell the customer what happened to the attempt they have just come back from.
    note = None
    asked = request.query_params.get("attempt")
    last = next((a for a in loaded["attempts"] if a[0] == asked), None) if asked else None
    if last is not None:
        note = {
            "failed": "failed_note",
            "expired": "failed_note",
            "cancelled": "cancelled_note",
            "pending": "pending_note",
            "needs_review": "review_note",
        }.get(last[1])
    return render(
        request,
        "checkout.html",
        env=env,
        lang=lang,
        status_code=status_code,
        session=s,
        amount=amount,
        methods=[_method_label(o, lang) for o in loaded["options"]],
        note=note,
        error=error,
        review=bool(last and last[1] == "needs_review"),
        last_order=last[0] if last else None,
    )


@router.get("/checkout/{session_id}", response_class=HTMLResponse)
def checkout_page(session_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    return _checkout_view(request, rt, session_id)


@router.post("/checkout/{session_id}/pay")
async def checkout_pay(session_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    form = await request.form()
    return await run_in_threadpool(_checkout_pay, request, rt, session_id, str(form.get("gateway", "")))


def _checkout_pay(request: Request, rt: Runtime, session_id: str, gateway: str):
    _limit(request, rt, session_id)
    loaded = _load_checkout(rt, session_id)
    if loaded is None:
        return message_page(request, None, pick_lang(request, None), "invalid_link", status_code=404)
    env = loaded["env"]
    if not env.usable:
        return _checkout_view(request, rt, session_id)
    try:
        url = checkout.start_attempt(rt, env, session_id, gateway)
    except checkout.AlreadyPaid:
        return RedirectResponse(f"/checkout/{session_id}", status_code=303)
    except checkout.CheckoutError as e:
        if e.status == 409:
            return RedirectResponse(f"/checkout/{session_id}", status_code=303)
        return _checkout_view(request, rt, session_id, error="method_error", status_code=400)
    except BdPayError as e:
        log.warning("checkout %s could not start %s: %s", session_id, gateway, e)
        return _checkout_view(request, rt, session_id, error=_friendly(e), status_code=200)
    return RedirectResponse(url, status_code=303)


@router.post("/checkout/{session_id}/cancel")
def checkout_cancel(session_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    loaded = _load_checkout(rt, session_id)
    if loaded is None:
        return message_page(request, None, pick_lang(request, None), "invalid_link", status_code=404)
    with session_scope(rt.sessions) as db:
        checkout.cancel_session(db, loaded["env"].scope, session_id)
    return RedirectResponse(f"/checkout/{session_id}", status_code=303)


# -------------------------------------------------------------------- links
def _load_link(rt: Runtime, link_id: str):
    with session_scope(rt.sessions) as db:
        link = checkout.find_public_link(db, link_id)
        if link is None:
            return None
        env = rt.load_env(Scope(link.workspace_id, link.mode))
        if env is None:
            return None
        return {"link": link, "env": env, "options": checkout.options(db, rt, env.scope, list(link.allowed_gateways))}


def _link_view(
    request: Request,
    rt: Runtime,
    link_id: str,
    values: dict[str, str] | None = None,
    error: str | None = None,
    error_text: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    loaded = _load_link(rt, link_id)
    if loaded is None:
        return message_page(request, None, pick_lang(request, None), "invalid_link", status_code=404)
    link, env = loaded["link"], loaded["env"]
    lang = pick_lang(request, env)
    if not checkout.link_accepts_payments(link):
        return message_page(request, env, lang, "link_closed", status_code=410)
    if not env.usable:
        return message_page(request, env, lang, "unavailable", status_code=503)
    return render(
        request,
        "link.html",
        env=env,
        lang=lang,
        status_code=status_code,
        link=link,
        amount=money(link.amount_poisha, lang) if link.amount_poisha is not None else None,
        low=money(link.min_poisha or 0, lang),
        high=money(link.max_poisha or 0, lang),
        methods=[_method_label(o, lang) for o in loaded["options"]],
        values=values or {},
        error=error,
        error_text=error_text,
    )


@router.get("/pay/{link_id}", response_class=HTMLResponse)
def link_page(link_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    return _link_view(request, rt, link_id)


@router.post("/pay/{link_id}")
async def link_pay(link_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    form = await request.form()
    values = {k: str(form.get(k, "")).strip()[:200] for k in ("gateway", "amount", "name", "phone", "email")}
    return await run_in_threadpool(_link_pay, request, rt, link_id, values)


def _link_pay(request: Request, rt: Runtime, link_id: str, values: dict[str, str]):
    _limit(request, rt, link_id)
    loaded = _load_link(rt, link_id)
    if loaded is None:
        return message_page(request, None, pick_lang(request, None), "invalid_link", status_code=404)
    link, env = loaded["link"], loaded["env"]
    if not env.usable or not checkout.link_accepts_payments(link):
        return _link_view(request, rt, link_id)
    customer = {k: values[k] for k in checkout.COLLECTABLE if values.get(k)}
    try:
        with session_scope(rt.sessions) as db:
            fresh = checkout.find_public_link(db, link_id)
            session_id = checkout.session_from_link(db, rt, env, fresh, amount=values["amount"], customer=customer).id
    except (BdPayError, checkout.CheckoutError) as e:
        return _link_view(request, rt, link_id, values, error_text=e.message, status_code=400)
    try:
        url = checkout.start_attempt(rt, env, session_id, values["gateway"], customer)
    except checkout.CheckoutError:
        # The session exists: let them pick a method on the checkout page.
        return RedirectResponse(f"/checkout/{session_id}", status_code=303)
    except BdPayError as e:
        log.warning("payment link %s could not start %s: %s", link_id, values["gateway"], e)
        return RedirectResponse(f"/checkout/{session_id}", status_code=303)
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------- simulator
_SIM_ACTIONS = {
    "approve": ("completed", "success"),
    "wrong_amount": ("completed", "success"),
    "lost_redirect": ("completed", None),
    "decline": ("failed", "fail"),
    "cancel": ("cancelled", "cancel"),
}


def _load_sim(rt: Runtime, sim_id: str):
    if not sim_id.startswith("sim_") or len(sim_id) > 64 or not rt.settings.simulator:
        return None
    with rt.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, workspace_id, mode, order_ref, amount_poisha, status, return_urls "
                "FROM simulator_payments WHERE id = :id"
            ),
            {"id": sim_id},
        ).one_or_none()
    if row is None:
        return None
    env = rt.load_env(Scope(row.workspace_id, row.mode))
    return (row, env) if env else None


@router.get("/simulator/{sim_id}", response_class=HTMLResponse)
def simulator_page(sim_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    loaded = _load_sim(rt, sim_id)
    if loaded is None:
        return message_page(request, None, "en", "invalid_link", status_code=404)
    row, env = loaded
    return render(
        request,
        "simulator.html",
        env=env,
        lang="en",
        row=row,
        amount=money(row.amount_poisha),
        wrong=money(max(100, row.amount_poisha - 100)),
        done=row.status != "created",
        dropped=False,
    )


@router.post("/simulator/{sim_id}")
async def simulator_action(sim_id: str, request: Request, rt: Runtime = Depends(get_rt)):
    form = await request.form()
    return await run_in_threadpool(_simulator_action, request, rt, sim_id, str(form.get("action", "")))


def _simulator_action(request: Request, rt: Runtime, sim_id: str, action: str):
    loaded = _load_sim(rt, sim_id)
    if loaded is None or action not in _SIM_ACTIONS:
        return message_page(request, None, "en", "invalid_link", status_code=404)
    row, env = loaded
    status, outcome = _SIM_ACTIONS[action]
    paid = None
    if status == "completed":
        # "wrong amount" pays 1 taka less, which the engine must refuse to call paid.
        paid = max(100, row.amount_poisha - 100) if action == "wrong_amount" else row.amount_poisha
    with rt.engine.begin() as conn:
        changed = conn.execute(
            text("""
            UPDATE simulator_payments SET status = :status, paid_poisha = :paid, txn_id = :txn, updated_at = now()
             WHERE id = :id AND status = 'created' RETURNING id"""),
            {"id": sim_id, "status": status, "paid": paid, "txn": "SIMTX" + rand_b32(10).upper() if paid else None},
        ).scalar()
    if not changed or outcome is None:
        return render(
            request,
            "simulator.html",
            env=env,
            lang="en",
            row=row,
            amount=money(row.amount_poisha),
            wrong="",
            done=True,
            dropped=bool(changed),
        )
    back = (row.return_urls or {}).get(outcome, "")
    return RedirectResponse(f"{back}?sim_payment_id={sim_id}&status={outcome}", status_code=303)


def gateway_names() -> dict[str, str]:
    return {g.id: g.name for g in catalog.load().gateways}
