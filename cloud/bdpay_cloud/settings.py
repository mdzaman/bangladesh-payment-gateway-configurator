"""Platform configuration.

Everything comes from environment variables prefixed ``BDPAY_CLOUD_`` (a ``.env``
file in the working directory is read as well). Nothing secret has a usable
default in production: the service refuses to start instead.
"""

from __future__ import annotations

import functools
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used only when no key is configured outside production, so a fresh checkout
# runs with zero setup. Data encrypted with it is NOT protected.
DEV_MASTER_KEY = "dev:ZGV2LW9ubHktaW5zZWN1cmUta2V5LTAwMDAwMDAwMDA="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BDPAY_CLOUD_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- deployment -------------------------------------------------------
    env: Literal["development", "test", "production"] = "development"
    # Public https origin of this service. Providers send customers and IPNs here,
    # so it must be reachable from the internet in production.
    public_url: str = "http://localhost:8000"
    brand_name: str = "bdpay cloud"
    support_email: str = ""

    # --- storage ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://bdpay:bdpay@127.0.0.1:54329/bdpay_cloud"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Set when connecting through PgBouncer in transaction mode: disables
    # server-side prepared statements and per-connection startup options.
    db_pgbouncer: bool = False
    # Optional. Used for rate limiting that is exact across replicas.
    redis_url: str = ""

    # --- encryption of tenant secrets --------------------------------------
    # Comma-separated key ring "id:base64(32 bytes)". The first key encrypts;
    # every key can decrypt. See `bdpay-cloud gen-key` and `rotate-keys`.
    master_keys: str = ""

    # --- product switches ---------------------------------------------------
    allow_signup: bool = True
    # self_serve: owners activate live mode themselves after the attestation
    # approval:   a platform admin must approve each workspace
    # disabled:   sandbox only
    live_mode: Literal["self_serve", "approval", "disabled"] = "self_serve"
    require_verified_email_for_live: bool = True
    # UddoktaPay-style wallet automation. Off by default; see docs/compliance.md.
    allow_experimental: bool = False
    # Built-in fake provider for sandbox mode (never available in live mode).
    simulator: bool = True
    # Outbound IPv4 address(es) of this deployment. Shown to merchants because
    # Nagad and Upay whitelist the caller's address.
    egress_ips: str = ""
    # Development only: lets webhook and gateway base URLs point at private or
    # loopback addresses. Never enable on a shared deployment (SSRF).
    allow_private_targets: bool = False
    # Host names (or suffixes like ".upaybd.com") a workspace may use for a
    # gateway base-URL override (Upay, UddoktaPay, Nagad). Empty: any public
    # host. Set it on a shared deployment: a workspace that controls the "gateway"
    # host also controls where its checkout sends customers.
    base_url_allowlist: str = ""

    # --- sessions and limits -------------------------------------------------
    session_idle_hours: int = 12
    session_max_days: int = 14
    api_rate_per_minute: int = 600
    login_rate_per_15min: int = 10
    # Per client IP. Generous on purpose: in Bangladesh thousands of mobile
    # customers can share one carrier-NAT address.
    signup_rate_per_hour: int = 30
    hosted_rate_per_minute: int = 600
    max_workspaces_per_user: int = 10
    max_api_keys_per_env: int = 20
    max_webhook_endpoints_per_env: int = 10
    checkout_session_ttl_minutes: int = 60

    # --- workers ----------------------------------------------------------------
    threadpool_size: int = 64
    worker_concurrency: int = 8
    worker_poll_seconds: float = 2.0
    reconcile_expire_hours: int = 24
    webhook_timeout_seconds: float = 10.0

    # --- mail -------------------------------------------------------------------
    mail_backend: Literal["console", "smtp"] = "console"
    mail_from: str = "bdpay cloud <no-reply@localhost>"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_tls: Literal["none", "starttls", "ssl"] = "none"

    # --- operations ---------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    # If set, GET /metrics needs "Authorization: Bearer <token>".
    metrics_token: str = ""
    docs_enabled: bool = True
    # Folder with the built dashboard (web/dist). Empty: the API serves no UI.
    web_dist: str = ""

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------ derived
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).hostname or ""

    @property
    def egress_ip_list(self) -> list[str]:
        return [x.strip() for x in self.egress_ips.split(",") if x.strip()]

    @property
    def base_url_allowed_hosts(self) -> list[str]:
        return [x.strip().lower() for x in self.base_url_allowlist.split(",") if x.strip()]

    def base_url_host_allowed(self, host: str) -> bool:
        allowed = self.base_url_allowed_hosts
        host = (host or "").lower()
        return not allowed or any(host == a.lstrip(".") or host.endswith("." + a.lstrip(".")) for a in allowed)

    @property
    def effective_master_keys(self) -> str:
        if self.master_keys.strip():
            return self.master_keys
        if self.is_production:
            raise ConfigError("BDPAY_CLOUD_MASTER_KEYS is required in production (run: bdpay-cloud gen-key)")
        return DEV_MASTER_KEY

    def problems(self) -> list[str]:
        """Configuration that must be fixed before serving real traffic."""
        out: list[str] = []
        if not self.is_production:
            return out
        if not self.public_url.startswith("https://"):
            out.append("BDPAY_CLOUD_PUBLIC_URL must be https:// in production")
        if self.public_host in ("localhost", "127.0.0.1", "::1"):
            out.append("BDPAY_CLOUD_PUBLIC_URL cannot be localhost in production")
        if not self.master_keys.strip():
            out.append("BDPAY_CLOUD_MASTER_KEYS is required in production (run: bdpay-cloud gen-key)")
        elif self.master_keys.strip() == DEV_MASTER_KEY:
            out.append("BDPAY_CLOUD_MASTER_KEYS is the published development key; generate your own")
        if self.allow_private_targets:
            out.append("BDPAY_CLOUD_ALLOW_PRIVATE_TARGETS must be off in production (SSRF)")
        if self.mail_backend == "console":
            out.append("BDPAY_CLOUD_MAIL_BACKEND=console only prints email; configure smtp in production")
        return out

    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self.master_keys.strip() and not self.is_production:
            out.append("no BDPAY_CLOUD_MASTER_KEYS set: using the published development key (not secret)")
        if self.is_production and not self.egress_ip_list:
            out.append("BDPAY_CLOUD_EGRESS_IPS is empty: merchants cannot see which IP to whitelist for Nagad/Upay")
        if self.is_production and not self.redis_url:
            out.append("no BDPAY_CLOUD_REDIS_URL: rate limits are per process, not shared between replicas")
        if self.is_production and not self.base_url_allowed_hosts:
            out.append(
                "BDPAY_CLOUD_BASE_URL_ALLOWLIST is empty: workspaces may point Upay/UddoktaPay/Nagad base "
                "URLs at any public host"
            )
        if self.is_production and not self.metrics_token:
            out.append("BDPAY_CLOUD_METRICS_TOKEN is empty: keep /metrics off the public internet")
        return out


class ConfigError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
