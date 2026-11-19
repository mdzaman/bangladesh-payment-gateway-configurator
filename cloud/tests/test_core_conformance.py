"""The reference test-suite (core/tests, 35 tests), run against the
multi-tenant PostgreSQL engine instead of the single-tenant SQLite one.

Nothing in core/tests is edited. Its ``make_pay`` helper is swapped for one
that returns a ``TenantPay`` over ``PgStore`` and ``PgTokenCache`` for a brand
new workspace, and every test is then re-exported from this module. If the
hosted engine ever stops behaving like the reference (amount truth, forward
only state machine, token reuse, signature checks, refunds), this file fails.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import httpx
import pytest

from bdpay.config import Settings as CoreSettings
from bdpay.registry import load_driver_class
from bdpay_cloud.engine.http import CloudHttp
from bdpay_cloud.engine.runtime import TenantPay
from bdpay_cloud.engine.store import PgStore

import helpers

CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
_state: dict = {}


def make_pay(gateways, env_extra, handler):
    rt = _state["rt"]
    env = {
        "BDPAY_ENV": "sandbox",
        "BDPAY_GATEWAYS": ",".join(gateways),
        "BDPAY_SECRET": "s" * 40,
        "BDPAY_PUBLIC_URL": "https://shop.test",
        **env_extra,
    }
    core_settings = CoreSettings.from_env(env)
    tenant = helpers.new_tenant(rt)  # every call gets its own tenant
    http = CloudHttp("sandbox", transport=httpx.MockTransport(handler))
    pay = TenantPay(rt, tenant.sandbox, drivers={}, core_settings=core_settings, callback_prefix="/bdpay")
    pay.http = http
    pay.tokens = rt.tokens.scoped(uuid.uuid4().hex)
    for name in core_settings.gateways:
        pay.drivers[name] = load_driver_class(name)(core_settings.gateway(name), http, pay.tokens)
    assert isinstance(pay.store, PgStore)
    return pay


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_reference_suite() -> dict[str, types.ModuleType]:
    original = _load("_core_reference_conftest", CORE_TESTS / "conftest.py")
    shim = types.ModuleType("conftest")
    shim.make_pay = make_pay
    shim.callback_parts = original.callback_parts
    shim.body = original.body
    saved = sys.modules.get("conftest")
    sys.modules["conftest"] = shim
    try:
        return {p.stem: _load(f"_core_reference_{p.stem}", p) for p in sorted(CORE_TESTS.glob("test_*.py"))}
    finally:
        if saved is None:
            sys.modules.pop("conftest", None)
        else:
            sys.modules["conftest"] = saved


_MODULES = _import_reference_suite()
REFERENCE_TEST_COUNT = 0
for _stem, _module in _MODULES.items():
    for _name in dir(_module):
        if _name.startswith("test_") and callable(getattr(_module, _name)):
            globals()[f"{_stem}__{_name}"] = getattr(_module, _name)
            REFERENCE_TEST_COUNT += 1


@pytest.fixture(autouse=True)
def _reference_environment(rt):
    """core/tests/test_web.py does ``import test_bkash`` at call time."""
    _state["rt"] = rt
    saved = {k: sys.modules.get(k) for k in _MODULES}
    sys.modules.update(_MODULES)
    yield
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_the_whole_reference_suite_was_collected():
    assert REFERENCE_TEST_COUNT == 35
