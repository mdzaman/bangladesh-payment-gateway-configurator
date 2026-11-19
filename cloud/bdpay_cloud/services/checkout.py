"""Hosted checkout sessions and payment links.

A checkout session is a fixed amount the customer pays on a page hosted by
this service, choosing among the workspace's enabled gateways. The reference
rule "one payment attempt per order id" is kept: every try is a fresh order
(``CO`` + 16 letters and digits, valid for every gateway), all linked to the
session. The session is completed in the same transaction that marks one of
its orders paid (see ``engine.store``).

A payment link is a reusable page that creates a session per visitor.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bdpay.core import BdPayError, Status, fmt_amount

from .. import catalog
from ..context import Actor, Scope
from ..db import session_scope, utcnow
from ..engine.orders import iso
from ..engine.runtime import EnvInfo, Runtime
from ..errors import ServiceError
from ..ids import new_order_ref, new_public_token
from ..models import CheckoutSession, Order, PaymentLink
from . import audit, gateways
from .validation import clean_amount, clean_customer, clean_metadata, clean_url, invalid

REUSE_PENDING_MINUTES = 15
COLLECTABLE = ("name", "phone", "email")


class CheckoutError(ServiceError):
    def __init__(self, message: str, status: int = 400, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


class AlreadyPaid(CheckoutError):
    def __init__(self) -> None:
        super().__init__("This checkout has already been paid", 409, "DUPLICATE")


# ------------------------------------------------------------------ sessions
def session_json(s: CheckoutSession, public_url: str) -> dict[str, Any]:
    return {
        "id": s.id,
        "object": "checkout.session",
        "mode": s.mode,
        "url": f"{public_url}/checkout/{s.id}",
        "status": s.status,
        "amount": fmt_amount(s.amount_poisha),
        "amount_poisha": s.amount_poisha,
        "currency": s.currency,
        "reference": s.reference,
        "description": s.description,
        "customer": s.customer,
        "metadata": s.meta,
        "gateways": list(s.allowed_gateways),
        "success_url": s.success_url,
        "cancel_url": s.cancel_url,
        "paid_order_id": s.paid_order_id,
        "payment_link_id": s.payment_link_id,
        "expires_at": iso(s.expires_at),
        "created_at": iso(s.created_at),
        "completed_at": iso(s.completed_at),
    }


def _clean_gateways(rt: Runtime, mode: str, ids: list[str] | None) -> list[str]:
    ids = list(dict.fromkeys(ids or []))
    known = {g.id for g in gateways.available(rt.settings, mode)}
    unknown = [g for g in ids if g not in known]
    if unknown:
        raise invalid(f"unknown gateway: {', '.join(unknown)}")
    return ids


def create_session(
    db: Session,
    rt: Runtime,
    env: EnvInfo,
    *,
    amount: Any,
    reference: str | None = None,
    description: str = "",
    customer: Any = None,
    metadata: Any = None,
    gateways_: list[str] | None = None,
    success_url: str | None = None,
    cancel_url: str | None = None,
    locale: str | None = None,
    expires_in_minutes: int | None = None,
    payment_link_id: str | None = None,
) -> CheckoutSession:
    scope, settings = env.scope, rt.settings
    if reference is not None and not 1 <= len(str(reference)) <= 100:
        raise invalid("reference must be 1 to 100 characters")
    allowed = _clean_gateways(rt, scope.mode, gateways_)
    if not options(db, rt, scope, allowed):
        raise invalid("no payment method is enabled for this checkout: enable a gateway first")
    ttl = expires_in_minutes if expires_in_minutes is not None else settings.checkout_session_ttl_minutes
    if not 5 <= ttl <= 7 * 24 * 60:
        raise invalid("expires_in_minutes must be between 5 and 10080")
    s = CheckoutSession(
        id=new_public_token("cs"),
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        amount_poisha=clean_amount(amount),
        reference=str(reference) if reference is not None else None,
        description=(description or "").strip()[:200],
        customer=clean_customer(customer),
        meta=clean_metadata(metadata),
        allowed_gateways=allowed,
        success_url=clean_url(settings, scope.mode, success_url, "success_url"),
        cancel_url=clean_url(settings, scope.mode, cancel_url, "cancel_url"),
        locale=locale if locale in ("en", "bn") else None,
        payment_link_id=payment_link_id,
        expires_at=utcnow() + timedelta(minutes=ttl),
    )
    db.add(s)
    db.flush()
    return s


def get_session(db: Session, scope: Scope, session_id: str, lock: bool = False) -> CheckoutSession | None:
    q = select(CheckoutSession).where(
        CheckoutSession.id == session_id,
        CheckoutSession.workspace_id == scope.workspace_id,
        CheckoutSession.mode == scope.mode,
    )
    return db.scalar(q.with_for_update() if lock else q)


def find_public(db: Session, session_id: str) -> CheckoutSession | None:
    """Lookup for the hosted page: the id itself is the capability (130 bits)."""
    if not session_id.startswith("cs_") or len(session_id) > 64:
        return None
    return db.get(CheckoutSession, session_id)


def list_sessions(db: Session, scope: Scope, *, status: str | None = None, limit: int = 50) -> list[CheckoutSession]:
    q = select(CheckoutSession).where(
        CheckoutSession.workspace_id == scope.workspace_id, CheckoutSession.mode == scope.mode
    )
    if status:
        q = q.where(CheckoutSession.status == status)
    return list(db.scalars(q.order_by(CheckoutSession.created_at.desc()).limit(min(limit, 100))))


def is_open(s: CheckoutSession) -> bool:
    return s.status == "open" and s.expires_at > utcnow()


def options(db: Session, rt: Runtime, scope: Scope, allowed: list[str] | None = None) -> list[dict[str, str]]:
    """Payment methods to offer, in the workspace's order, with customer-facing labels."""
    out = []
    for row in gateways.enabled_gateways(db, rt, scope):
        if allowed and row.gateway not in allowed:
            continue
        spec = catalog.load().get(row.gateway)
        labels = (spec.raw.get("checkout") if spec else None) or {}
        out.append(
            {
                "id": row.gateway,
                "label": labels.get("label") or (spec.name if spec else row.gateway),
                "hint": labels.get("hint", ""),
                "label_bn": labels.get("label_bn") or labels.get("label", ""),
                "hint_bn": labels.get("hint_bn") or labels.get("hint", ""),
            }
        )
    return out


