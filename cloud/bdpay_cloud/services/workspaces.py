"""Workspaces, their two environments, members and invitations."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crypto import Vault, aad_callback_secret
from ..db import utcnow
from ..errors import ServiceError
from ..ids import new_id, new_public_token, new_secret, rand_b32, sha256_hex
from ..models import MODES, ROLES, GatewayConfig, Invitation, Membership, User, Workspace, WorkspaceEnv
from ..settings import Settings

ROLE_RANK = {role: len(ROLES) - i for i, role in enumerate(ROLES)}  # owner 4 ... analyst 1
INVITE_DAYS = 7


class WorkspaceError(ServiceError):
    def __init__(self, message: str, status: int = 400, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def role_at_least(role: str | None, minimum: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK[minimum]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    return s if len(s) >= 3 else f"shop-{rand_b32(6)}"


def _unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug = base
    while db.scalar(select(func.count()).select_from(Workspace).where(Workspace.slug == slug)):
        slug = f"{base}-{rand_b32(4)}"
    return slug


def create_workspace(db: Session, vault: Vault, settings: Settings, *, name: str, owner: User | None) -> Workspace:
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 80:
        raise WorkspaceError("Workspace name must be 2 to 80 characters")
    if owner is not None:
        owned = db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == owner.id))
        if owned >= settings.max_workspaces_per_user:
            raise WorkspaceError(f"You can belong to at most {settings.max_workspaces_per_user} workspaces")
    ws = Workspace(
        id=new_id("ws"), slug=_unique_slug(db, name), name=name, settings={}, created_by=owner.id if owner else None
    )
    db.add(ws)
    db.flush()
    for mode in MODES:
        db.add(
            WorkspaceEnv(
                workspace_id=ws.id,
                mode=mode,
                public_key=new_public_token("es" if mode == "sandbox" else "el"),
                # Signs callback links (HMAC-SHA256). 64 hex characters, never shared between tenants.
                callback_secret_enc=vault.encrypt(new_secret(32), aad_callback_secret(ws.id, mode)),
                settings={},
            )
        )
    db.flush()
    if settings.simulator:
        # A new workspace can take a (fake) payment straight away.
        db.add(
            GatewayConfig(
                workspace_id=ws.id,
                mode="sandbox",
                gateway="simulator",
                enabled=True,
                config={},
                secret_hints={},
                status="verified",
            )
        )
    if owner is not None:
        db.add(Membership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    db.flush()
    return ws


def membership(db: Session, workspace_id: str, user_id: str) -> Membership | None:
    return db.get(Membership, (workspace_id, user_id))


def list_for_user(db: Session, user_id: str) -> list[tuple[Workspace, str]]:
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id, Workspace.archived_at.is_(None))
        .order_by(Workspace.created_at)
    ).all()
    return [(r[0], r[1]) for r in rows]


def members(db: Session, workspace_id: str) -> list[tuple[User, Membership]]:
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _owner_count(db: Session, workspace_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id, Membership.role == "owner")
        )
        or 0
    )


def change_role(db: Session, workspace_id: str, actor_role: str, target_user_id: str, new_role: str) -> Membership:
    if new_role not in ROLES:
        raise WorkspaceError("Unknown role")
    m = membership(db, workspace_id, target_user_id)
    if m is None:
        raise WorkspaceError("That person is not a member", 404, "NOT_FOUND")
    # Only owners hand out or take away ownership.
    if (new_role == "owner" or m.role == "owner") and actor_role != "owner":
        raise WorkspaceError("Only an owner can change an owner's role", 403, "FORBIDDEN")
    if m.role == "owner" and new_role != "owner" and _owner_count(db, workspace_id) <= 1:
        raise WorkspaceError("A workspace needs at least one owner")
    m.role = new_role
    db.flush()
    return m


def remove_member(db: Session, workspace_id: str, actor_role: str, target_user_id: str) -> None:
    m = membership(db, workspace_id, target_user_id)
    if m is None:
        raise WorkspaceError("That person is not a member", 404, "NOT_FOUND")
    if m.role == "owner":
        if actor_role != "owner":
            raise WorkspaceError("Only an owner can remove an owner", 403, "FORBIDDEN")
        if _owner_count(db, workspace_id) <= 1:
            raise WorkspaceError("A workspace needs at least one owner")
    db.delete(m)
    db.flush()


def invite(
    db: Session, workspace_id: str, actor: User, actor_role: str, email: str, role: str
) -> tuple[Invitation, str]:
    """Returns the invitation and the one-time token for the emailed link."""
    email = email.strip().lower()
    if role not in ROLES:
        raise WorkspaceError("Unknown role")
    if role == "owner" and actor_role != "owner":
        raise WorkspaceError("Only an owner can invite another owner", 403, "FORBIDDEN")
    existing = db.scalar(select(User).where(User.email == email))
    if existing and membership(db, workspace_id, existing.id):
        raise WorkspaceError("That person is already a member", 409, "DUPLICATE")
    pending = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.workspace_id == workspace_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
    )
    if pending >= 50:
        raise WorkspaceError("Too many pending invitations")
    # A fresh invitation replaces an older one to the same address.
    for old in db.scalars(
        select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ):
        old.revoked_at = utcnow()
    token = new_secret(24)
    inv = Invitation(
        id=new_id("inv"),
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=sha256_hex(token),
        invited_by=actor.id,
        expires_at=utcnow() + timedelta(days=INVITE_DAYS),
    )
    db.add(inv)
    db.flush()
    return inv, token


def find_invitation(db: Session, token: str) -> Invitation | None:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == sha256_hex(token or "")))
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at <= utcnow():
        return None
    return inv


def accept_invitation(db: Session, settings: Settings, inv: Invitation, user: User) -> Membership:
    if user.email != inv.email:
        raise WorkspaceError("This invitation was sent to a different email address", 403, "FORBIDDEN")
    m = membership(db, inv.workspace_id, user.id)
    if m is None:
        count = db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == user.id)) or 0
        if count >= settings.max_workspaces_per_user:
            raise WorkspaceError(f"You can belong to at most {settings.max_workspaces_per_user} workspaces")
        m = Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role)
        db.add(m)
    inv.accepted_at = utcnow()
    # Receiving the invitation proves the address.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    db.flush()
    return m
