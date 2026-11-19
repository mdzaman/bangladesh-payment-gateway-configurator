"""Database engine and small helpers. PostgreSQL 14 or newer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .ids import lock_key
from .settings import Settings

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(settings: Settings, *, pool_size: int | None = None) -> Engine:
    connect_args: dict = {"application_name": "bdpay-cloud"}
    if settings.db_pgbouncer:
        # Transaction pooling: no server-side prepared statements, no startup options.
        connect_args["prepare_threshold"] = None
    else:
        # A provider call can hold a transaction open for the length of one HTTP
        # timeout (token refresh, refund lock), so idle-in-transaction is generous.
        connect_args["options"] = (
            "-c timezone=UTC -c statement_timeout=30000 "
            "-c idle_in_transaction_session_timeout=120000 -c lock_timeout=15000"
        )
    return create_engine(
        settings.database_url,
        pool_size=pool_size or settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def xact_lock(conn: Connection, *parts: str) -> None:
    """Block until this transaction holds the advisory lock named by ``parts``.
    Released automatically at commit or rollback, so it is safe behind PgBouncer."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key(*parts)})


def try_xact_lock(conn: Connection, *parts: str) -> bool:
    return bool(conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(*parts)}).scalar())


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