def attempts(db: Session, scope: Scope, session_id: str) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .where(
                Order.workspace_id == scope.workspace_id,
                Order.mode == scope.mode,
                Order.checkout_session_id == session_id,
            )
            .order_by(Order.id.desc())
            .limit(20)
        )
    )


def start_attempt(
    rt: Runtime, env: EnvInfo, session_id: str, gateway: str, customer_extra: dict[str, str] | None = None
) -> str:
    """Create the next attempt of a session and return the provider URL."""
    scope = env.scope
    pay = rt.pay(env)
    # One attempt at a time per session: a double click must not open two payments.
    with pay.store.exclusive(session_id, "checkout"):
        with session_scope(rt.sessions) as db:
            s = get_session(db, scope, session_id)
            if s is None:
                raise CheckoutError("This checkout does not exist", 404, "NOT_FOUND")
            if s.status == "paid":
                raise AlreadyPaid()
            if not is_open(s):
                raise CheckoutError("This checkout has expired. Ask the merchant for a new link.", 409, "DUPLICATE")
            if gateway not in {o["id"] for o in options(db, rt, scope, list(s.allowed_gateways))}:
                raise CheckoutError("That payment method is not available for this checkout")
            pending = [(o.order_id, o.gateway) for o in attempts(db, scope, session_id) if o.status == "pending"][:3]
            amount, description, reference = s.amount_poisha, s.description, s.reference
            customer, meta, source = dict(s.customer), dict(s.meta), "link" if s.payment_link_id else "checkout"
        # An earlier try may have been paid without us hearing about it (closed tab,
        # lost callback). Ask before letting the customer pay a second time.
        for order_id, order_gateway in pending:
            try:
                checked = pay.requery(order_id)
            except BdPayError:
                continue
            if checked.status == Status.PAID:
                raise AlreadyPaid()
            fresh = utcnow().timestamp() - checked.created_at < REUSE_PENDING_MINUTES * 60
            if (
                checked.status == Status.PENDING
                and order_gateway == gateway
                and fresh
                and getattr(checked, "redirect_url", None)
            ):
                return checked.redirect_url  # same method again: same provider page
        customer.update(customer_extra or {})
        if description:
            customer.setdefault("product", description[:100])
        meta["checkout_session"] = session_id
        if reference:
            meta.setdefault("reference", reference[:500])
        order_id = new_order_ref()
        pay.create_order(
            order_id,
            fmt_amount(amount),
            gateway,
            customer,
            metadata=meta,
            source=source,
            checkout_session_id=session_id,
        )
        return pay.start_payment(order_id)


def cancel_session(db: Session, scope: Scope, session_id: str) -> CheckoutSession | None:
    s = get_session(db, scope, session_id, lock=True)
    if s is not None and s.status == "open":
        s.status, s.completed_at = "cancelled", utcnow()
    return s


def expire_due(rt: Runtime) -> int:
    with rt.engine.begin() as conn:
        return conn.execute(
            text(
                "UPDATE checkout_sessions SET status = 'expired', completed_at = now() "
                "WHERE status = 'open' AND expires_at <= now()"
            )
        ).rowcount


# --------------------------------------------------------------------- links
def link_json(link: PaymentLink, public_url: str) -> dict[str, Any]:
    def money(v: int | None) -> str | None:
        return fmt_amount(v) if v is not None else None

    return {
        "id": link.id,
        "object": "payment_link",
        "mode": link.mode,
        "url": f"{public_url}/pay/{link.id}",
        "title": link.title,
        "description": link.description,
        "amount": money(link.amount_poisha),
        "amount_poisha": link.amount_poisha,
        "min_amount": money(link.min_poisha),
        "max_amount": money(link.max_poisha),
        "gateways": list(link.allowed_gateways),
        "collect": list(link.collect),
        "success_url": link.success_url,
        "active": link.active,
        "max_payments": link.max_payments,
        "paid_count": link.paid_count,
        "created_at": iso(link.created_at),
    }


