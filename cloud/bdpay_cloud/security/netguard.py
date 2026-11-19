"""Outbound network guard (SSRF protection).

Tenants supply URLs this service will call: webhook endpoints, and base-URL
overrides for gateways whose host comes from an onboarding pack (Upay,
UddoktaPay, Nagad). Without a guard, a tenant could point one at
``http://169.254.169.254/`` or an internal service and read the answer back.

Two layers:

1. :func:`check_public_url` rejects obviously bad URLs when they are saved, with
   a message a person can act on.
2. :class:`GuardedTransport` is the enforcement. It resolves the host at connect
   time, refuses if *any* resolved address is not publicly routable, and then
   connects to the address it just checked. Resolving and connecting in one
   step is what defeats DNS rebinding.

TLS still verifies the certificate against the hostname: only the TCP
connection is pinned to the vetted address.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpcore
import httpx

log = logging.getLogger("bdpay_cloud.netguard")

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class BlockedAddress(Exception):
    pass


def is_public_ip(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        addr = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        # Addresses that smuggle an IPv4 address inside an IPv6 one.
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr.sixtofour is not None:
            addr = addr.sixtofour
        elif addr.teredo is not None:
            return False
        elif addr in _NAT64:
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return bool(addr.is_global) and not addr.is_multicast


def resolve_checked(host: str, port: int, allow_private: bool = False) -> list[str]:
    """Resolve ``host`` and return its addresses, IPv4 first. Raises
    :class:`BlockedAddress` if any address is not public (unless allowed) and
    ``OSError`` if the name does not resolve."""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addrs = [host]
    except ValueError:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addrs:
        raise OSError(f"{host} has no addresses")
    if not allow_private:
        bad = [a for a in addrs if not is_public_ip(a)]
        if bad:
            raise BlockedAddress(f"{host} resolves to a private, local or reserved address")
    # Nagad and Upay whitelist IPv4 callers, so prefer IPv4 when both exist.
    return sorted(addrs, key=lambda a: ":" in a)


def check_public_url(url: str, *, allow_http: bool, allow_private: bool = False) -> str:
    """Validate a tenant-supplied URL when it is saved. Returns the cleaned URL
    or raises ``ValueError`` with a message for the person who typed it."""
    url = (url or "").strip()
    if len(url) > 2000:
        raise ValueError("is too long")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("must start with https://")
    if p.scheme == "http" and not allow_http:
        raise ValueError("must use https://")
    if not p.hostname:
        raise ValueError("has no host name")
    if p.username or p.password:
        raise ValueError("must not contain a username or password")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise ValueError("has an invalid port") from None
    if not allow_private:
        try:
            resolve_checked(p.hostname, port, allow_private=False)
        except BlockedAddress:
            raise ValueError("points to a private, local or reserved address") from None
        except OSError:
            pass  # does not resolve right now: the connect-time guard still applies
    return url


class GuardedBackend(httpcore.NetworkBackend):
    def __init__(self, allow_private: bool = False):
        self._inner = httpcore.SyncBackend()
        self.allow_private = allow_private

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addrs = resolve_checked(host, port, self.allow_private)
        except BlockedAddress as e:
            log.warning("blocked outbound connection: %s", e)
            raise httpcore.ConnectError(str(e)) from None
        except OSError as e:
            raise httpcore.ConnectError(f"cannot resolve {host}: {e}") from None
        last: Exception | None = None
        for ip in addrs[:4]:
            try:
                return self._inner.connect_tcp(
                    ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


DEFAULT_LIMITS = httpx.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30.0)


class GuardedTransport(httpx.HTTPTransport):
    """httpx transport whose connections go through :class:`GuardedBackend`.
    TLS verification is always on; proxies from the environment are not used."""

    def __init__(self, *, allow_private: bool = False, limits: httpx.Limits = DEFAULT_LIMITS):
        super().__init__(verify=True, limits=limits)
        self._pool.close()
        self.backend = GuardedBackend(allow_private)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            retries=0,
            network_backend=self.backend,
        )
