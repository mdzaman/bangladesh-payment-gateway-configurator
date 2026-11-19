"""The tenant runtime: one ``BdPay`` per workspace environment.

``TenantPay`` *is* the reference ``BdPay`` service object. It inherits
``create_order``, ``handle_callback``, ``handle_ipn``, ``requery``, ``reconcile``
and the core rule ``_apply`` unchanged (server-side amount truth, redirects and
IPNs re-verified, forward-only state machine). What differs is only where it
gets its parts from:

==================  =========================================================
reference BdPay      TenantPay
==================  =========================================================
``BDPAY_*`` env      the workspace's stored, encrypted gateway configuration
SQLite ``Store``     ``PgStore`` scoped to (workspace, mode)
``BDPAY_SECRET``     a per-environment callback secret (never shared by tenants)
``/bdpay`` prefix    ``/p/<environment key>``
==================  =========================================================

Three methods are tightened, none loosened:

* ``start_payment`` opens at most one provider session per order, even under
  concurrent calls, and returns the same checkout URL if asked again. A second
  session for the same order could be paid and then rejected as "not this
  order's payment", which is money taken with nothing to show for it.
* ``refund`` serialises refunds per order, and records a refund whose outcome
  is unknown (timeout) so someone checks the provider panel.
* ``driver`` explains a missing gateway in terms of the dashboard, not env vars.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import Engine, text

from bdpay.config import Settings as CoreSettings
from bdpay.core import BdPayError, Driver, RefundResult, Status
from bdpay.http import redact
from bdpay.service import BdPay

from ..context import Scope
from ..crypto import Vault, aad_callback_secret, aad_gateway
from ..db import make_engine, make_session_factory
from ..settings import Settings
from .drivers import build_driver
from .http import CloudHttp
from .orders import CloudOrder
from .store import PgStore
from .tokens import PgTokenCache

log = logging.getLogger("bdpay_cloud.engine")


@dataclass(frozen=True)
class EnvInfo:
    """One workspace environment, as loaded for a request."""

    scope: Scope
    public_key: str
    callback_secret: str
    result_url: str | None
    workspace_name: str
    workspace_slug: str
    workspace_settings: dict[str, Any]
    live_status: str
    suspended: bool
    archived: bool

    @property
    def display_name(self) -> str:
        return (self.workspace_settings.get("display_name") or self.workspace_name).strip()

    @property
    def usable(self) -> bool:
        """May this environment take new payments right now?"""
        if self.suspended or self.archived:
            return False
        return self.scope.mode == "sandbox" or self.live_status == "active"


@dataclass(frozen=True)
class GatewayValues:
    enabled: bool
    values: dict[str, str]


_ENV_SQL = """
    SELECT e.workspace_id, e.mode, e.public_key, e.callback_secret_enc, e.result_url,
           w.name, w.slug, w.settings, w.live_status, w.suspended_at, w.archived_at
      FROM workspace_envs e JOIN workspaces w ON w.id = e.workspace_id