def create_link(
    db: Session,
    rt: Runtime,
    env: EnvInfo,
    *,
    title: str,
    description: str = "",
    amount: Any = None,
    min_amount: Any = None,
    max_amount: Any = None,
    gateways_: list[str] | None = None,
    collect: list[str] | None = None,
    success_url: str | None = None,
    max_payments: int | None = None,
    actor: Actor,
    user_id: str | None,
) -> PaymentLink:
    scope = env.scope
    title = " ".join((title or "").split())
    if not 1 <= len(title) <= 100:
        raise invalid("title must be 1 to 100 characters")
    fixed = clean_amount(amount) if amount not in (None, "") else None
    low = clean_amount(min_amount) if min_amount not in (None, "") else None
    high = clean_amount(max_amount) if max_amount not in (None, "") else None
    if fixed is None:
        low = low or 1000  # BDT 10.00
        high = high or 50_000_00  # BDT 50,000.00
        if low > high:
            raise invalid("min_amount cannot be larger than max_amount")
    else:
        low = high = None
    collect = list(dict.fromkeys(collect if collect is not None else ["name", "phone"]))
    if any(c not in COLLECTABLE for c in collect):
        raise invalid(f"collect can contain: {', '.join(COLLECTABLE)}")
    if max_payments is not None and max_payments < 1:
        raise invalid("max_payments must be at least 1")
    link = PaymentLink(
        id=new_public_token("plink"),
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        title=title,
        description=(description or "").strip()[:500],
        amount_poisha=fixed,
        min_poisha=low,
        max_poisha=high,
        allowed_gateways=_clean_gateways(rt, scope.mode, gateways_),
        collect=collect,
        success_url=clean_url(rt.settings, scope.mode, success_url, "success_url"),
        max_payments=max_payments,
        created_by=user_id,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        actor,
        "payment_link.created",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="payment_link",
        target_id=link.id,
        detail={"title": title},
    )
    return link


def get_link(db: Session, scope: Scope, link_id: str, lock: bool = False) -> PaymentLink | None:
    q = select(PaymentLink).where(
        PaymentLink.id == link_id, PaymentLink.workspace_id == scope.workspace_id, PaymentLink.mode == scope.mode
    )
    return db.scalar(q.with_for_update() if lock else q)


def find_public_link(db: Session, link_id: str) -> PaymentLink | None:
    if not link_id.startswith("plink_") or len(link_id) > 64:
        return None
    return db.get(PaymentLink, link_id)


def list_links(db: Session, scope: Scope) -> list[PaymentLink]:
    return list(
        db.scalars(
            select(PaymentLink)
            .where(PaymentLink.workspace_id == scope.workspace_id, PaymentLink.mode == scope.mode)
            .order_by(PaymentLink.created_at.desc())
            .limit(200)
        )
    )


def set_link_active(db: Session, scope: Scope, link_id: str, active: bool, actor: Actor) -> PaymentLink:
    link = get_link(db, scope, link_id, lock=True)
    if link is None:
        raise CheckoutError("No such payment link", 404, "NOT_FOUND")
    link.active, link.updated_at = active, utcnow()
    audit.record(
        db,
        actor,
        "payment_link.activated" if active else "payment_link.deactivated",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="payment_link",
        target_id=link.id,
    )
    return link


def link_accepts_payments(link: PaymentLink) -> bool:
    return link.active and (link.max_payments is None or link.paid_count < link.max_payments)


def session_from_link(
    db: Session, rt: Runtime, env: EnvInfo, link: PaymentLink, *, amount: Any, customer: dict[str, str]
) -> CheckoutSession:
    if not link_accepts_payments(link):
        raise CheckoutError("This payment link is no longer accepting payments", 409, "DUPLICATE")
    if link.amount_poisha is not None:
        poisha = link.amount_poisha
    else:
        poisha = clean_amount(amount)
        if poisha < (link.min_poisha or 0) or poisha > (link.max_poisha or poisha):
            raise invalid(
                f"Enter an amount between {fmt_amount(link.min_poisha or 0)} "
                f"and {fmt_amount(link.max_poisha or poisha)}"
            )
    missing = [c for c in link.collect if not customer.get(c)]
    if missing:
        raise invalid(f"Please enter your {' and '.join(missing)}")
    return create_session(
        db,
        rt,
        env,
        amount=fmt_amount(poisha),
        description=link.title,
        customer={k: v for k, v in customer.items() if k in COLLECTABLE and v},
        metadata={"payment_link": link.id},
        gateways_=list(link.allowed_gateways),
        success_url=link.success_url,
        payment_link_id=link.id,
    )
