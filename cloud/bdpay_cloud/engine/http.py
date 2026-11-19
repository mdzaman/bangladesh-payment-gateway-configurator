"""Outbound HTTP for provider calls.

Keeps every rule of the reference client (connect 5 s / read 30 s, retries only
on reads, TLS verification always on, live mode refuses ``http://``, redacted
logs) and adds what a shared deployment needs: connections only to public
addresses (see ``security.netguard``) and a connection pool sized for many
tenants talking to the same few providers.
"""

from __future__ import annotations

import httpx

from bdpay.http import Http

from ..security.netguard import GuardedTransport

USER_AGENT = "bdpay-cloud/1.0 (bdpay-python/1.0)"


class CloudHttp(Http):
    def __init__(self, environment: str, *, transport: httpx.BaseTransport | None = None, allow_private: bool = False):
        super().__init__(environment, transport=transport)
        if transport is None:
            self.client.close()
            self.client = httpx.Client(
                transport=GuardedTransport(allow_private=allow_private),
                timeout=httpx.Timeout(30.0, connect=5.0),
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
