"""Builds a gateway driver for one tenant from its stored configuration.

Drivers are the reference ones, unmodified. This module only decides what
settings mapping each driver receives:

* values come from the workspace's own gateway configuration (secrets are
  decrypted here, in memory, for the life of one request);
* Nagad key material comes from those settings instead of files on disk;
* the experimental UddoktaPay driver needs both the workspace's tick box and
  the platform switch;
* the simulator can only ever be built in sandbox mode.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from bdpay.config import GatewayConfig
from bdpay.core import BdPayError, Driver
from bdpay.drivers.nagad import BASE as NAGAD_BASE
from bdpay.drivers.nagad import NagadDriver, load_private_key, load_public_key
from bdpay.registry import DRIVERS, load_driver_class

from ..context import Scope
from .simulator import SimulatorDriver

if TYPE_CHECKING:
    from .runtime import Runtime


class NagadInlineDriver(NagadDriver):
    """The reference Nagad driver with one difference: the merchant private key
    and the Nagad public key are read from settings (``NAGAD_PRIVATE_KEY``,
    ``NAGAD_PUBLIC_KEY``) rather than from ``*_KEY_PATH`` files, because a
    hosted service keeps tenant keys encrypted in its database, not on disk."""

    required_env = ("NAGAD_MERCHANT_ID", "NAGAD_PRIVATE_KEY", "NAGAD_PUBLIC_KEY")

    def __init__(self, cfg, http, tokens):
        Driver.__init__(self, cfg, http, tokens)
        self.base = (cfg.get("NAGAD_BASE_URL") or NAGAD_BASE[self.env]).rstrip("/")
        self.mid = cfg["NAGAD_MERCHANT_ID"]
        self.merchant_number = cfg.get("NAGAD_MERCHANT_NUMBER")
        try:
            self.private_key = load_private_key(cfg["NAGAD_PRIVATE_KEY"])
            self.nagad_public = load_public_key(cfg["NAGAD_PUBLIC_KEY"])
        except (ValueError, TypeError) as e:
            raise self.fail("CONFIG", f"cannot load Nagad keys: {e}") from None
        self.client_ip = cfg.get("NAGAD_CLIENT_IP", "127.0.0.1")


KNOWN_GATEWAYS = (*DRIVERS, "simulator")


def driver_class(gateway: str, values: Mapping[str, str] | None = None) -> type[Driver]:
    if gateway == "simulator":
        return SimulatorDriver
    if gateway == "nagad" and not (values or {}).get("NAGAD_PRIVATE_KEY_PATH"):
        return NagadInlineDriver
    return load_driver_class(gateway)


def credential_namespace(mode: str, gateway: str, values: Mapping[str, str]) -> str:
    """Hash of the complete credential set. Used to namespace cached provider
    tokens: identical credentials share a token (so a shared sandbox account is
    not asked for a new token by every workspace), anything less does not."""
    material = json.dumps([mode, gateway, sorted((k, str(v)) for k, v in values.items())])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_driver(rt: Runtime, scope: Scope, gateway: str, values: Mapping[str, str]) -> Driver:
    values = {k: str(v) for k, v in values.items() if v not in (None, "")}
    if gateway == "simulator":
        if scope.mode != "sandbox" or not rt.settings.simulator:
            raise BdPayError("CONFIG", "the simulator is not available here", "simulator")
        return SimulatorDriver(
            GatewayConfig(gateway, scope.mode, values),
            rt.http(scope.mode),
            rt.tokens,
            engine=rt.engine,
            scope=scope,
            public_url=rt.settings.public_url,
        )
    if gateway == "uddoktapay" and not rt.settings.allow_experimental:
        raise BdPayError("CONFIG", "experimental gateways are switched off on this deployment", gateway)
    if gateway == "nagad" and not values.get("NAGAD_CLIENT_IP") and rt.settings.egress_ip_list:
        values["NAGAD_CLIENT_IP"] = rt.settings.egress_ip_list[0]
    cls = driver_class(gateway, values)
    tokens = rt.tokens.scoped(credential_namespace(scope.mode, gateway, values))
    return cls(GatewayConfig(gateway, scope.mode, values), rt.http(scope.mode), tokens)
