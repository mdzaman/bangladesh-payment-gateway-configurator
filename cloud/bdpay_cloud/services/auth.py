"""Accounts, dashboard sessions, email verification, password reset and two-factor sign-in."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..crypto import Vault, VaultError, aad_totp
from ..db import utcnow
from ..errors import ServiceError
from ..ids import new_id, new_secret, sha256_hex
from ..models import EmailToken, LoginChallenge, RecoveryCode, User, UserSession, Workspace
from ..security import passwords, totp
from ..settings import Settings
from . import mailer, workspaces

log = logging.getLogger("bdpay_cloud.auth")

VERIFY_HOURS = 48
RESET_MINUTES = 60
MAX_FAILED_LOGINS = 8
LOCK_MINUTES = 15
# How long a correct password stays good while the second factor is typed.
CHALLENGE_MINUTES = 10
# How long a started two-factor set-up waits for its first code.
SETUP_MINUTES = 15


class AuthError(ServiceError):
    def __init__(self, message: str, status: int = 400, code: str = "VALIDATION"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


BAD_LOGIN = AuthError("Invalid email or password", 401, "UNAUTHENTICATED")
BAD_CODE = "That code is not correct. Use the newest code from your app, or a recovery code you have not used."


def _refuse_if_locked(user: User, now: datetime) -> None:
    """Locked after too many wrong passwords or codes: nothing that takes either
    is even looked at, at sign-in or on a signed-in page, until the lock has passed."""
    if user.locked_until is not None and user.locked_until > now:
        raise AuthError("Too many failed attempts. Try again in a few minutes.", 429, "RATE_LIMITED")


def _challenge_expired() -> AuthError:
    return AuthError("This sign-in has expired. Enter your password again.", 401, "CHALLENGE_EXPIRED")


def _setup_expired(message: str) -> AuthError:
    return AuthError(message, 400, "SETUP_EXPIRED")


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


# How an account row is locked (FOR NO KEY UPDATE): against every other change to
# the same account, but not against rows that merely refer to it. A new session
# or a new emailed link only needs the account to go on existing. The stronger
# FOR UPDATE would make those wait as well, and a password reset would then
# deadlock with a second "forgot password" for the same account.
ACCOUNT_LOCK = {"key_share": True}


def lock_account(db: Session, user_id: str) -> User | None:
    """The account row, locked for this transaction and freshly read. Everything
    that changes how an account signs in takes this first, then the account's
    sessions, then its sign-in challenges: one order, so such requests wait for
    each other instead of deadlocking."""
    return db.get(User, user_id, with_for_update=ACCOUNT_LOCK, populate_existing=True)


def revoke_sessions(db: Session, user_id: str, keep: str | None = None) -> None:
    # The account row first (see lock_account), whether or not the caller holds it already.
    db.execute(select(User.id).where(User.id == user_id).with_for_update(**ACCOUNT_LOCK))
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep:
        q = q.where(UserSession.id != keep)
    db.execute(q.values(revoked_at=utcnow()))
    # A sign-in that has passed the password but not the second factor ends too.
    db.execute(delete(LoginChallenge).where(LoginChallenge.user_id == user_id))


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
    user.password_hash, user.updated_at = passwords.hash_password(new_password), utcnow()
    # The failure count also covers wrong two-factor codes, so for an account
    # with the factor on it stays: holding the mailbox must not buy more guesses.
    if user.totp_enabled_at is None:
        user.failed_logins, user.locked_until = 0, None
    # Using the emailed link also proves the address.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    revoke_sessions(db, user.id)
    return user


def change_password(db: Session, user: User, current: str, new: str, keep_session: str | None) -> None:
    """``user`` is the caller's own row, locked by the caller."""
    now = utcnow()
    _refuse_if_locked(user, now)
    _need_password(db, user, current, now)
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


@dataclass
class LoginResult:
    user: User
    session: UserSession | None = None
    token: str = ""
    # Set instead of a session when the account has two-factor sign-in.
    challenge: str = ""


