"""Periodic clean-up and gauges. Everything here is safe to run on several
workers at once and safe to skip for a while."""

from __future__ import annotations

import json
import os
import socket

from sqlalchemy import text

from .. import metrics
from ..engine.runtime import Runtime
from ..services import checkout


def heartbeat(rt: Runtime, name: str = "worker") -> None:
    with rt.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO worker_heartbeats (name, last_beat, detail) VALUES (:n, now(), CAST(:d AS jsonb))
            ON CONFLICT (name) DO UPDATE SET last_beat = now(), detail = EXCLUDED.detail"""),
            {"n": name, "d": json.dumps({"host": socket.gethostname(), "pid": os.getpid()})},
        )


def run(rt: Runtime) -> dict[str, int]:
    done = {"sessions_expired": checkout.expire_due(rt)}
    with rt.engine.begin() as conn:
        # A two-factor set-up that was started and never confirmed stopped being
        # usable after a few minutes; its secret is not kept around. This runs
        # first because it locks account rows, and every other path takes an
        # account before that account's sessions and sign-in challenges.
        done["two_factor_setups"] = conn.execute(
            text(
                "UPDATE users SET totp_secret_enc = NULL, totp_started_at = NULL "
                "WHERE totp_enabled_at IS NULL AND totp_secret_enc IS NOT NULL "
                "AND (totp_started_at IS NULL OR totp_started_at < now() - interval '1 day')"
            )
        ).rowcount
        done["tokens"] = conn.execute(text("DELETE FROM provider_tokens WHERE expires_at < now()")).rowcount
        done["idempotency"] = conn.execute(
            text("DELETE FROM idempotency_keys WHERE created_at < now() - interval '48 hours'")
        ).rowcount
        done["sessions"] = conn.execute(
            text(
                "DELETE FROM sessions WHERE expires_at < now() - interval '7 days' "
                "OR revoked_at < now() - interval '7 days'"
            )
        ).rowcount
        done["email_tokens"] = conn.execute(
            text("DELETE FROM email_tokens WHERE expires_at < now() - interval '7 days'")
        ).rowcount
        done["login_challenges"] = conn.execute(
            text("DELETE FROM login_challenges WHERE expires_at < now() - interval '1 day'")
        ).rowcount
        done["emails"] = conn.execute(
            text("DELETE FROM outbound_emails WHERE status <> 'pending' AND created_at < now() - interval '30 days'")
        ).rowcount
        done["simulator"] = conn.execute(
            text("DELETE FROM simulator_payments WHERE created_at < now() - interval '30 days'")
        ).rowcount
        backlog = conn.execute(
            text("""
            SELECT (SELECT count(*) FROM webhook_deliveries WHERE status = 'pending' AND next_attempt_at <= now()),
                   (SELECT count(*) FROM orders WHERE status = 'pending' AND next_check_at <= now()),
                   (SELECT count(*) FROM outbound_emails WHERE status = 'pending' AND next_attempt_at <= now())""")
        ).one()
    metrics.WORKER_BACKLOG.labels("webhooks").set(backlog[0])
    metrics.WORKER_BACKLOG.labels("reconcile").set(backlog[1])
    metrics.WORKER_BACKLOG.labels("email").set(backlog[2])
    return done
