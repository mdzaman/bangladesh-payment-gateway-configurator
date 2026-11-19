"""Accounts, dashboard sessions, email verification and password reset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import ServiceError
from ..ids import new_id, new_secret, sha256_hex
from ..models import EmailToken, User, UserSession, Workspace
from ..security import passwords
from ..settings import Settings
from . import mailer, workspaces

VERIFY_HOURS = 48
RESET_MINUTES = 60
MAX_FAILED_LOGINS = 8
LOCK_MINUTES = 15


class AuthError(ServiceError):
    def __init__(self, message: str, status: int = 400, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


BAD_LOGIN = AuthError("Invalid email or password", 401, "UNAUTHENTICATED")


def cookie_name(settings: Settings) -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure,
    # host-only and Path=/, which blocks a sibling subdomain from planting one.
    return "__Host-bdpay_session" if settings.cookie_secure else "bdpay_session"


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


@dataclass
class SignupResult:
    user: User
    workspace: Workspace | None
    session: UserSession
    token: str


# ------------------------------------------------------------------- sessions
def create_session(db: Session, settings: Settings, user: User, ip: str | None, user_agent: str | None):
    token = new_secret(32)
    session = UserSession(
        id=sha256_hex(token),
        user_id=user.id,
        csrf_token=new_secret(16),
        expires_at=utcnow() + timedelta(days=settings.session_max_days),
        ip=ip,
        user_agent=(user_agent or "")[:300],
    )
    db.add(session)
    db.flush()
    return session, token


def resolve_session(db: Session, settings: Settings, token: str) -> tuple[UserSession, User] | None:
    if not token or len(token) > 200:
        return None
    session = db.get(UserSession, sha256_hex(token))
    now = utcnow()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return None
    if now - session.last_seen_at > timedelta(hours=settings.session_idle_hours):
        return None
    user = db.get(User, session.user_id)
    if user is None or user.disabled_at is not None:
        return None
    if now - session.last_seen_at > timedelta(seconds=60):
        session.last_seen_at = now
    return session, user


def logout(db: Session, token: str) -> None:
    session = db.get(UserSession, sha256_hex(token or ""))
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()


def revoke_sessions(db: Session, user_id: str, keep: str | None = None) -> None:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep:
        q = q.where(UserSession.id != keep)
    db.execute(q.values(revoked_at=utcnow()))


# -------------------------------------------------------------- email tokens
def _issue(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = new_secret(24)
    db.add(EmailToken(id=sha256_hex(token), user_id=user.id, purpose=purpose, expires_at=utcnow() + ttl))
    return token


def _consume(db: Session, token: str, purpose: str) -> User | None:
    row = db.get(EmailToken, sha256_hex(token or ""))
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= utcnow():
        return None
    row.used_at = utcnow()
    return db.get(User, row.user_id)


def send_verification(db: Session, settings: Settings, user: User) -> None:
    if user.email_verified_at is None:
        mailer.send_verification(
            db, settings, user.email, _issue(db, user, "verify_email", timedelta(hours=VERIFY_HOURS))
        )


def verify_email(db: Session, token: str) -> User:
    user = _consume(db, token, "verify_email")
    if user is None:
        raise AuthError("This confirmation link is invalid or has expired", 400)
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    return user


def request_password_reset(db: Session, settings: Settings, email: str) -> None:
    """Always succeeds from the caller's point of view (no account enumeration)."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is not None and user.disabled_at is None:
        mailer.send_password_reset(
            db, settings, user.email, _issue(db, user, "reset_password", timedelta(minutes=RESET_MINUTES))
        )


def reset_password(db: Session, token: str, new_password: str) -> User:
    user = _consume(db, token, "reset_password")
    if user is None:
        raise AuthError("This reset link is invalid or has expired", 400)
    problem = passwords.password_problem(new_password, email=user.email, name=user.name)
    if problem:
        raise AuthError(problem)
    user.password_hash = passwords.hash_password(new_password)
    user.failed_logins, user.locked_until, user.updated_at = 0, None, utcnow()
    # Using the emailed link also proves the address.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    revoke_sessions(db, user.id)
    return user


def change_password(db: Session, user: User, current: str, new: str, keep_session: str | None) -> None:
    if not passwords.verify_password(user.password_hash, current):
        raise AuthError("Your current password is not correct", 400)
    problem = passwords.password_problem(new, email=user.email, name=user.name)
    if problem:
        raise AuthError(problem)
    user.password_hash, user.updated_at = passwords.hash_password(new), utcnow()
    revoke_sessions(db, user.id, keep=keep_session)


# ------------------------------------------------------------ sign-up, login
def signup(
    db: Session,
    rt,
    *,
    email: str,
    password: str,
    name: str,
    workspace_name: str | None = None,
    invite_token: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SignupResult:
    settings: Settings = rt.settings
    email, name = normalize_email(email), " ".join((name or "").split())
    invitation = workspaces.find_invitation(db, invite_token) if invite_token else None
    if invite_token and invitation is None:
        raise AuthError("This invitation is invalid or has expired", 400)
    if invitation is not None and invitation.email != email:
        raise AuthError("This invitation was sent to a different email address", 403, "FORBIDDEN")
    if invitation is None and not settings.allow_signup:
        raise AuthError(
            "Sign-up is closed on this deployment. Ask a workspace owner for an invitation.", 403, "FORBIDDEN"
        )
    if not 1 <= len(name) <= 80:
        raise AuthError("Enter your name (up to 80 characters)")
    problem = passwords.password_problem(password, email=email, name=name)
    if problem:
        raise AuthError(problem)
    if db.scalar(select(User.id).where(User.email == email)):
        raise AuthError("An account with this email already exists", 409, "DUPLICATE")
    user = User(id=new_id("usr"), email=email, name=name, password_hash=passwords.hash_password(password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AuthError("An account with this email already exists", 409, "DUPLICATE") from None
    workspace: Workspace | None = None
    if invitation is not None:
        workspaces.accept_invitation(db, settings, invitation, user)
        workspace = db.get(Workspace, invitation.workspace_id)
    else:
        try:
            workspace = workspaces.create_workspace(
                db, rt.vault, settings, name=workspace_name or f"{name}'s shop", owner=user
            )
        except workspaces.WorkspaceError as e:
            raise AuthError(e.message, e.status, e.code) from None
    send_verification(db, settings, user)
    session, token = create_session(db, settings, user, ip, user_agent)
    user.last_login_at = utcnow()
    return SignupResult(user, workspace, session, token)


def login(
    db: Session, settings: Settings, *, email: str, password: str, ip: str | None = None, user_agent: str | None = None
) -> tuple[User, UserSession, str]:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        passwords.burn(password)
        raise BAD_LOGIN
    now = utcnow()
    if user.locked_until is not None and user.locked_until > now:
        raise AuthError("Too many failed attempts. Try again in a few minutes.", 429, "RATE_LIMITED")
    if not passwords.verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
        db.commit()  # keep the counter even though the request fails
        raise BAD_LOGIN
    if user.disabled_at is not None:
        raise AuthError("This account is disabled", 403, "FORBIDDEN")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    session, token = create_session(db, settings, user, ip, user_agent)
    return user, session, token
