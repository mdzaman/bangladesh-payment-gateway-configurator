"""Transactional email through an outbox table.

Requests only insert a row (in their own transaction); the worker sends. A
slow or broken mail server therefore never slows a sign-up and a message is
retried instead of lost. With ``MAIL_BACKEND=console`` messages are written to
the log, which is how a development setup shows verification links.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import OutboundEmail
from ..settings import Settings

log = logging.getLogger("bdpay_cloud.mail")
MAX_ATTEMPTS = 6


def enqueue(db: Session, to: str, subject: str, body_text: str, body_html: str | None = None) -> None:
    db.add(OutboundEmail(to_email=to, subject=subject[:300], body_text=body_text, body_html=body_html))


def _layout(settings: Settings, heading: str, paragraphs: list[str], button: tuple[str, str] | None) -> str:
    parts = [f'<p style="margin:0 0 14px">{html.escape(p)}</p>' for p in paragraphs]
    if button:
        label, url = button
        parts.append(
            f'<p style="margin:22px 0"><a href="{html.escape(url, quote=True)}" style="background:#006a4e;'
            f"color:#fff;text-decoration:none;padding:12px 20px;border-radius:10px;font-weight:600;"
            f'display:inline-block">{html.escape(label)}</a></p>'
            f'<p style="margin:0 0 14px;color:#55665f;font-size:13px">Or paste this link into your browser:'
            f"<br>{html.escape(url)}</p>"
        )
    return (
        f'<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#14211c;max-width:520px;'
        f'margin:0 auto;padding:24px"><h1 style="font-size:22px;margin:0 0 16px">{html.escape(heading)}</h1>'
        f'{"".join(parts)}<p style="color:#55665f;font-size:13px;margin-top:28px">'
        f"{html.escape(settings.brand_name)}</p></div>"
    )


def compose(
    settings: Settings, heading: str, paragraphs: list[str], button: tuple[str, str] | None = None
) -> tuple[str, str]:
    """(plain text, html) for a simple message with one optional action link."""
    lines = [heading, "", *paragraphs]
    if button:
        lines += ["", f"{button[0]}: {button[1]}"]
    lines += ["", f"-- {settings.brand_name}"]
    return "\n".join(lines), _layout(settings, heading, paragraphs, button)


def send_verification(db: Session, settings: Settings, to: str, token: str) -> None:
    url = f"{settings.public_url}/verify-email?token={token}"
    t, h = compose(
        settings,
        "Confirm your email address",
        [
            "Confirm this address to finish setting up your account. The link works for 48 hours.",
            "If you did not create an account, ignore this message.",
        ],
        ("Confirm email", url),
    )
    enqueue(db, to, f"Confirm your email for {settings.brand_name}", t, h)


def send_password_reset(db: Session, settings: Settings, to: str, token: str) -> None:
    url = f"{settings.public_url}/reset-password?token={token}"
    t, h = compose(
        settings,
        "Reset your password",
        [
            "Someone asked to reset the password for this account. The link works for 1 hour.",
            "If that was not you, ignore this message: your password stays the same.",
        ],
        ("Choose a new password", url),
    )
    enqueue(db, to, f"Reset your {settings.brand_name} password", t, h)


def send_invitation(
    db: Session, settings: Settings, to: str, token: str, workspace: str, inviter: str, role: str
) -> None:
    url = f"{settings.public_url}/invite?token={token}"
    t, h = compose(
        settings,
        f"Join {workspace}",
        [f'{inviter} invited you to the workspace "{workspace}" as {role}. The invitation works for 7 days.'],
        ("Accept invitation", url),
    )
    enqueue(db, to, f"{inviter} invited you to {workspace} on {settings.brand_name}", t, h)


def deliver(settings: Settings, to: str, subject: str, body_text: str, body_html: str | None) -> None:
    if settings.mail_backend == "console":
        log.info("EMAIL to=%s subject=%s\n%s", to, subject, body_text)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, to, subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    if settings.smtp_tls == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()
        )
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    with client:
        if settings.smtp_tls == "starttls":
            client.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(msg)


def send_pending(rt, limit: int = 20) -> int:
    """Send due messages. Safe to run on several workers at once (SKIP LOCKED)."""
    with rt.engine.begin() as conn:
        rows = conn.execute(
            text("""
            UPDATE outbound_emails SET attempts = attempts + 1, next_attempt_at = now() + interval '2 minutes'
             WHERE id IN (SELECT id FROM outbound_emails WHERE status = 'pending' AND next_attempt_at <= now()
                           ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED)
            RETURNING id, to_email, subject, body_text, body_html, attempts"""),
            {"n": limit},
        ).all()
    for r in rows:
        try:
            deliver(rt.settings, r.to_email, r.subject, r.body_text, r.body_html)
        except Exception as e:
            log.warning("email %s failed (attempt %s): %s", r.id, r.attempts, e.__class__.__name__)
            with rt.engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE outbound_emails
                       SET last_error = :err,
                           status = CASE WHEN attempts >= :max THEN 'failed' ELSE 'pending' END,
                           next_attempt_at = CASE WHEN attempts >= :max THEN NULL
                                                  ELSE now() + make_interval(mins => power(2, attempts)::int) END
                     WHERE id = :id"""),
                    {"id": r.id, "err": f"{e.__class__.__name__}: {e}"[:500], "max": MAX_ATTEMPTS},
                )
        else:
            with rt.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE outbound_emails SET status = 'sent', sent_at = now(), "
                        "next_attempt_at = NULL, last_error = NULL WHERE id = :id"
                    ),
                    {"id": r.id},
                )
    return len(rows)
