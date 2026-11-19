"""Gateway configuration: the hosted configurator.

A workspace stores its *own* credentials for each gateway, per environment.
Rules carried over from the original configurator:

* required fields must be filled before a gateway can be saved;
* published sandbox demo values are refused in live mode;
* the experimental UddoktaPay driver needs its compliance box ticked.

Rules a hosted service adds:

* secret values are write-only. They are encrypted, never returned by any
  API, and the dashboard only ever sees "set" plus a short hint;
* a blank secret field on save means "keep the stored one";
* URL fields may only point at public addresses (see ``security.netguard``).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from bdpay.core import BdPayError
from bdpay.drivers.nagad import load_private_key, load_public_key
from bdpay.http import redact

from .. import catalog
from ..catalog import GatewaySpec
from ..context import Actor, Scope
from ..crypto import aad_gateway
from ..db import utcnow
from ..engine.drivers import driver_class
from ..engine.orders import iso
from ..engine.runtime import EnvInfo, Runtime
from ..errors import ServiceError
from ..ids import rand_b32, sha256_hex
from ..models import ApiKey, GatewayConfig, Order, WebhookEndpoint, WorkerHeartbeat
from ..security.netguard import check_public_url
from ..settings import Settings
from . import audit

MAX_TEXT = 500
MAX_KEY_MATERIAL = 8000
TOKEN_GATEWAYS = ("bkash", "shurjopay", "eps", "upay")


class GatewayError(ServiceError):
    def __init__(
        self, message: str, status: int = 400, code: str = "VALIDATION", errors: list[dict[str, str]] | None = None
    ):
        super().__init__(message)
        self.message, self.status, self.code, self.errors = message, status, code, errors or []


# ------------------------------------------------------------------ catalog
def spec_for(gateway: str) -> GatewaySpec:
    spec = catalog.load().get(gateway)
    if spec is None:
        raise GatewayError(f"Unknown gateway '{gateway}'", 404, "NOT_FOUND")
    return spec


def unavailable_reason(settings: Settings, spec: GatewaySpec, mode: str) -> str | None:
    if spec.id == "simulator" and not settings.simulator:
        return "The simulator is switched off on this deployment"
    if spec.sandbox_only and mode == "live":
        return f"{spec.name} exists only in sandbox mode"
    if spec.experimental and not settings.allow_experimental:
        return "Experimental gateways are switched off on this deployment"
    return None


def available(settings: Settings, mode: str) -> list[GatewaySpec]:
    return [g for g in catalog.load().gateways if unavailable_reason(settings, g, mode) is None]


def refund_capability(gateway: str) -> str:
    """none | full | partial, straight from the driver class."""
    try:
        return driver_class(gateway).capabilities.refunds
    except BdPayError:
        return "none"


# --------------------------------------------------------------- validation
def _hint(spec_field, value: str) -> str:
    if spec_field.type == "textarea":
        clean = value.strip()
        return f"key, {len(clean)} characters, fingerprint {sha256_hex(clean)[:8]}"
    return f"•••• {value[-4:]}" if len(value) >= 12 else "••••"


def validate(spec: GatewaySpec, mode: str, values: dict[str, str], settings: Settings) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def bad(key: str, message: str) -> None:
        errors.append({"field": key, "message": message})

    for f in spec.fields:
        v = values.get(f.key) or ""
        if not v:
            if f.required:
                bad(f.key, "Tick the compliance box" if f.type == "check" else f"{f.label} is required")
            continue
        if len(v) > (MAX_KEY_MATERIAL if f.type == "textarea" else MAX_TEXT):
            bad(f.key, f"{f.label} is too long")
            continue
        if f.type == "select" and v not in f.options:
            bad(f.key, f"{f.label}: choose one of the listed options")
        if f.is_url:
            try:
                check_public_url(v, allow_http=(mode == "sandbox"), allow_private=settings.allow_private_targets)
            except ValueError as e:
                bad(f.key, f"{f.label} {e}")
            else:
                if not settings.base_url_host_allowed(urlparse(v).hostname or ""):
                    bad(f.key, f"{f.label}: this host is not on the deployment's list of allowed gateway hosts")
        if mode == "live" and f.demo and v == f.demo:
            bad(f.key, f"{f.label} is a sandbox demo value; live mode needs your live credentials")
    if spec.id == "nagad":
        if values.get("NAGAD_PRIVATE_KEY"):
            try:
                if not isinstance(load_private_key(values["NAGAD_PRIVATE_KEY"]), rsa.RSAPrivateKey):
                    raise ValueError
            except (ValueError, TypeError):
                bad("NAGAD_PRIVATE_KEY", "Your merchant private key is not a PEM or base64 RSA private key")
        if values.get("NAGAD_PUBLIC_KEY"):
            try:
                if not isinstance(load_public_key(values["NAGAD_PUBLIC_KEY"]), rsa.RSAPublicKey):
                    raise ValueError
            except (ValueError, TypeError):
                bad("NAGAD_PUBLIC_KEY", "The Nagad public key is not a PEM or base64 RSA public key")
    return errors


# --------------------------------------------------------------------- views
def provider_urls(settings: Settings, env: EnvInfo, spec: GatewaySpec) -> dict[str, Any]:
    base = f"{settings.public_url}/p/{env.public_key}"
    return {"callback": f"{base}/callback/{spec.id}/", "ipn": f"{base}/ipn/{spec.id}" if spec.ipn else None}


def view(settings: Settings, env: EnvInfo, spec: GatewaySpec, row: GatewayConfig | None) -> dict[str, Any]:
    config = dict(row.config) if row else {}
    hints = dict(row.secret_hints) if row else {}
    return {
        "id": spec.id,
        "configured": row is not None,
        "enabled": bool(row and row.enabled),
        "status": row.status if row else "unconfigured",
        "values": {f.key: config.get(f.key, "") for f in spec.fields if not f.is_secret},
        "secrets": {f.key: {"set": f.key in hints, "hint": hints.get(f.key, "")} for f in spec.fields if f.is_secret},
        "position": row.position if row else 0,
        "refunds": refund_capability(spec.id),
        "urls": provider_urls(settings, env, spec),
        "last_test_at": iso(row.last_test_at) if row else None,
        "last_test": row.last_test_detail if row else None,
        "last_success_at": iso(row.last_success_at) if row else None,
        "last_error_at": iso(row.last_error_at) if row else None,
        "last_error": row.last_error if row else None,
        "updated_at": iso(row.updated_at) if row else None,
    }


def _rows(db: Session, scope: Scope) -> dict[str, GatewayConfig]:
    rows = db.scalars(
        select(GatewayConfig).where(GatewayConfig.workspace_id == scope.workspace_id, GatewayConfig.mode == scope.mode)
    )
    return {r.gateway: r for r in rows}


def listing(db: Session, rt: Runtime, env: EnvInfo) -> list[dict[str, Any]]:
    rows = _rows(db, env.scope)
    out = [view(rt.settings, env, spec, rows.get(spec.id)) for spec in available(rt.settings, env.scope.mode)]
    out.sort(key=lambda v: (not v["configured"], v["position"]))
    return out


def enabled_gateways(db: Session, rt: Runtime, scope: Scope) -> list[GatewayConfig]:
    """Enabled gateways in the order the workspace arranged them."""
    rows = db.scalars(
        select(GatewayConfig)
        .where(
            GatewayConfig.workspace_id == scope.workspace_id, GatewayConfig.mode == scope.mode, GatewayConfig.enabled
        )
        .order_by(GatewayConfig.position, GatewayConfig.created_at)
    )
    return [
        r for r in rows if (s := catalog.load().get(r.gateway)) and not unavailable_reason(rt.settings, s, scope.mode)
    ]


# -------------------------------------------------------------------- writes
def save(
    db: Session,
    rt: Runtime,
    env: EnvInfo,
    gateway: str,
    *,
    values: dict[str, Any],
    enabled: bool | None,
    clear: list[str] | None,
    actor: Actor,
    user_id: str | None,
) -> dict[str, Any]:
    scope, spec = env.scope, spec_for(gateway)
    reason = unavailable_reason(rt.settings, spec, scope.mode)
    if reason:
        raise GatewayError(reason, 403, "FORBIDDEN")
    known = {f.key: f for f in spec.fields}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise GatewayError(f"Unknown setting for {spec.name}: {', '.join(unknown)}")
    aad = aad_gateway(scope.workspace_id, scope.mode, gateway)
    row = db.get(GatewayConfig, (scope.workspace_id, scope.mode, gateway), with_for_update=True)
    config = dict(row.config) if row else {}
    secrets = rt.vault.decrypt_json(row.secrets_enc, aad) if row else {}
    changed: list[str] = []
    for key in clear or []:
        if key in known and known[key].is_secret and key in secrets:
            secrets.pop(key)
            changed.append(key)
    for key, raw in values.items():
        f = known[key]
        v = "" if raw is None else str(raw).strip()
        if f.type == "check":
            v = "1" if (raw is True or v.lower() in ("1", "true", "on", "yes")) else ""
        if f.is_secret:
            if v and secrets.get(key) != v:  # blank keeps what is stored
                secrets[key] = v
                changed.append(key)
        elif (config.get(key) or "") != v:
            changed.append(key)
            if v:
                config[key] = v
            else:
                config.pop(key, None)
    if row is None:
        for f in spec.fields:
            if f.default and not f.is_secret and f.key not in values and f.key not in config:
                config[f.key] = f.default
    errors = validate(spec, scope.mode, {**config, **secrets}, rt.settings)
    if errors:
        raise GatewayError(f"{spec.name}: fix {len(errors)} field{'s' if len(errors) > 1 else ''}", errors=errors)
    now = utcnow()
    if row is None:
        top = db.scalar(
            select(func.coalesce(func.max(GatewayConfig.position), 0)).where(
                GatewayConfig.workspace_id == scope.workspace_id, GatewayConfig.mode == scope.mode
            )
        )
        row = GatewayConfig(
            workspace_id=scope.workspace_id,
            mode=scope.mode,
            gateway=gateway,
            position=top + 1,
            enabled=True if enabled is None else enabled,
            status="unverified",
        )
        db.add(row)
    elif enabled is not None:
        row.enabled = enabled
    row.config = config
    row.secrets_enc = rt.vault.encrypt_json(secrets, aad) if secrets else None
    row.secret_hints = {k: _hint(known[k], v) for k, v in secrets.items() if k in known}
    row.updated_by, row.updated_at = user_id, now
    if changed:
        # New credentials have not been proven yet.
        row.status = "verified" if gateway == "simulator" else "unverified"
        row.last_error = row.last_error_at = row.last_test_at = row.last_test_detail = None
    db.flush()
    audit.record(
        db,
        actor,
        "gateway.saved",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="gateway",
        target_id=gateway,
        detail={"changed_fields": sorted(changed), "enabled": row.enabled},
    )
    return view(rt.settings, env, spec, row)


def set_enabled(db: Session, rt: Runtime, env: EnvInfo, gateway: str, enabled: bool, actor: Actor) -> dict[str, Any]:
    scope, spec = env.scope, spec_for(gateway)
    row = db.get(GatewayConfig, (scope.workspace_id, scope.mode, gateway), with_for_update=True)
    if row is None:
        raise GatewayError(f"{spec.name} is not configured yet", 404, "NOT_FOUND")
    if enabled:
        reason = unavailable_reason(rt.settings, spec, scope.mode)
        if reason:
            raise GatewayError(reason, 403, "FORBIDDEN")
        secrets = rt.vault.decrypt_json(row.secrets_enc, aad_gateway(scope.workspace_id, scope.mode, gateway))
        errors = validate(spec, scope.mode, {**row.config, **secrets}, rt.settings)
        if errors:
            raise GatewayError(f"{spec.name} cannot be enabled until its settings are complete", errors=errors)
    row.enabled, row.updated_at = enabled, utcnow()
    db.flush()
    audit.record(
        db,
        actor,
        "gateway.enabled" if enabled else "gateway.disabled",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="gateway",
        target_id=gateway,
    )
    return view(rt.settings, env, spec, row)


def reorder(db: Session, scope: Scope, order: list[str]) -> None:
    rows = _rows(db, scope)
    arranged = [g for g in order if g in rows] + [g for g in rows if g not in order]
    for position, gateway in enumerate(arranged, start=1):
        rows[gateway].position = position
    db.flush()


def delete(db: Session, rt: Runtime, env: EnvInfo, gateway: str, actor: Actor) -> None:
    scope, spec = env.scope, spec_for(gateway)
    row = db.get(GatewayConfig, (scope.workspace_id, scope.mode, gateway), with_for_update=True)
    if row is None:
        return
    in_flight = db.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.workspace_id == scope.workspace_id,
            Order.mode == scope.mode,
            Order.gateway == gateway,
            Order.status == "pending",
        )
    )
    if in_flight:
        raise GatewayError(
            f"{in_flight} payment{'s are' if in_flight > 1 else ' is'} still in progress with "
            f"{spec.name}. Disable it now and remove the credentials once they have settled.",
            409,
            "DUPLICATE",
        )
    db.delete(row)
    db.flush()
    audit.record(
        db,
        actor,
        "gateway.removed",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="gateway",
        target_id=gateway,
    )


# --------------------------------------------------------------- connection
_SSL_API = {
    "FAILED": "SSLCOMMERZ rejected the store ID or store password",
    "INVALID_REQUEST": "SSLCOMMERZ says the request is invalid: check the store ID",
    "INACTIVE": "SSLCOMMERZ says this store is inactive",
}


def _probe(spec: GatewaySpec, driver) -> tuple[str, str]:
    """(result, message). result: verified | rejected | reachable | unreachable."""
    gid = spec.id
    if gid == "simulator":
        return "verified", "The simulator is built in and always answers."
    try:
        if gid in TOKEN_GATEWAYS:
            # A fresh token grant proves the credentials; keep the token so it is not wasted.
            granted = driver._grant()
            driver.tokens.invalidate(driver.token_key)
            driver.tokens.get_or_fetch(driver.token_key, lambda: granted)
            return "verified", f"{spec.name} accepted these credentials and issued an access token."
        if gid == "sslcommerz":
            j = driver.http.json(
                gid,
                "GET",
                f"{driver.host}/validator/api/merchantTransIDvalidationAPI.php",
                params={"tran_id": "BDPAYCHECK" + rand_b32(8).upper(), **driver._auth()},
            )
            api = str(j.get("APIConnect") or "")
            if api == "DONE":
                return "verified", "SSLCOMMERZ accepted the store ID and password."
            if api in _SSL_API:
                return "rejected", _SSL_API[api] + "."
            return "reachable", "SSLCOMMERZ answered, but did not say whether the credentials are valid."
        base = getattr(driver, "base", None) or getattr(driver, "host", None) or getattr(driver, "url", "")
        driver.http.request(gid, "GET", base)
        return "reachable", (
            f"{spec.name} is reachable. Its API has no call that checks credentials without "
            "creating a payment, so run one test payment to confirm them."
        )
    except BdPayError as e:
        message = str(redact(e.message))[:300]
        if e.code in ("TIMEOUT", "PROVIDER_UNAVAILABLE"):
            return "unreachable", f"Could not reach {spec.name}: {message}"
        return "rejected", message


def test_connection(rt: Runtime, env: EnvInfo, gateway: str) -> dict[str, Any]:
    scope, spec = env.scope, spec_for(gateway)
    try:
        driver = rt.driver_for(scope, gateway)
        result, message = _probe(spec, driver)
    except BdPayError as e:
        result, message = "rejected", str(redact(e.message))[:300]
    detail = {"result": result, "message": message, "checked_at": iso(utcnow())}
    with rt.engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE gateway_configs
               SET last_test_at = now(), last_test_detail = CAST(:detail AS jsonb),
                   status = CASE WHEN :result = 'verified' THEN 'verified'
                                 WHEN :result = 'rejected' THEN 'rejected' ELSE status END,
                   last_error = CASE WHEN :result = 'verified' THEN NULL ELSE last_error END
             WHERE workspace_id = :ws AND mode = :mode AND gateway = :g"""),
            {**scope.params, "g": gateway, "result": result, "detail": json.dumps(detail)},
        )
    return detail


