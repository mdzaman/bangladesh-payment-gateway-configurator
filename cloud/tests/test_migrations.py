import uuid

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from bdpay_cloud import migrate
from bdpay_cloud.models import Base


def test_models_match_the_migrated_schema(engine):
    """Catches a model change that forgot its migration (or the reverse)."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff


def test_schema_is_at_head(engine):
    assert migrate.is_current(engine)
    assert migrate.head_revision() == migrate.current_revision(engine)


def test_upgrade_downgrade_upgrade_on_a_fresh_database(settings):
    name = "bdpay_cloud_test_mig_" + uuid.uuid4().hex[:8]
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    url = settings.database_url.rsplit("/", 1)[0] + "/" + name
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate.upgrade(url)
        migrate.upgrade(url)  # running it again is a no-op
        fresh = create_engine(url)
        tables = set(inspect(fresh).get_table_names())
        assert {"orders", "order_events", "gateway_configs", "webhook_deliveries", "workspace_envs"} <= tables
        migrate.downgrade(url, "base")
        assert set(inspect(fresh).get_table_names()) <= {"alembic_version"}
        migrate.upgrade(url)
        assert migrate.is_current(fresh)
        fresh.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
