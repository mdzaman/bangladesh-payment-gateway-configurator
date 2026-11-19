"""Dashboard API: accounts, sessions, workspaces, members."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select

from .. import __version__, catalog
from ..context import Actor
from ..db import session_scope, utcnow
from ..engine.orders import iso
from ..engine.runtime import Runtime
from ..models import Invitation, Membership, User, UserSession, Workspace
from ..security import totp
from ..services import audit, auth, mailer, workspaces
from ..settings import Settings
from .deps import UserCtx, WsCtx, client_ip, current_user, get_rt, rate_limit, workspace_ctx
from .errors import ApiError
from .schemas import (
    ChangePasswordIn,
    EmailIn,
    GoLiveIn,
    InviteIn,
    LoginIn,
    ProfileIn,
    ResetIn,
    RoleIn,
    SignupIn,
    TokenIn,
    TwoFactorChangeIn,
    TwoFactorConfirmIn,
    TwoFactorLoginIn,
    TwoFactorSetupIn,
    WorkspaceIn,
    WorkspacePatch,
)

router = APIRouter(prefix="/api", tags=["dashboard"], include_in_schema=False)

ATTESTATION_VERSION = "2026-11"
ATTESTATION = (
    "I confirm that: (1) this business holds its own merchant account with every payment provider it configures "
    "here; (2) payments settle directly from the provider into those accounts, and this service never receives, "
    "holds or moves the money; (3) this workspace will not be used to collect payments on behalf of other people "
    "or businesses, or into personal wallets; and (4) I am authorised to accept this for the business."
)


# -------------------------------------------------------------------- shapes
def user_json(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "email_verified": u.email_verified_at is not None,
        "two_factor_enabled": u.totp_enabled_at is not None,
        "is_platform_admin": u.is_platform_admin,
        "created_at": iso(u.created_at),
    }


def two_factor_json(db, u: User) -> dict[str, Any]:
    enabled = u.totp_enabled_at is not None
    return {
        "enabled": enabled,
        "enabled_at": iso(u.totp_enabled_at),
        "setup_pending": auth.setup_pending(u),
        "recovery_codes_left": auth.recovery_codes_left(db, u.id) if enabled else 0,
    }


def workspace_json(ws: Workspace, role: str | None = None) -> dict[str, Any]:
    s = ws.settings or {}
    return {
        "id": ws.id,
        "slug": ws.slug,
        "name": ws.name,
        "role": role,
        "display_name": s.get("display_name", ""),
        "support_email": s.get("support_email", ""),
        "support_phone": s.get("support_phone", ""),
        "locale": s.get("locale", ""),
        "live_status": ws.live_status,
        "live_activated_at": iso(ws.live_activated_at),
        "suspended": ws.suspended_at is not None,
        "suspended_reason": ws.suspended_reason,
        "created_at": iso(ws.created_at),
    }


def platform_json(settings: Settings) -> dict[str, Any]:
    return {
        "brand_name": settings.brand_name,
        "version": __version__,
        "public_url": settings.public_url,
        "allow_signup": settings.allow_signup,
        "live_mode": settings.live_mode,
        "require_verified_email_for_live": settings.require_verified_email_for_live,
        "simulator": settings.simulator,
        "allow_experimental": settings.allow_experimental,
        "egress_ips": settings.egress_ip_list,
        "docs_enabled": settings.docs_enabled,
        "support_email": settings.support_email,
        "attestation": ATTESTATION,
        "is_production": settings.is_production,
    }


def me_payload(db, rt: Runtime, user: User, csrf_token: str) -> dict[str, Any]:
    return {
        "user": user_json(user),
        "csrf_token": csrf_token,
        "workspaces": [workspace_json(ws, role) for ws, role in workspaces.list_for_user(db, user.id)],
    }


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        auth.cookie_name(settings),
        token,
        max_age=settings.session_max_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        auth.cookie_name(settings), path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


# -------------------------------------------------------------------- public
@router.get("/config")
def config(rt: Runtime = Depends(get_rt)):
    return platform_json(rt.settings)


@router.get("/catalog")
def gateway_catalog():
    return catalog.raw_json()


# ---------------------------------------------------------------------- auth
@router.post("/auth/signup")
def signup(body: SignupIn, request: Request, response: Response, rt: Runtime = Depends(get_rt)):
    ip = client_ip(request)
    rate_limit(request, "signup", ip, rt.settings.signup_rate_per_hour, 3600)
    with session_scope(rt.sessions) as db:
        res = auth.signup(
            db,
            rt,
            email=body.email,
            password=body.password,
            name=body.name,
            workspace_name=body.workspace_name,
            invite_token=body.invite_token,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        audit.record(
            db,
            Actor("user", res.user.id, res.user.email, ip),
            "user.signed_up",
            workspace_id=res.workspace.id if res.workspace else None,
        )
        payload = me_payload(db, rt, res.user, res.session.csrf_token)
    _set_cookie(response, rt.settings, res.token)
    return payload


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, rt: Runtime = Depends(get_rt)):
    ip, email = client_ip(request), auth.normalize_email(body.email)
    rate_limit(request, "login_ip", ip, rt.settings.login_rate_per_15min * 3, 900)
    rate_limit(request, "login_email", email, rt.settings.login_rate_per_15min, 900)
    with session_scope(rt.sessions) as db:
        res = auth.login(
            db, rt.settings, email=email, password=body.password, ip=ip, user_agent=request.headers.get("user-agent")
        )
        if res.session is None:
            # Two-factor account: the password was right, but there is no session
            # and no cookie until /auth/login/two-factor gets a code as well.
            return {
                "two_factor_required": True,
                "challenge": res.challenge,
                "expires_in": auth.CHALLENGE_MINUTES * 60,
            }
        payload = me_payload(db, rt, res.user, res.session.csrf_token)
    _set_cookie(response, rt.settings, res.token)
    return payload


@router.post("/auth/login/two-factor")
def login_two_factor(body: TwoFactorLoginIn, request: Request, response: Response, rt: Runtime = Depends(get_rt)):
    ip = client_ip(request)
    rate_limit(request, "login_code_ip", ip, rt.settings.login_rate_per_15min * 3, 900)
    with session_scope(rt.sessions) as db:
        email = auth.challenge_account(db, body.challenge)
        if email:
            # Per account, a code is one more sign-in attempt: it draws on the budget of the password step.
            rate_limit(request, "login_email", email, rt.settings.login_rate_per_15min, 900)
        res = auth.complete_login(
            db,
            rt.settings,
            rt.vault,
            challenge=body.challenge,
            code=body.code,
            recovery_code=body.recovery_code,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        if res.method == "recovery_code":
            audit.record(
                db,
                Actor("user", res.user.id, res.user.email, ip),
                "user.recovery_code_used",
                detail={"remaining": auth.recovery_codes_left(db, res.user.id)},
            )
        payload = me_payload(db, rt, res.user, res.session.csrf_token)
    _set_cookie(response, rt.settings, res.token)
    return payload


@router.post("/auth/logout")
def logout(request: Request, response: Response, rt: Runtime = Depends(get_rt)):
    token = request.cookies.get(auth.cookie_name(rt.settings), "")
    with session_scope(rt.sessions) as db:
        auth.logout(db, token)
    _clear_cookie(response, rt.settings)
    return {"ok": True}


@router.get("/auth/me")
def me(user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return me_payload(db, rt, db.get(User, user.user.id), user.csrf_token)


@router.patch("/auth/profile")
def update_profile(body: ProfileIn, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        row = db.get(User, user.user.id)
        row.name, row.updated_at = " ".join(body.name.split()), utcnow()
        return user_json(row)


@router.post("/auth/verify-email")
def verify_email(body: TokenIn, request: Request, rt: Runtime = Depends(get_rt)):
    rate_limit(request, "token", client_ip(request), 30, 900)
    with session_scope(rt.sessions) as db:
        user = auth.verify_email(db, body.token)
        return {"ok": True, "email": user.email}


@router.post("/auth/resend-verification")
def resend_verification(request: Request, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    rate_limit(request, "resend", user.user.id, 3, 3600)
    with session_scope(rt.sessions) as db:
        auth.send_verification(db, rt.settings, db.get(User, user.user.id))
    return {"ok": True}


@router.post("/auth/forgot-password")
def forgot_password(body: EmailIn, request: Request, rt: Runtime = Depends(get_rt)):
    rate_limit(request, "forgot_ip", client_ip(request), 10, 3600)
    rate_limit(request, "forgot_email", auth.normalize_email(body.email), 3, 3600)
    with session_scope(rt.sessions) as db:
        auth.request_password_reset(db, rt.settings, body.email)
    return {"ok": True}  # the same answer whether or not the account exists


@router.post("/auth/reset-password")
def reset_password(body: ResetIn, request: Request, rt: Runtime = Depends(get_rt)):
    rate_limit(request, "token", client_ip(request), 30, 900)
    with session_scope(rt.sessions) as db:
        user = auth.reset_password(db, body.token, body.password)
        audit.record(db, Actor("user", user.id, user.email, client_ip(request)), "user.password_reset")
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        auth.change_password(
            db, db.get(User, user.user.id), body.current_password, body.new_password, keep_session=user.session_id
        )
        audit.record(db, user.actor, "user.password_changed")
    return {"ok": True}


# ---------------------------------------------------------------- two-factor
def _two_factor_limit(request: Request, user: UserCtx) -> None:
    # These take a password or a code from someone already signed in. The account
    # lock-out is the real limit on guessing; this keeps a script from even getting there fast.
    rate_limit(request, "two_factor", user.user.id, 20, 900)


@router.get("/auth/two-factor")
def two_factor_status(user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return two_factor_json(db, db.get(User, user.user.id))


@router.post("/auth/two-factor/setup")
def two_factor_setup(
    body: TwoFactorSetupIn, request: Request, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)
):
    """Start a set-up. The secret is in this response and in no other."""
    _two_factor_limit(request, user)
    with session_scope(rt.sessions) as db:
        setup = auth.begin_two_factor(db, rt.vault, rt.settings, user.user.id, body.current_password)
    return {
        "secret": setup.secret,
        "otpauth_uri": setup.uri,
        "account": user.user.email,
        "issuer": rt.settings.brand_name,
        "digits": totp.DIGITS,
        "period": totp.PERIOD,
        "expires_in": auth.TOTP_SETUP_MINUTES * 60,
    }


@router.post("/auth/two-factor/confirm")
def two_factor_confirm(
    body: TwoFactorConfirmIn, request: Request, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)
):
    """Switch it on with the first code. The recovery codes are in this response and in no other."""
    _two_factor_limit(request, user)
    with session_scope(rt.sessions) as db:
        codes = auth.confirm_two_factor(db, rt.vault, user.user.id, body.code, keep_session=user.session_id)
        audit.record(db, user.actor, "user.two_factor_enabled")
    return {"enabled": True, "recovery_codes": codes}


@router.post("/auth/two-factor/disable")
def two_factor_disable(
    body: TwoFactorChangeIn, request: Request, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)
):
    _two_factor_limit(request, user)
    with session_scope(rt.sessions) as db:
        auth.disable_two_factor(
            db,
            rt.vault,
            user.user.id,
            password=body.current_password,
            code=body.code,
            recovery_code=body.recovery_code,
            keep_session=user.session_id,
        )
        audit.record(db, user.actor, "user.two_factor_disabled")
    return {"enabled": False}


@router.post("/auth/two-factor/recovery-codes")
def two_factor_recovery_codes(
    body: TwoFactorChangeIn, request: Request, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)
):
    """Replace the recovery codes. The new ones are in this response and in no other."""
    _two_factor_limit(request, user)
    with session_scope(rt.sessions) as db:
        codes = auth.regenerate_recovery_codes(
            db,
            rt.vault,
            user.user.id,
            password=body.current_password,
            code=body.code,
            recovery_code=body.recovery_code,
        )
        audit.record(db, user.actor, "user.recovery_codes_regenerated")
    return {"recovery_codes": codes}


@router.get("/auth/sessions")
def list_sessions(user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        rows = db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user.user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > utcnow()
            )
            .order_by(UserSession.last_seen_at.desc())
            .limit(50)
        )
        return {
            "data": [
                {
                    "current": s.id == user.session_id,
                    "created_at": iso(s.created_at),
                    "last_seen_at": iso(s.last_seen_at),
                    "ip": s.ip,
                    "user_agent": s.user_agent,
                }
                for s in rows
            ]
        }


@router.post("/auth/sessions/revoke-others")
def revoke_other_sessions(user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        auth.revoke_sessions(db, user.user.id, keep=user.session_id)
    return {"ok": True}


@router.get("/auth/invitation")
def invitation_preview(request: Request, token: str = Query(max_length=200), rt: Runtime = Depends(get_rt)):
    rate_limit(request, "token", client_ip(request), 30, 900)
    with session_scope(rt.sessions) as db:
        inv = workspaces.find_invitation(db, token)
        if inv is None:
            raise ApiError(404, "NOT_FOUND", "This invitation is invalid or has expired")
        ws = db.get(Workspace, inv.workspace_id)
        inviter = db.get(User, inv.invited_by) if inv.invited_by else None
        return {
            "workspace": ws.name,
            "email": inv.email,
            "role": inv.role,
            "inviter": inviter.name if inviter else "",
            "has_account": db.scalar(select(func.count()).select_from(User).where(User.email == inv.email)) > 0,
        }


@router.post("/auth/accept-invitation")
def accept_invitation(body: TokenIn, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        inv = workspaces.find_invitation(db, body.token)
        if inv is None:
            raise ApiError(404, "NOT_FOUND", "This invitation is invalid or has expired")
        member = workspaces.accept_invitation(db, rt.settings, inv, db.get(User, user.user.id))
        ws = db.get(Workspace, inv.workspace_id)
        audit.record(
            db,
            user.actor,
            "member.joined",
            workspace_id=ws.id,
            target_type="user",
            target_id=user.user.id,
            detail={"role": member.role},
        )
        return workspace_json(ws, member.role)


# ---------------------------------------------------------------- workspaces
@router.get("/workspaces")
def list_workspaces(user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        return {"data": [workspace_json(ws, role) for ws, role in workspaces.list_for_user(db, user.user.id)]}


@router.post("/workspaces")
def create_workspace(body: WorkspaceIn, user: UserCtx = Depends(current_user), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        ws = workspaces.create_workspace(db, rt.vault, rt.settings, name=body.name, owner=db.get(User, user.user.id))
        audit.record(db, user.actor, "workspace.created", workspace_id=ws.id)
        return workspace_json(ws, "owner")


@router.get("/workspaces/{slug}")
def get_workspace(ctx: WsCtx = Depends(workspace_ctx)):
    return workspace_json(ctx.workspace, ctx.role)


@router.patch("/workspaces/{slug}")
def update_workspace(body: WorkspacePatch, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("admin")
    with session_scope(rt.sessions) as db:
        ws = db.get(Workspace, ctx.workspace.id, with_for_update=True)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("name"):
            ws.name = " ".join(changes.pop("name").split())
        changes.pop("name", None)
        settings = dict(ws.settings or {})
        for key, value in changes.items():
            if value:
                settings[key] = value
            else:
                settings.pop(key, None)
        ws.settings, ws.updated_at = settings, utcnow()
        audit.record(
            db,
            ctx.actor,
            "workspace.updated",
            workspace_id=ws.id,
            detail={"fields": sorted(body.model_dump(exclude_unset=True))},
        )
        return workspace_json(ws, ctx.role)


@router.delete("/workspaces/{slug}")
def archive_workspace(ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    """Archive, not erase: payment records are kept. API keys stop working at once."""
    ctx.need("owner")
    with session_scope(rt.sessions) as db:
        ws = db.get(Workspace, ctx.workspace.id, with_for_update=True)
        ws.archived_at = utcnow()
        audit.record(db, ctx.actor, "workspace.archived", workspace_id=ws.id)
    return {"ok": True}


@router.post("/workspaces/{slug}/go-live")
def go_live(body: GoLiveIn, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    settings = rt.settings
    ctx.need("owner")
    if settings.live_mode == "disabled":
        raise ApiError(403, "FORBIDDEN", "Live mode is switched off on this deployment")
    if not body.accept:
        raise ApiError(400, "VALIDATION", "You need to accept the statement to activate live mode")
    with session_scope(rt.sessions) as db:
        user = db.get(User, ctx.user.user.id)
        if settings.require_verified_email_for_live and user.email_verified_at is None:
            raise ApiError(403, "FORBIDDEN", "Confirm your email address before activating live mode")
        ws = db.get(Workspace, ctx.workspace.id, with_for_update=True)
        if ws.live_status == "active":
            return workspace_json(ws, ctx.role)
        now = utcnow()
        ws.live_attestation = {
            "version": ATTESTATION_VERSION,
            "text": ATTESTATION,
            "legal_name": body.legal_name,
            "accepted_by": user.id,
            "email": user.email,
            "at": iso(now),
            "ip": ctx.user.ip,
        }
        if settings.live_mode == "self_serve":
            ws.live_status, ws.live_activated_at = "active", now
        else:
            ws.live_status, ws.live_requested_at = "pending", now
        ws.updated_at = now
        audit.record(
            db,
            ctx.actor,
            "workspace.live_" + ("activated" if ws.live_status == "active" else "requested"),
            workspace_id=ws.id,
            mode="live",
            detail={"legal_name": body.legal_name},
        )
        return workspace_json(ws, ctx.role)


# ------------------------------------------------------------------- members
@router.get("/workspaces/{slug}/members")
def list_members(ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    with session_scope(rt.sessions) as db:
        people = [
            {
                "user_id": u.id,
                "email": u.email,
                "name": u.name,
                "role": m.role,
                "joined_at": iso(m.created_at),
                "you": u.id == ctx.user.user.id,
            }
            for u, m in workspaces.members(db, ctx.workspace.id)
        ]
        pending = []
        if workspaces.role_at_least(ctx.role, "admin"):
            rows = db.scalars(
                select(Invitation)
                .where(
                    Invitation.workspace_id == ctx.workspace.id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > utcnow(),
                )
                .order_by(Invitation.created_at)
            )
            pending = [
                {
                    "id": i.id,
                    "email": i.email,
                    "role": i.role,
                    "expires_at": iso(i.expires_at),
                    "created_at": iso(i.created_at),
                }
                for i in rows
            ]
        return {"members": people, "invitations": pending}


@router.post("/workspaces/{slug}/invitations")
def invite_member(body: InviteIn, request: Request, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("admin")
    rate_limit(request, "invite", ctx.workspace.id, 30, 3600)
    with session_scope(rt.sessions) as db:
        inv, token = workspaces.invite(db, ctx.workspace.id, ctx.user.user, ctx.role, body.email, body.role)
        mailer.send_invitation(
            db, rt.settings, inv.email, token, ctx.workspace.name, ctx.user.user.name or ctx.user.user.email, inv.role
        )
        audit.record(
            db,
            ctx.actor,
            "member.invited",
            workspace_id=ctx.workspace.id,
            target_type="invitation",
            target_id=inv.id,
            detail={"email": inv.email, "role": inv.role},
        )
        return {"id": inv.id, "email": inv.email, "role": inv.role, "expires_at": iso(inv.expires_at)}


@router.delete("/workspaces/{slug}/invitations/{invitation_id}")
def revoke_invitation(invitation_id: str, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("admin")
    with session_scope(rt.sessions) as db:
        inv = db.scalar(
            select(Invitation).where(Invitation.id == invitation_id, Invitation.workspace_id == ctx.workspace.id)
        )
        if inv is None:
            raise ApiError(404, "NOT_FOUND", "No such invitation")
        inv.revoked_at = inv.revoked_at or utcnow()
        audit.record(
            db,
            ctx.actor,
            "member.invitation_revoked",
            workspace_id=ctx.workspace.id,
            target_type="invitation",
            target_id=inv.id,
        )
    return {"ok": True}


@router.patch("/workspaces/{slug}/members/{user_id}")
def change_member_role(user_id: str, body: RoleIn, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    ctx.need("admin")
    with session_scope(rt.sessions) as db:
        m = workspaces.change_role(db, ctx.workspace.id, ctx.role, user_id, body.role)
        audit.record(
            db,
            ctx.actor,
            "member.role_changed",
            workspace_id=ctx.workspace.id,
            target_type="user",
            target_id=user_id,
            detail={"role": m.role},
        )
        return {"user_id": user_id, "role": m.role}


@router.delete("/workspaces/{slug}/members/{user_id}")
def remove_member(user_id: str, ctx: WsCtx = Depends(workspace_ctx), rt: Runtime = Depends(get_rt)):
    leaving = user_id == ctx.user.user.id
    if not leaving:
        ctx.need("admin")
    with session_scope(rt.sessions) as db:
        workspaces.remove_member(
            db, ctx.workspace.id, "owner" if leaving and ctx.role == "owner" else ctx.role, user_id
        )
        audit.record(
            db,
            ctx.actor,
            "member.left" if leaving else "member.removed",
            workspace_id=ctx.workspace.id,
            target_type="user",
            target_id=user_id,
        )
    return {"ok": True}


@router.get("/workspaces/{slug}/audit-log")
def audit_log(
    ctx: WsCtx = Depends(workspace_ctx),
    rt: Runtime = Depends(get_rt),
    limit: int = Query(50, ge=1, le=200),
    before: int | None = None,
):
    ctx.need("admin")
    with session_scope(rt.sessions) as db:
        return audit.listing(db, ctx.workspace.id, limit=limit, before=before)


# --------------------------------------------------------------------- admin
def _admin(user: UserCtx = Depends(current_user)) -> UserCtx:
    if not user.user.is_platform_admin:
        raise ApiError(404, "NOT_FOUND", "Not found")
    return user


@router.get("/admin/workspaces")
def admin_workspaces(
    admin: UserCtx = Depends(_admin),
    rt: Runtime = Depends(get_rt),
    q: str = Query("", max_length=80),
    live_status: str = Query("", max_length=20),
):
    with session_scope(rt.sessions) as db:
        stmt = select(Workspace)
        if q:
            stmt = stmt.where(Workspace.name.ilike(f"%{q}%") | Workspace.slug.ilike(f"%{q}%"))
        if live_status:
            stmt = stmt.where(Workspace.live_status == live_status)
        rows = list(db.scalars(stmt.order_by(Workspace.created_at.desc()).limit(200)))
        counts = dict(
            db.execute(
                select(Membership.workspace_id, func.count())
                .group_by(Membership.workspace_id)
                .where(Membership.workspace_id.in_([w.id for w in rows] or [""]))
            ).all()
        )
        return {
            "data": [
                {
                    **workspace_json(w),
                    "members": counts.get(w.id, 0),
                    "attestation": w.live_attestation,
                    "archived": w.archived_at is not None,
                }
                for w in rows
            ]
        }


@router.post("/admin/workspaces/{workspace_id}/{action}")
def admin_action(
    workspace_id: str, action: str, request: Request, admin: UserCtx = Depends(_admin), rt: Runtime = Depends(get_rt)
):
    if action not in ("suspend", "unsuspend", "approve-live", "revoke-live"):
        raise ApiError(404, "NOT_FOUND", "Unknown action")
    with session_scope(rt.sessions) as db:
        ws = db.get(Workspace, workspace_id, with_for_update=True)
        if ws is None:
            raise ApiError(404, "NOT_FOUND", "No such workspace")
        now = utcnow()
        if action == "suspend":
            ws.suspended_at, ws.suspended_reason = now, (request.query_params.get("reason") or "")[:300] or None
        elif action == "unsuspend":
            ws.suspended_at = ws.suspended_reason = None
        elif action == "approve-live":
            if ws.live_attestation is None:
                raise ApiError(409, "DUPLICATE", "The owner has not accepted the attestation yet")
            ws.live_status, ws.live_activated_at = "active", now
        else:
            ws.live_status = "inactive"
        ws.updated_at = now
        audit.record(db, admin.actor, f"platform.{action}", workspace_id=ws.id)
        return workspace_json(ws)
