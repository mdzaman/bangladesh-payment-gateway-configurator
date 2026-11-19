"""Alembic environment. Run through `bdpay-cloud migrate`, which hands over an
open connection so the whole upgrade is one transaction under an advisory lock."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from bdpay_cloud.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from bdpay_cloud.settings import get_settings

    engine = create_engine(
        config.get_main_option("sqlalchemy.url") or get_settings().database_url, poolclass=pool.NullPool
    )
    with engine.connect() as conn:
        _run(conn)
        conn.commit()


def run_migrations_offline() -> None:
    from bdpay_cloud.settings import get_settings

    context.configure(
        url=config.get_main_option("sqlalchemy.url") or get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
