"""Configuration. Everything comes from environment variables (a .env file is
loaded automatically if present). Secrets never live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class GatewayConfig:
    gateway: str
    environment: str
    values: Mapping[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        v = self.values.get(key)
        return v if v not in (None, "") else default

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.get(k)]
        if missing:
            from .core import BdPayError
            raise BdPayError("CONFIG", f"missing settings: {', '.join(missing)}", self.gateway)

    def __getitem__(self, key: str) -> str:
        self.require(key)
        return self.values[key]


@dataclass
class Settings:
    environment: str = "sandbox"
    gateways: tuple = ()
    secret: str = ""
    db_path: str = "bdpay.sqlite3"
    public_url: str = "http://localhost:8000"
    values: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, dotenv: bool = True) -> "Settings":
        if env is None:
            if dotenv:
                load_dotenv(os.environ.get("BDPAY_DOTENV", ".env"))
            env = os.environ
        environment = (env.get("BDPAY_ENV") or "sandbox").strip().lower()
        if environment not in ("sandbox", "live"):
            from .core import BdPayError
            raise BdPayError("CONFIG", "BDPAY_ENV must be 'sandbox' or 'live'")
        gateways = tuple(g.strip().lower() for g in (env.get("BDPAY_GATEWAYS") or "").split(",") if g.strip())
        secret = env.get("BDPAY_SECRET") or ""
        if environment == "live" and len(secret) < 32:
            from .core import BdPayError
            raise BdPayError("CONFIG", "BDPAY_SECRET must be at least 32 characters in live mode")
        if not secret:
            secret = "dev-only-insecure-secret-change-me-0000"
        return cls(
            environment=environment,
            gateways=gateways,
            secret=secret,
            db_path=env.get("BDPAY_DB") or "bdpay.sqlite3",
            public_url=(env.get("BDPAY_PUBLIC_URL") or "http://localhost:8000").rstrip("/"),
            values=dict(env),
        )

    def gateway(self, name: str) -> GatewayConfig:
        return GatewayConfig(name, self.environment, self.values)