"""


class Runtime:
    """Process-wide dependencies. One per process; safe to share between threads."""

    def __init__(self, settings: Settings, engine: Engine | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.engine = engine or make_engine(settings)
        self.sessions = make_session_factory(self.engine)
        self.vault = Vault.from_spec(settings.effective_master_keys)
        self.tokens = PgTokenCache(self.engine, self.vault)
        self._http: dict[str, CloudHttp] = {}
        self.use_transport(transport)

    # ------------------------------------------------------------------ http
    def use_transport(self, transport: httpx.BaseTransport | None) -> None:
        """Swap the provider transport (tests use ``httpx.MockTransport``)."""
        for h in self._http.values():
            h.close()
        self._http = {
            mode: CloudHttp(mode, transport=transport, allow_private=self.settings.allow_private_targets)
            for mode in ("sandbox", "live")
        }

    def http(self, mode: str) -> CloudHttp:
        return self._http[mode]

    def close(self) -> None:
        for h in self._http.values():
            h.close()
        self.engine.dispose()

    # --------------------------------------------------------------- loading
    def _env(self, r) -> EnvInfo:
        scope = Scope(r.workspace_id, r.mode)
        return EnvInfo(
            scope=scope,
            public_key=r.public_key,
            callback_secret=self.vault.decrypt_text(r.callback_secret_enc, aad_callback_secret(r.workspace_id, r.mode)),
            result_url=r.result_url,
            workspace_name=r.name,
            workspace_slug=r.slug,
            workspace_settings=r.settings or {},
            live_status=r.live_status,
            suspended=r.suspended_at is not None,
            archived=r.archived_at is not None,
        )

    def load_env(self, scope: Scope) -> EnvInfo | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                text(_ENV_SQL + " WHERE e.workspace_id = :ws AND e.mode = :mode"), scope.params
            ).one_or_none()
        return self._env(r) if r else None

    def load_env_by_public_key(self, public_key: str) -> EnvInfo | None:
        with self.engine.connect() as conn:
            r = conn.execute(text(_ENV_SQL + " WHERE e.public_key = :k"), {"k": public_key}).one_or_none()
        return self._env(r) if r else None

    def gateway_values(self, scope: Scope, gateway: str) -> GatewayValues | None:
        """The workspace's settings for one gateway, secrets decrypted."""
        with self.engine.connect() as conn:
            r = conn.execute(
                text(
                    "SELECT enabled, config, secrets_enc FROM gateway_configs "
                    "WHERE workspace_id = :ws AND mode = :mode AND gateway = :g"
                ),
                {**scope.params, "g": gateway},
            ).one_or_none()
        if r is None:
            return None
        secrets = self.vault.decrypt_json(r.secrets_enc, aad_gateway(scope.workspace_id, scope.mode, gateway))
        return GatewayValues(bool(r.enabled), {**(r.config or {}), **secrets})

    def driver_for(self, scope: Scope, gateway: str) -> Driver:
        """Driver built from the stored configuration, enabled or not (used by
        the connection test before a gateway is switched on)."""
        loaded = self.gateway_values(scope, gateway)
        if loaded is None:
            raise BdPayError("CONFIG", f"gateway '{gateway}' is not configured", gateway)
        return build_driver(self, scope, gateway, loaded.values)

    def pay(self, env: EnvInfo | Scope) -> TenantPay:
        if isinstance(env, Scope):
            loaded = self.load_env(env)
            if loaded is None:
                raise BdPayError("NOT_FOUND", "unknown workspace environment")
            env = loaded
        return TenantPay(self, env)

    def mark_gateway(self, scope: Scope, gateway: str, error: BdPayError | None) -> None:
        """Remember whether the last real call with these credentials worked."""
        try:
            with self.engine.begin() as conn:
                if error is None:
                    conn.execute(
                        text("""
                        UPDATE gateway_configs
                           SET last_success_at = now(), status = 'verified', last_error = NULL, last_error_at = NULL
                         WHERE workspace_id = :ws AND mode = :mode AND gateway = :g
                           AND (status <> 'verified' OR last_success_at IS NULL
                                OR last_success_at < now() - interval '5 minutes')"""),
                        {**scope.params, "g": gateway},
                    )
                else:
                    conn.execute(
                        text("""
                        UPDATE gateway_configs
                           SET last_error_at = now(), last_error = :msg,
                               status = CASE WHEN :rejected THEN 'rejected' ELSE status END
                         WHERE workspace_id = :ws AND mode = :mode AND gateway = :g"""),
                        {
                            **scope.params,
                            "g": gateway,
                            "rejected": error.code in ("AUTH", "CONFIG"),
                            "msg": str(redact(f"{error.code}: {error.message}"))[:500],
                        },
                    )
        except Exception:  # health bookkeeping must never break a payment
            log.exception("could not record gateway health for %s", gateway)


class LazyDrivers(dict):
    """``BdPay.drivers`` that builds a driver the first time it is asked for,
    from the workspace's stored configuration.

    A *disabled* gateway still yields a driver: switching a gateway off stops
    new payments, but payments already in flight must still be verified,
    reconciled and refunded. ``TenantPay`` checks ``is_enabled`` before it
    creates or starts anything."""

    def __init__(self, rt: Runtime, scope: Scope):
        super().__init__()
        self._rt = rt
        self._scope = scope
        self.enabled: dict[str, bool] = {}

    def get(self, gateway, default=None):
        if gateway in self:
            return self[gateway]
        loaded = self._rt.gateway_values(self._scope, gateway)
        if loaded is None:
            return default
        driver = build_driver(self._rt, self._scope, gateway, loaded.values)
        self[gateway] = driver
        self.enabled[gateway] = loaded.enabled
        return driver

    def is_enabled(self, gateway: str) -> bool:
        return self.get(gateway) is not None and self.enabled.get(gateway, True)


