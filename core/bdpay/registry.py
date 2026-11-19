"""Gateway registry. Drivers are imported lazily, so a project only needs the
driver files for the gateways it enables in BDPAY_GATEWAYS."""
from __future__ import annotations

import importlib

from .core import BdPayError

DRIVERS = {
    "bkash": "bdpay.drivers.bkash:BkashDriver",
    "nagad": "bdpay.drivers.nagad:NagadDriver",
    "sslcommerz": "bdpay.drivers.sslcommerz:SslcommerzDriver",
    "aamarpay": "bdpay.drivers.aamarpay:AamarpayDriver",
    "shurjopay": "bdpay.drivers.shurjopay:ShurjopayDriver",
    "eps": "bdpay.drivers.eps:EpsDriver",
    "portpos": "bdpay.drivers.portpos:PortposDriver",
    "paystation": "bdpay.drivers.paystation:PaystationDriver",
    "upay": "bdpay.drivers.upay:UpayDriver",
    "uddoktapay": "bdpay.drivers.uddoktapay:UddoktapayDriver",
}


def load_driver_class(name: str):
    target = DRIVERS.get(name)
    if not target:
        raise BdPayError("CONFIG", f"unknown gateway '{name}'. Known: {', '.join(sorted(DRIVERS))}")
    module, _, cls = target.partition(":")
    try:
        return getattr(importlib.import_module(module), cls)
    except ModuleNotFoundError as e:
        raise BdPayError("CONFIG", f"driver file for '{name}' is missing ({module}): {e}")
