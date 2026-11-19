"""Request dependencies: who is calling, and for which tenant.

Two kinds of caller:

* a person in the dashboard: session cookie, CSRF header on writes, and a
  workspace role (``/api/...``);
* a merchant's server: API key bound to one workspace environment (``/v1/...``).

Every tenant route resolves to a ``Scope`` here, before any handler runs.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, Request

from .. import metrics
from ..context import Actor, Scope
from ..db import session_scope
from ..engine.runtime import EnvInfo, Runtime
from ..models import User, Workspace
from ..services import apikeys, auth, workspaces
from ..settings import Settings
from .errors import ApiError

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def get_rt(request: Request) -> Runtime:
    return request.app.state.rt


def get_settings(request: Request) -> Settings:
    return request.app.state.rt.settings


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str, key: str, limit: int, window: int) -> None:
    hit = request.app.state.limiter.hit(f"{bucket}:{key}", limit, window)
    if not hit.allowed:
        metrics.RATE_LIMITED.labels(bucket).inc()
        raise ApiError(
            429,
            "RATE_LIMITED",
            "Too many requests. Slow down and try again shortly.",
            headers={"Retry-After": str(hit.retry_after)},
        )


# ------------------------------------------------------------------ dashboard
@dataclass
class UserCtx:
    user: User
    session_id: str
    csrf_token: str
    ip: str

    @property
    def actor(self) -> Actor:
        return Actor("user", self.user.id, self.user.email, self.ip)


def _same_origin(settings: Settings, request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or not settings.is_production:
        return True
    want, got = urlparse(settings.public_url), urlparse(origin)
    return (want.scheme, want.netloc) == (got.scheme, got.netloc)


def current_user(request: Request, rt: Runtime = Depends(get_rt)) -> UserCtx:
    token = request.cookies.get(auth.cookie_name(rt.settings), "")
    with session_scope(rt.sessions) as db:
        resolved = auth.resolve_session(db, rt.settings, token)
        if resolved is None:
            raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue")
        session, user = resolved
        ctx = UserCtx(user, session.id, session.csrf_token, client_ip(request))
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, ctx.csrf_token) or not _same_origin(rt.settings, request):
            raise ApiError(403, "FORBIDDEN", "This request is missing its security token. Reload the page and retry.")
    return ctx


def platform_admin(user: UserCtx = Depends(current_user)) -> UserCtx:
    if not user.user.is_platform_admin:
        raise ApiError(404, "NOT_FOUND", "Not found")
    return user


@dataclass
class WsCtx:
    user: UserCtx
    workspace: Workspace
    role: str
    env: EnvInfo | None = None

    @property
    def scope(self) -> Scope:
        assert self.env is not None
        return self.env.scope

    @property
    def actor(self) -> Actor:
        return self.user.actor

    def need(self, minimum: str) -> None:
        if not workspaces.role_at_least(self.role, minimum):
            raise ApiError(403, "FORBIDDEN", f"Your role ({self.role}) cannot do this. It needs {minimum} or higher.")

    def need_write(self) -> None:
        """Changing things: developers may in sandbox, live needs an admin."""
        self.need("admin" if self.env is not None and self.env.scope.mode == "live" else "developer")


def workspace_ctx(slug: str, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)) -> WsCtx:
    with session_scope(rt.sessions) as db:
        from sqlalchemy import select

        ws = db.scalar(select(Workspace).where(Workspace.slug == slug, Workspace.archived_at.is_(None)))
        member = workspaces.membership(db, ws.id, user.user.id) if ws else None
        # Same answer whether the workspace is missing or simply not yours.
        if ws is None or member is None:
            raise ApiError(404, "NOT_FOUND", "No such workspace")
        return WsCtx(user, ws, member.role)


def env_ctx(mode: str, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)) -> WsCtx:
    if mode not in ("sandbox", "live"):
        raise ApiError(404, "NOT_FOUND", "Mode must be sandbox or live")
    env = rt.load_env(Scope(ctx.workspace.id, mode))
    if env is None:
        raise ApiError(404, "NOT_FOUND", "No such environment")
    if mode == "live" and env.live_status != "active":
        raise ApiError(403, "LIVE_NOT_ACTIVE", "Live mode is not active for this workspace yet")
    ctx.env = env
    return ctx


# --------------------------------------------------------------- merchant API
@dataclass
class KeyCtx:
    auth: apikeys.KeyAuth

    @property
    def scope(self) -> Scope:
        return self.auth.scope

    @property
    def env(self) -> EnvInfo:
        return self.auth.env

    @property
    def actor(self) -> Actor:
        return self.auth.actor

    def need(self, scope_name: str) -> None:
        if not self.auth.allows(scope_name):
            raise ApiError(403, "FORBIDDEN", f"This API key does not have the '{scope_name}' permission")


def _presented_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Drop-in compatibility with self-hosted bdpay, which uses this header.
    return request.headers.get("x-bdpay-admin", "").strip()


def api_key(request: Request, rt: Runtime = Depends(get_rt)) -> KeyCtx:
    presented = _presented_key(request)
    if not presented:
        raise ApiError(
            401,
            "UNAUTHENTICATED",
            "Send your API key as 'Authorization: Bearer <key>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    found = apikeys.authenticate(rt, presented)
    if found is None:
        rate_limit(request, "bad_key", client_ip(request), 30, 60)
        raise ApiError(
            401, "UNAUTHENTICATED", "Invalid, expired or revoked API key", headers={"WWW-Authenticate": "Bearer"}
        )
    if found.env.suspended:
        raise ApiError(403, "FORBIDDEN", "This workspace is suspended. Contact the platform operator.")
    if found.env.archived:
        raise ApiError(403, "FORBIDDEN", "This workspace has been archived")
    if found.scope.mode == "live" and found.env.live_status != "active":
        raise ApiError(403, "LIVE_NOT_ACTIVE", "Live mode is not active for this workspace")
    rate_limit(request, "api", found.key_id, rt.settings.api_rate_per_minute, 60)
    return KeyCtx(found)