# ---------------------------------------------------------- go-live checklist
def checklist(db: Session, rt: Runtime, env: EnvInfo) -> list[dict[str, Any]]:
    """What is left before (or while) taking real money. ``state`` is ``done``,
    ``todo``, or ``manual`` for steps only the merchant can confirm."""
    scope, settings = env.scope, rt.settings
    rows = enabled_gateways(db, rt, scope)
    confirmed = (
        db.execute(
            text("SELECT settings FROM workspace_envs WHERE workspace_id = :ws AND mode = :mode"), scope.params
        ).scalar()
        or {}
    ).get("confirmed", {})
    items: list[dict[str, Any]] = []

    def add(item_id: str, label: str, state: str, detail: str = "", gateway: str | None = None) -> None:
        if state == "manual" and item_id in confirmed:
            state = "done"
        items.append(
            {
                "id": item_id,
                "label": label,
                "state": state,
                "detail": detail,
                "gateway": gateway,
                "manual": state == "manual" or item_id in confirmed,
            }
        )

    def count(model, *where) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == scope.workspace_id, model.mode == scope.mode, *where)
            )
            or 0
        )

    add("gateway", "At least one gateway is enabled", "done" if rows else "todo", ", ".join(r.gateway for r in rows))
    add(
        "api_key", "An API key exists for your server", "done" if count(ApiKey, ApiKey.revoked_at.is_(None)) else "todo"
    )
    add(
        "webhook",
        "A webhook endpoint receives payment results",
        "done" if count(WebhookEndpoint, WebhookEndpoint.enabled) else "todo",
        "Without one, your server must poll each order.",
    )
    beat = db.get(WorkerHeartbeat, "worker")
    alive = bool(beat and (utcnow() - beat.last_beat).total_seconds() < 180)
    add(
        "worker",
        "The reconcile worker is running",
        "done" if alive else "todo",
        "It re-checks payments whose redirect or callback was lost.",
    )
    if scope.mode == "live":
        add(
            "https",
            "This service is served over https",
            "done" if settings.public_url.startswith("https://") else "todo",
            settings.public_url,
        )
    for r in rows:
        spec = catalog.load().get(r.gateway)
        if spec is None or spec.id == "simulator":
            continue
        paid = count(Order, Order.gateway == r.gateway, Order.paid_at.is_not(None))
        add(
            f"{spec.id}.payment",
            f"{spec.name}: one real payment completed"
            if scope.mode == "live"
            else f"{spec.name}: one sandbox payment completed",
            "done" if paid else "todo",
            gateway=spec.id,
        )
        if scope.mode == "live" and refund_capability(spec.id) != "none":
            refunded = count(Order, Order.gateway == r.gateway, Order.refunded_poisha > 0)
            add(
                f"{spec.id}.refund",
                f"{spec.name}: one refund of the minimum amount",
                "done" if refunded else "todo",
                gateway=spec.id,
            )
        if spec.ipn:
            add(
                f"{spec.id}.ipn",
                f"{spec.name}: IPN URL registered in the provider panel",
                "manual",
                provider_urls(settings, env, spec)["ipn"] or "",
                gateway=spec.id,
            )
        if spec.whitelist and scope.mode == "live":
            ips = ", ".join(settings.egress_ip_list) or "ask your platform operator for the outbound IP"
            add(
                f"{spec.id}.whitelist",
                f"{spec.name}: server IPv4 and callback domain whitelisted by {spec.name}",
                "manual",
                f"IP: {ips}. Domain: {settings.public_host}",
                gateway=spec.id,
            )
        if spec.stability == "beta":
            add(
                f"{spec.id}.beta",
                f"{spec.name} (beta driver): every field compared with the provider's pack",
                "manual",
                gateway=spec.id,
            )
    review = count(Order, Order.status == "needs_review")
    add(
        "needs_review",
        "No payments are waiting for manual review",
        "done" if not review else "todo",
        f"{review} order{'s' if review != 1 else ''} in needs_review" if review else "",
    )
    return items


def confirm_checklist_item(db: Session, env: EnvInfo, item_id: str, done: bool, actor: Actor) -> None:
    scope = env.scope
    current = (
        db.execute(
            text("SELECT settings FROM workspace_envs WHERE workspace_id = :ws AND mode = :mode FOR UPDATE"),
            scope.params,
        ).scalar()
        or {}
    )
    confirmed = dict(current.get("confirmed", {}))
    if done:
        confirmed[item_id[:80]] = {"at": iso(utcnow()), "by": actor.label}
    else:
        confirmed.pop(item_id, None)
    current["confirmed"] = confirmed
    db.execute(
        text("UPDATE workspace_envs SET settings = CAST(:s AS jsonb) WHERE workspace_id = :ws AND mode = :mode"),
        {**scope.params, "s": json.dumps(current)},
    )
    audit.record(
        db,
        actor,
        "checklist.confirmed" if done else "checklist.unconfirmed",
        workspace_id=scope.workspace_id,
        mode=scope.mode,
        target_type="checklist",
        target_id=item_id,
    )
