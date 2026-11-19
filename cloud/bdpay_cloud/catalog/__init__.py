"""Gateway catalog: which gateways exist, what each needs, which fields are secret.

``gateways.json`` is the single source of truth. The service validates and
stores credentials from it, and the web app renders its forms from the same file.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PATH = Path(__file__).with_name("gateways.json")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str  # text | secret | textarea | select | check
    required: bool = False
    demo: str | None = None  # published sandbox value, refused in live mode
    default: str | None = None
    options: tuple[str, ...] = ()
    is_url: bool = False

    @property
    def is_secret(self) -> bool:
        return self.type in ("secret", "textarea")


@dataclass(frozen=True)
class GatewaySpec:
    id: str
    name: str
    stability: str  # stable | beta | exp
    fields: tuple[Field, ...]
    ipn: bool = False  # has an IPN URL to register with the provider
    whitelist: bool = False  # provider whitelists the caller's IP
    cloud_only: bool = False
    sandbox_only: bool = False
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)

    @property
    def secret_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.is_secret)

    @property
    def experimental(self) -> bool:
        return self.stability == "exp"


@dataclass(frozen=True)
class Catalog:
    gateways: tuple[GatewaySpec, ...]
    via: tuple[dict[str, Any], ...]

    def get(self, gateway_id: str) -> GatewaySpec | None:
        return next((g for g in self.gateways if g.id == gateway_id), None)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(g.id for g in self.gateways)


def _field(d: dict[str, Any]) -> Field:
    return Field(
        key=d["k"],
        label=d["l"],
        type=d["t"],
        required=bool(d.get("req")),
        demo=d.get("demo"),
        default=d.get("cloudVal", d.get("val")),
        options=tuple(o[0] for o in d.get("opts", ())),
        is_url=bool(d.get("url")),
    )


@functools.lru_cache(maxsize=1)
def load() -> Catalog:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    gateways = tuple(
        GatewaySpec(
            id=g["id"],
            name=g["name"],
            stability=g["st"],
            fields=tuple(_field(f) for f in g["f"]),
            ipn=bool(g.get("ipn")),
            whitelist=bool(g.get("whitelist")),
            cloud_only=bool(g.get("cloudOnly")),
            sandbox_only=bool(g.get("sandboxOnly")),
            raw=g,
        )
        for g in data["gateways"]
    )
    return Catalog(gateways=gateways, via=tuple(data["via"]))


def raw_json() -> dict[str, Any]:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data
