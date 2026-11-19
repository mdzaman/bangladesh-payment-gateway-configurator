"""Command line: ``bdpay-cloud <command>`` (or ``python -m bdpay_cloud``).

serve               run the API (and the dashboard, if BDPAY_CLOUD_WEB_DIST is set)
worker              run the background worker
migrate             bring the database schema up to date
doctor              check configuration, database, keys, clock
gen-key             print a new master key entry for BDPAY_CLOUD_MASTER_KEYS
rotate-keys         re-encrypt stored secrets with the first key in the ring
create-user         create an account (the way in when sign-up is closed)
disable-two-factor  switch two-factor sign-in off for an account that lost its authenticator
openapi             print the OpenAPI document of the merchant API
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from . import __version__, logs, migrate
from .crypto import (
    Vault,
    VaultError,
    aad_callback_secret,
    aad_gateway,
    aad_totp,
    aad_webhook_secret,
    generate_key_entry,
)
from .db import make_engine, ping, session_scope
from .settings import ConfigError, Settings, get_settings


def _settings() -> Settings:
    return get_settings()


def cmd_serve(args) -> int:
    import uvicorn

    settings = _settings()
    if args.migrate:
        migrate.upgrade(settings.database_url)
    uvicorn.run(
        "bdpay_cloud.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
        log_config=None,
        access_log=False,
        timeout_keep_alive=30,
    )
    return 0


def cmd_worker(args) -> int:
    from .engine.runtime import Runtime
    from .workers.runner import Worker

    settings = _settings()
    logs.configure(settings.log_level, settings.log_format)
    problems = settings.problems()
    if problems:
        raise ConfigError("refusing to start:\n  - " + "\n  - ".join(problems))
    from . import metrics

    metrics.install_provider_tap()
    worker = Worker(Runtime(settings))
    if args.once:
        print(f"handled {worker.tick()} item(s)")
        worker.close()
        return 0
    worker.run_forever()
    return 0


def cmd_migrate(args) -> int:
    settings = _settings()
    engine = make_engine(settings, pool_size=1)
    if args.check:
        current, head = migrate.current_revision(engine), migrate.head_revision()
        print(f"database: {current or '(empty)'}   code: {head}")
        return 0 if current == head else 1
    before = migrate.current_revision(engine)
    migrate.upgrade(settings.database_url, args.revision)
    after = migrate.current_revision(engine)
    print(f"schema {before or '(empty)'} -> {after}" if before != after else f"schema already at {after}")
    return 0


def cmd_doctor(_args) -> int:
    ok = True

    def line(mark: str, text_: str) -> None:
        print(f"{mark} {text_}")

    try:
        s = _settings()
    except Exception as e:
        line("✗", f"configuration cannot be read: {e}")
        return 1
    print(f"bdpay cloud {__version__}")
    print(f"environment : {s.env}")
    print(f"public url  : {s.public_url}")
    dhaka = datetime.now(timezone(timedelta(hours=6)))
    print(f"Dhaka time  : {dhaka:%Y-%m-%d %H:%M:%S} (Nagad and Upay timestamps use this)")
    print(f"egress IPs  : {', '.join(s.egress_ip_list) or '(not set)'}")
    print(
        f"live mode   : {s.live_mode}   sign-up: {'open' if s.allow_signup else 'invitation only'}   "
        f"simulator: {'on' if s.simulator else 'off'}   experimental: {'on' if s.allow_experimental else 'off'}"
    )
    for problem in s.problems():
        ok = False
        line("✗", problem)
    for warning in s.warnings():
        line("!", warning)
    if "localhost" in s.public_url or "127.0.0.1" in s.public_url:
        line("!", "public url is localhost: providers cannot reach callbacks or IPNs. Use a tunnel or a real domain.")
    try:
        vault = Vault.from_spec(s.effective_master_keys)
        line("✓", f"key ring: {', '.join(vault.key_ids)} (encrypting with '{vault.primary}')")
    except (VaultError, ConfigError) as e:
        ok = False
        line("✗", f"key ring: {e}")
    engine = make_engine(s, pool_size=1)
    if ping(engine):
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
        line("✓", f"database reachable (PostgreSQL {version})")
        current, head = migrate.current_revision(engine), migrate.head_revision()
        if current == head:
            line("✓", f"schema is current ({head})")
        else:
            ok = False
            line("✗", f"schema is {current or 'empty'}, code expects {head}: run `bdpay-cloud migrate`")
        if current == head:
            with engine.connect() as conn:
                age = conn.execute(
                    text(
                        "SELECT extract(epoch FROM now() - last_beat)::float8 FROM worker_heartbeats "
                        "WHERE name = 'worker'"
                    )
                ).scalar()
            if age is None:
                line("!", "no worker has ever run: start `bdpay-cloud worker` (webhooks and reconcile need it)")
            elif age > 180:
                line("!", f"the worker last reported {int(age)} s ago: is `bdpay-cloud worker` running?")
            else:
                line("✓", "worker is running")
    else:
        ok = False
        line("✗", "database is not reachable (BDPAY_CLOUD_DATABASE_URL)")
    if s.redis_url:
        from .security.ratelimit import RedisLimiter

        line(
            "✓" if RedisLimiter(s.redis_url).ping() else "!",
            "redis reachable"
            if RedisLimiter(s.redis_url).ping()
            else "redis not reachable: limits fall back to in-process",
        )
    return 0 if ok else 1


def cmd_gen_key(args) -> int:
    key_id = args.id or "k" + datetime.now(timezone.utc).strftime("%Y%m%d")
    print(generate_key_entry(key_id))
    print(
        "\nPut it FIRST in BDPAY_CLOUD_MASTER_KEYS (comma-separated, older keys after it), restart every\n"
        "process, then run `bdpay-cloud rotate-keys`. Keep it in a secret manager: without it the stored\n"
        "gateway credentials cannot be decrypted.",
        file=sys.stderr,
    )
    return 0


def cmd_rotate_keys(_args) -> int:
    """Re-encrypt every stored secret with the primary key. A value that no key
    in the ring can read is reported and left alone; the rest still rotate, so
    one damaged row cannot block a key rotation."""
    settings = _settings()
    vault = Vault.from_spec(settings.effective_master_keys)
    engine = make_engine(settings, pool_size=1)
    counts = {
        "environment secret(s)": 0,
        "gateway configuration(s)": 0,
        "webhook secret(s)": 0,
        "two-factor secret(s)": 0,
    }
    unreadable: list[str] = []

    def rotate(token: str, aad: str, label: str, what: str) -> str | None:
        if not vault.needs_rotation(token):
            return None
        try:
            fresh = vault.rotate(token, aad)
        except VaultError as e:
            unreadable.append(f"{label}: {e}")
            return None
        counts[what] += 1
        return fresh

    with engine.begin() as conn:
        for r in conn.execute(
            text("SELECT workspace_id, mode, callback_secret_enc FROM workspace_envs FOR UPDATE")
        ).all():
            v = rotate(
                r.callback_secret_enc,
                aad_callback_secret(r.workspace_id, r.mode),
                f"callback secret of {r.workspace_id}/{r.mode}",
                "environment secret(s)",
            )
            if v:
                conn.execute(
                    text("UPDATE workspace_envs SET callback_secret_enc = :v WHERE workspace_id = :ws AND mode = :m"),
                    {"v": v, "ws": r.workspace_id, "m": r.mode},
                )
        for r in conn.execute(
            text(
                "SELECT workspace_id, mode, gateway, secrets_enc FROM gateway_configs "
                "WHERE secrets_enc IS NOT NULL FOR UPDATE"
            )
        ).all():
            v = rotate(
                r.secrets_enc,
                aad_gateway(r.workspace_id, r.mode, r.gateway),
                f"{r.gateway} credentials of {r.workspace_id}/{r.mode}",
                "gateway configuration(s)",
            )
            if v:
                conn.execute(
                    text(
                        "UPDATE gateway_configs SET secrets_enc = :v WHERE workspace_id = :ws AND mode = :m "
                        "AND gateway = :g"
                    ),
                    {"v": v, "ws": r.workspace_id, "m": r.mode, "g": r.gateway},
                )
        for r in conn.execute(text("SELECT id, secret_enc FROM webhook_endpoints FOR UPDATE")).all():
            v = rotate(
                r.secret_enc, aad_webhook_secret(r.id), f"secret of webhook endpoint {r.id}", "webhook secret(s)"
            )
            if v:
                conn.execute(text("UPDATE webhook_endpoints SET secret_enc = :v WHERE id = :id"), {"v": v, "id": r.id})
        # Authenticator secrets of dashboard users, confirmed or still being set up.
        for r in conn.execute(
            text("SELECT id, email, totp_secret_enc FROM users WHERE totp_secret_enc IS NOT NULL FOR UPDATE")
        ).all():
            v = rotate(r.totp_secret_enc, aad_totp(r.id), f"two-factor secret of {r.email}", "two-factor secret(s)")
            if v:
                conn.execute(text("UPDATE users SET totp_secret_enc = :v WHERE id = :id"), {"v": v, "id": r.id})
        # Cached provider tokens are disposable: drop them and let them be fetched again.
        conn.execute(text("DELETE FROM provider_tokens"))
    print(f"re-encrypted with key '{vault.primary}': " + ", ".join(f"{n} {what}" for what, n in counts.items()))
    if unreadable:
        print(
            f"✗ {len(unreadable)} value(s) could not be read with any key in the ring and were left as they are:",
            file=sys.stderr,
        )
        for line in unreadable[:50]:
            print(f"    {line}", file=sys.stderr)
        print(
            "  Do NOT remove old keys yet. Restore the missing key, or have the workspace re-enter those "
            "credentials (for a two-factor secret: `bdpay-cloud disable-two-factor`, then the person sets it "
            "up again), then run this again.",
            file=sys.stderr,
        )
        return 1
    if len(vault.key_ids) > 1:
        print(f"once every process runs with the new ring, you can remove: {', '.join(vault.key_ids[1:])}")
    return 0


def cmd_create_user(args) -> int:
    from .db import utcnow
    from .engine.runtime import Runtime
    from .ids import new_id
    from .models import User
    from .security import passwords
    from .services import workspaces

    rt = Runtime(_settings())
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Password: ")
    email = args.email.strip().lower()
    problem = passwords.password_problem(password, email=email, name=args.name)
    if problem:
        print(f"✗ {problem}", file=sys.stderr)
        return 1
    with session_scope(rt.sessions) as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print("✗ an account with this email already exists", file=sys.stderr)
            return 1
        user = User(
            id=new_id("usr"),
            email=email,
            name=args.name,
            password_hash=passwords.hash_password(password),
            email_verified_at=utcnow(),
            is_platform_admin=args.platform_admin,
        )
        db.add(user)
        db.flush()
        ws = workspaces.create_workspace(
            db, rt.vault, rt.settings, name=args.workspace or f"{args.name}'s shop", owner=user
        )
        print(
            f"created {email}{' (platform admin)' if args.platform_admin else ''} with workspace '{ws.name}' "
            f"({ws.slug})"
        )
    rt.close()
    return 0


def cmd_disable_two_factor(args) -> int:
    """For someone who has lost the authenticator app and the recovery codes: no
    other way in is left, on purpose (a password reset does not bypass the second
    factor). Be sure who is asking before running this."""
    from .context import Actor
    from .engine.runtime import Runtime
    from .models import User
    from .services import audit, auth

    rt = Runtime(_settings())
    email = args.email.strip().lower()
    try:
        with session_scope(rt.sessions) as db:
            user = db.scalar(select(User).where(User.email == email).with_for_update())
            if user is None:
                print("✗ no account with this email", file=sys.stderr)
                return 1
            if user.totp_secret_enc is None:
                print(f"two-factor authentication is not on for {email}: nothing to do")
                return 0
            auth.remove_two_factor(db, user)
            audit.record(
                db,
                Actor.system("operator (command line)"),
                "user.two_factor_reset",
                target_type="user",
                target_id=user.id,
            )
    finally:
        rt.close()
    print(
        f"two-factor authentication is off for {email} and their sessions are ended. They sign in with the "
        "password alone and can set it up again under Account."
    )
    return 0


def cmd_seed_demo(args) -> int:
    from .devtools import seed_demo
    from .engine.runtime import Runtime

    rt = Runtime(_settings())
    try:
        made = seed_demo(rt, args.workspace, count=args.count, days=args.days)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    finally:
        rt.close()
    print("sandbox demo payments created: " + ", ".join(f"{n} {k}" for k, n in made.items() if n))
    return 0


def cmd_make_migration(args) -> int:
    """Development: write the next migration from the difference between the
    models and the development database."""
    from alembic import command

    from .migrate import MIGRATIONS, alembic_config

    settings = _settings()
    if settings.is_production:
        print("✗ make-migration is a development tool", file=sys.stderr)
        return 1
    number = len(list((MIGRATIONS / "versions").glob("[0-9]*.py"))) + 1
    command.revision(
        alembic_config(settings.database_url), message=args.message, autogenerate=True, rev_id=f"{number:04d}"
    )
    print("Read the generated file before committing it: it must be safe while the previous release still serves.")
    return 0


def cmd_openapi(_args) -> int:
    import contextlib

    from .api.app import create_app

    # Start-up log lines go to stderr here, so stdout is the document and nothing else.
    with contextlib.redirect_stdout(sys.stderr):
        spec = create_app().openapi()
    json.dump(spec, sys.stdout, indent=2)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdpay-cloud", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the API")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 - a server binds all interfaces in a container
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=1, help="processes (prefer more containers over more workers)")
    p.add_argument(
        "--forwarded-allow-ips",
        default="127.0.0.1",
        help="proxies whose X-Forwarded-* headers are trusted ('*' behind a private load balancer)",
    )
    p.add_argument("--migrate", action="store_true", help="run migrations before starting")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("worker", help="run the background worker")
    p.add_argument("--once", action="store_true", help="process what is due, then exit")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("migrate", help="upgrade the database schema")
    p.add_argument("--revision", default="head")
    p.add_argument("--check", action="store_true", help="only report whether the schema is current (exit 1 if not)")
    p.set_defaults(fn=cmd_migrate)

    sub.add_parser("doctor", help="check configuration and connectivity").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("gen-key", help="print a new master key")
    p.add_argument("--id", help="key id (default: k<today>)")
    p.set_defaults(fn=cmd_gen_key)

    sub.add_parser("rotate-keys", help="re-encrypt stored secrets with the primary key").set_defaults(
        fn=cmd_rotate_keys
    )

    p = sub.add_parser("create-user", help="create an account and its first workspace")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--workspace", help="workspace name")
    p.add_argument("--platform-admin", action="store_true")
    p.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    p.set_defaults(fn=cmd_create_user)

    p = sub.add_parser("disable-two-factor", help="switch two-factor sign-in off for one account")
    p.add_argument("--email", required=True)
    p.set_defaults(fn=cmd_disable_two_factor)

    p = sub.add_parser("seed-demo", help="development: fill a workspace's sandbox with simulator payments")
    p.add_argument("--workspace", required=True, help="workspace address (the slug in its dashboard URL)")
    p.add_argument("--count", type=int, default=140)
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(fn=cmd_seed_demo)

    p = sub.add_parser("make-migration", help="development: generate the next schema migration from the models")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(fn=cmd_make_migration)

    sub.add_parser("openapi", help="print the OpenAPI document").set_defaults(fn=cmd_openapi)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, VaultError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