def _count_failure(db: Session, user: User, now: datetime) -> None:
    """A wrong password or a wrong code, at sign-in or on a signed-in page. The
    database adds it up in one statement, so guesses sent at the same moment are
    each counted, and the account locks at the limit. Committed here, so the
    count survives the error the caller raises next."""
    reached = User.failed_logins + 1 >= MAX_FAILED_LOGINS
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            failed_logins=case((reached, 0), else_=User.failed_logins + 1),
            locked_until=case((reached, now + timedelta(minutes=LOCK_MINUTES)), else_=User.locked_until),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _need_password(db: Session, user: User, password: str, now: datetime) -> None:
    """The password, asked for again on a signed-in page. A wrong one counts
    towards the lock-out as it does at sign-in: an open session must not be a
    place to guess it without limit."""
    if not passwords.verify_password(user.password_hash, password):
        _count_failure(db, user, now)
        raise AuthError("Your current password is not correct")


def login(
    db: Session, settings: Settings, *, email: str, password: str, ip: str | None = None, user_agent: str | None = None
) -> LoginResult:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        passwords.burn(password)
        raise BAD_LOGIN
    _refuse_if_locked(user, utcnow())
    checked = user.password_hash
    if not passwords.verify_password(checked, password):
        _count_failure(db, user, utcnow())
        raise BAD_LOGIN
    # Checking a password is slow on purpose, and the row was not locked for it.
    # Take the row now and look again: guesses sent at the same moment may have
    # locked the account, or the password may have been changed, in the meantime.
    user = lock_account(db, user.id)
    now = utcnow()
    if user is None:
        raise BAD_LOGIN
    # A different hash is a new password, or the same password re-hashed by a
    # sign-in that finished a moment earlier. Only the first is a reason to refuse.
    if user.password_hash != checked and not passwords.verify_password(user.password_hash, password):
        raise BAD_LOGIN
    _refuse_if_locked(user, now)
    if user.disabled_at is not None:
        raise AuthError("This account is disabled", 403, "FORBIDDEN")
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    if user.totp_enabled_at is not None:
        # The password alone opens nothing. The failure count is left as it is:
        # it also counts wrong codes, and a correct password must not wipe those.
        token = new_secret(32)
        db.add(
            LoginChallenge(id=sha256_hex(token), user_id=user.id, expires_at=now + timedelta(minutes=CHALLENGE_MINUTES))
        )
        return LoginResult(user, challenge=token)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    session, token = create_session(db, settings, user, ip, user_agent)
    return LoginResult(user, session, token)


def complete_login(
    db: Session,
    settings: Settings,
    vault: Vault,
    *,
    challenge: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[LoginResult, str]:
    """The second step of sign-in: the session, and which factor opened it
    (``"totp"`` or ``"recovery_code"``)."""
    # The account row is locked before anything is checked, and the challenge
    # after it, so two requests carrying the same code cannot both get a session.
    challenge_id = sha256_hex(challenge or "")
    user_id = db.scalar(select(LoginChallenge.user_id).where(LoginChallenge.id == challenge_id))
    user = lock_account(db, user_id) if user_id else None
    row = db.get(LoginChallenge, challenge_id, with_for_update=True, populate_existing=True) if user else None
    now = utcnow()
    if user is None or row is None or row.used_at is not None or row.expires_at <= now:
        raise _challenge_expired()
    if user.disabled_at is not None or user.totp_enabled_at is None:
        raise _challenge_expired()
    _refuse_if_locked(user, now)
    factor = _check_second_factor(db, vault, user, code)
    if factor is None:
        _count_failure(db, user, now)
        raise AuthError(BAD_CODE, 401, "UNAUTHENTICATED")
    row.used_at = now
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    session, token = create_session(db, settings, user, ip, user_agent)
    return LoginResult(user, session, token), factor


# ------------------------------------------------------------------ two-factor
def _totp_secret(vault: Vault, user: User) -> str:
    try:
        return vault.decrypt_text(user.totp_secret_enc or "", aad_totp(user.id))
    except VaultError:
        # A master key that is gone, or a secret that was not written for this account.
        log.error("the two-factor secret of %s cannot be decrypted", user.id)
        raise AuthError(
            "Your authenticator code cannot be checked right now. Use a recovery code, or contact support.",
            500,
            "INTERNAL",
        ) from None


def _check_second_factor(db: Session, vault: Vault, user: User, code: str) -> str | None:
    """``"totp"`` or ``"recovery_code"`` when ``code`` is good, otherwise None. A
    good code is used up here; the caller's transaction decides whether that stays."""
    if totp.looks_like_totp(code):
        step = totp.verify(_totp_secret(vault, user), code, last_step=user.totp_last_step)
        if step is None:
            return None
        user.totp_last_step = step
        return "totp"
    used = db.execute(
        update(RecoveryCode)
        .where(
            RecoveryCode.id == totp.recovery_hash(user.id, code),
            RecoveryCode.user_id == user.id,
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=utcnow())
    ).rowcount
    return "recovery_code" if used else None


def _require_password_and_factor(db: Session, vault: Vault, user: User, password: str, code: str) -> None:
    """Switching the factor off, or issuing new recovery codes, takes both: a
    session and a password that fell into the wrong hands are not enough. A wrong
    password or code here counts towards the lock-out, as it does at sign-in."""
    now = utcnow()
    _refuse_if_locked(user, now)
    if user.totp_enabled_at is None:
        raise AuthError("Two-factor sign-in is not on for this account")
    _need_password(db, user, password, now)
    if _check_second_factor(db, vault, user, code) is None:
        _count_failure(db, user, now)
        raise AuthError(BAD_CODE)


def _new_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    db.add_all(RecoveryCode(id=totp.recovery_hash(user.id, code), user_id=user.id) for code in codes)
    return codes


def recovery_codes_left(db: Session, user_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(RecoveryCode)
            .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        )
        or 0
    )


