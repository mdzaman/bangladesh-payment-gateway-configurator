"""Schema migrations (Alembic), callable from code and from the CLI.

An upgrade runs in one transaction while holding a PostgreSQL advisory lock,
so starting several replicas at once is safe: one migrates, the others wait
and then find nothing to do.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, pool, text

from .ids import lock_key

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def is_current(engine: Engine) -> bool:
    return current_revision(engine) == head_revision()


def upgrade(database_url: str, revision: str = "head") -> None:
    # A dedicated connection without the application's statement timeout.
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key("bdpay-cloud", "migrate")})
            cfg = alembic_config()
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, revision)
    finally:
        engine.dispose()


def downgrade(database_url: str, revision: str) -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key("bdpay-cloud", "migrate")})
            cfg = alembic_config()
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, revision)
    finally:
        engine.dispose()