class TenantPay(BdPay):
    def __init__(
        self,
        rt: Runtime,
        env: EnvInfo,
        *,
        drivers: dict | None = None,
        core_settings: CoreSettings | None = None,
        callback_prefix: str | None = None,
    ):
        # Deliberately not calling BdPay.__init__: it wires the env/SQLite parts.
        self.rt = rt
        self.env = env
        self.scope = env.scope
        self.settings = core_settings or CoreSettings(
            environment=env.scope.mode,
            gateways=(),
            secret=env.callback_secret,
            db_path="",
            public_url=rt.settings.public_url,
            values={},
        )
        self.store = PgStore(rt.engine, env.scope, rt.settings.public_url)
        self.tokens = rt.tokens
        self.http = rt.http(env.scope.mode)
        self.callback_prefix = (callback_prefix if callback_prefix is not None else f"/p/{env.public_key}").rstrip("/")
        self.drivers = drivers if drivers is not None else LazyDrivers(rt, env.scope)

    # ------------------------------------------------------------- overrides
    def driver(self, gateway: str) -> Driver:
        d = self.drivers.get(gateway)
        if not d:
            raise BdPayError(
                "CONFIG",
                f"gateway '{gateway}' is not configured for this workspace in {self.scope.mode} mode",
                gateway or "core",
            )
        return d

    def require_enabled(self, gateway: str) -> Driver:
        """The driver for a *new* payment: the gateway must be switched on."""
        d = self.drivers.get(gateway)
        enabled = self.drivers.is_enabled(gateway) if isinstance(self.drivers, LazyDrivers) else bool(d)
        if not d or not enabled:
            raise BdPayError(
                "CONFIG",
                f"gateway '{gateway}' is not enabled for this workspace in {self.scope.mode} mode",
                gateway or "core",
            )
        return d

    def create_order(
        self,
        order_id: str,
        amount,
        gateway: str,
        customer: dict | None = None,
        currency: str = "BDT",
        *,
        metadata: dict | None = None,
        return_url: str | None = None,
        source: str = "api",
        checkout_session_id: str | None = None,
    ) -> CloudOrder:
        self.require_enabled(gateway)
        with self.store.creating(
            metadata=metadata, return_url=return_url, source=source, checkout_session_id=checkout_session_id
        ):
            return super().create_order(order_id, amount, gateway, customer, currency)  # type: ignore[return-value]

    def start_payment(self, order_id: str) -> str:
        order = self._order(order_id)
        if order.status not in (Status.CREATED, Status.PENDING):
            raise BdPayError("VALIDATION", f"order is {order.status.value}; cannot start payment")
        if order.status == Status.PENDING:
            return self._existing_url(order)
        driver = self.require_enabled(order.gateway)
        if not self.store.claim_start(order_id):
            again = self._order(order_id)
            if again.status == Status.PENDING:
                return self._existing_url(again)
            raise BdPayError(
                "DUPLICATE",
                "payment for this order is being started by another request; retry in a moment",
                retryable=True,
            )
        try:
            session = driver.create_payment(order, self.callback_urls(order))
        except BdPayError as e:
            self.store.release_start(order_id)
            self.rt.mark_gateway(self.scope, order.gateway, e)
            raise
        except BaseException:
            self.store.release_start(order_id)
            raise
        self.store.save_start(order_id, session.provider_payment_id, session.redirect_url)
        self.store.transition(order_id, Status.PENDING, {"provider_payment_id": session.provider_payment_id})
        self.rt.mark_gateway(self.scope, order.gateway, None)
        return session.redirect_url

    @staticmethod
    def _existing_url(order) -> str:
        url = getattr(order, "redirect_url", None)
        if not url:
            raise BdPayError(
                "VALIDATION", "payment was already started for this order; use a new order id for a new attempt"
            )
        return url

    def refund(self, order_id: str, amount=None, reason: str = "customer request") -> RefundResult:
        with self.store.exclusive(order_id, "refund"):
            try:
                return super().refund(order_id, amount, reason)
            except BdPayError as e:
                if e.code in ("TIMEOUT", "PROVIDER_UNAVAILABLE"):
                    # The provider may or may not have refunded: flag it for a human.
                    order = self.store.get(order_id)
                    self.store.event(
                        order_id,
                        "refund_unconfirmed",
                        order.status.value if order else None,
                        None,
                        {"code": e.code, "message": e.message, "requested": str(amount or "full")},
                    )
                raise

    # ---------------------------------------------------------------- extras
    def result_token(self, order_id: str) -> str:
        """Signature for the hosted result page, so order status cannot be
        read by guessing order ids."""
        import hashlib
        import hmac

        return hmac.new(self.settings.secret.encode(), f"result|{order_id}".encode(), hashlib.sha256).hexdigest()[:32]

    def check_result_token(self, order_id: str, token: str) -> bool:
        import hmac

        return hmac.compare_digest(self.result_token(order_id), token or "")


def make_runtime(settings: Settings, **kwargs) -> Runtime:
    return Runtime(settings, **kwargs)


__all__ = [
    "EnvInfo",
    "GatewayValues",
    "LazyDrivers",
    "Runtime",
    "TenantPay",
    "make_engine",
    "make_runtime",
    "make_session_factory",
]
