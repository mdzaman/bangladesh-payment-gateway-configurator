"""Test setup. The suite runs against a real PostgreSQL (the service uses row
locks, advisory locks, SKIP LOCKED and JSONB, none of which a stand-in has).

    docker compose -f docker-compose.dev.yml up -d postgres
    pytest

Point ``BDPAY_CLOUD_TEST_DATABASE_URL`` elsewhere if you like; the database
name must contain "test" because its public schema is dropped and rebuilt.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).parent))  # so tests can `import helpers`

from bdpay_cloud import migrate
from bdpay_cloud.db import make_engine
from bdpay_cloud.engine.runtime import Runtime
from bdpay_cloud.settings import Settings

TEST_DB = os.environ.get(
    "BDPAY_CLOUD_TEST_DATABASE_URL", "postgresql+psycopg://bdpay:bdpay@127.0.0.1:54329/bdpay_cloud_test"
)
TEST_KEYS = "t1:" + base64.b64encode(b"k" * 32).decode()


def make_settings(**over) -> Settings:
    base = {
        "env": "test",
        "database_url": TEST_DB,
        "public_url": "https://pay.test",
        "master_keys": TEST_KEYS,
        "mail_backend": "console",
        "log_format": "text",
        "redis_url": "",
        "egress_ips": "203.0.113.10",
        "web_dist": "",
        "metrics_token": "",
        "allow_experimental": False,
        "simulator": True,
        "allow_private_targets": False,
        "live_mode": "self_serve",
        "allow_signup": True,
        "base_url_allowlist": "",
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session")
def engine(settings):
    url = settings.database_url
    if "test" not in url.rsplit("/", 1)[-1]:
        pytest.exit("refusing to run: the test database name must contain 'test'", returncode=2)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as e:
        pytest.exit(
            f"PostgreSQL is not reachable at {url.split('@')[-1]}: start it with "
            f"`docker compose -f docker-compose.dev.yml up -d postgres` ({e.__class__.__name__})",
            returncode=2,
        )
    finally:
        admin.dispose()
    migrate.upgrade(url)
    eng = make_engine(settings)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def rt(settings, engine):
    runtime = Runtime(settings, engine=engine)
    yield runtime
    runtime.close()


@pytest.fixture
def tenant(rt):
    import helpers

    return helpers.new_tenant(rt)


@pytest.fixture(scope="session")
def app(rt):
    from bdpay_cloud.api.app import create_app
    from bdpay_cloud.security import passwords

    passwords.use_fast_parameters_for_tests()
    return create_app(runtime=rt)


@pytest.fixture
def new_client(app):
    """Factory for independent browser sessions (each has its own cookie jar)."""
    from fastapi.testclient import TestClient

    app.state.limiter.reset()
    clients = []

    def make():
        c = TestClient(app, base_url="https://pay.test")
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()


@pytest.fixture
def client(new_client):
    return new_client()


@pytest.fixture
def dash(new_client):
    """A signed-up owner with a fresh workspace."""
    import helpers

    d = helpers.Dash(new_client())
    r = d.signup()
    assert r.status_code == 200, r.text
    return d


@pytest.fixture
def provider(rt):
    """Route provider HTTP to a fake: ``provider(handler)``. Restored afterwards."""
    import httpx

    def use(handler):
        rt.use_transport(httpx.MockTransport(handler))
        return handler

    yield use
    rt.use_transport(None)