def begin_two_factor(db: Session, vault: Vault, settings: Settings, user: User, password: str) -> tuple[str, str]:
    """Start set-up. The new secret is stored, but sign-in asks for nothing until
    a code made from it has been confirmed, which has to happen within
    ``SETUP_MINUTES``. Starting again replaces the secret of an unfinished
    set-up. ``user`` is the caller's own row, locked by the caller (the same
    goes for the functions below). Returns (secret, ``otpauth://`` link)."""
    now = utcnow()
    _refuse_if_locked(user, now)
    if user.totp_enabled_at is not None:
        raise AuthError("Two-factor sign-in is already on for this account", 409, "DUPLICATE")
    _need_password(db, user, password, now)
    secret = totp.new_secret()
    user.totp_secret_enc, user.totp_last_step = vault.encrypt(secret, aad_totp(user.id)), None
    user.totp_started_at = user.updated_at = now
    return secret, totp.provisioning_uri(secret, user.email, settings.brand_name)


def confirm_two_factor(db: Session, vault: Vault, user: User, code: str, keep_session: str | None) -> list[str]:
    """Finish set-up with a code from the app. Returns the recovery codes, which
    exist in readable form only in this answer."""
    now = utcnow()
    _refuse_if_locked(user, now)
    if user.totp_enabled_at is not None:
        raise AuthError("Two-factor sign-in is already on for this account", 409, "DUPLICATE")
    if not user.totp_secret_enc or user.totp_started_at is None:
        raise _setup_expired("There is no set-up to finish. Start it again.")
    # A set-up left unfinished must not stay open: this step takes a code and no
    # password, so whoever holds a session later could keep guessing at it.
    if now - user.totp_started_at > timedelta(minutes=SETUP_MINUTES):
        raise _setup_expired("This set-up took too long and has expired. Start it again.")
    step = totp.verify(_totp_secret(vault, user), code)
    if step is None:
        _count_failure(db, user, now)
        raise AuthError(
            "That code is not correct. Check that your phone sets its clock automatically, then use the newest code."
        )
    user.totp_enabled_at, user.totp_last_step, user.updated_at = now, step, now
    user.totp_started_at = None
    codes = _new_recovery_codes(db, user)
    revoke_sessions(db, user.id, keep=keep_session)
    return codes


def clear_two_factor(db: Session, user: User) -> None:
    """Remove the factor, or an unfinished set-up, and everything that belongs to it."""
    user.totp_secret_enc = user.totp_enabled_at = user.totp_last_step = user.totp_started_at = None
    user.updated_at = utcnow()
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))


def disable_two_factor(
    db: Session, vault: Vault, user: User, password: str, code: str, keep_session: str | None
) -> None:
    _require_password_and_factor(db, vault, user, password, code)
    clear_two_factor(db, user)
    revoke_sessions(db, user.id, keep=keep_session)


def regenerate_recovery_codes(db: Session, vault: Vault, user: User, password: str, code: str) -> list[str]:
    _require_password_and_factor(db, vault, user, password, code)
    return _new_recovery_codes(db, user)
