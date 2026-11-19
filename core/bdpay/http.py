"""HTTP client policy: timeouts, safe retries, TLS always on, redacted logs."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Optional

import httpx

from .core import BdPayError

log = logging.getLogger("bdpay.http")

_SECRET_KEY = re.compile(r"(secret|passw|passwd|token|key|signature|authorization|sensitive|hash)", re.I)
_MSISDN = re.compile(r"\b(01[3-9])(\d{4})(\d{4})\b")


def redact(obj: Any) -> Any:
    """Mask secrets and Bangladeshi mobile numbers before anything is logged."""
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _MSISDN.sub(lambda m: f"{m.group(1)}****{m.group(3)}", obj)
    return obj


class Http:
    def __init__(self, environment: str, transport: Optional[httpx.BaseTransport] = None,
                 connect_timeout: float = 5.0, read_timeout: float = 30.0, retries: int = 3):
        self.environment = environment
        self.retries = retries
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            verify=True,              # never configurable: TLS verification stays on
            follow_redirects=False,
            headers={"User-Agent": "bdpay-python/1.0"},
        )

    def request(self, provider: str, method: str, url: str, *, retry: bool = False,
                **kwargs: Any) -> httpx.Response:
        if self.environment == "live" and not url.startswith("https://"):
            raise BdPayError("CONFIG", f"live mode refuses non-HTTPS URL: {url}", provider)
        attempts = self.retries if retry else 1
        last: Optional[Exception] = None
        for i in range(attempts):
            started = time.monotonic()
            try:
                resp = self.client.request(method, url, **kwargs)
                log.info(json.dumps({
                    "provider": provider, "method": method, "url": url.split("?")[0],
                    "status": resp.status_code, "ms": int((time.monotonic() - started) * 1000),
                }))
                if resp.status_code >= 500 and i < attempts - 1:
                    last = BdPayError("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", provider, retryable=True)
                    self._sleep(i)
                    continue
                return resp
            except httpx.TimeoutException:
                last = BdPayError("TIMEOUT", f"{method} {url.split('?')[0]} timed out", provider, retryable=True)
            except httpx.TransportError as e:
                last = BdPayError("PROVIDER_UNAVAILABLE", f"network error: {e.__class__.__name__}", provider, retryable=True)
            if i < attempts - 1:
                self._sleep(i)
        assert last is not None
        raise last

    @staticmethod
    def _sleep(i: int) -> None:
        time.sleep(min(4.0, 0.25 * (2 ** i)) + random.uniform(0, 0.2))

    def json(self, provider: str, method: str, url: str, **kwargs: Any) -> Any:
        resp = self.request(provider, method, url, **kwargs)
        if resp.status_code >= 500:
            raise BdPayError("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", provider, retryable=True)
        try:
            data = resp.json()
        except ValueError:
            raise BdPayError("PROVIDER_UNAVAILABLE",
                             f"non-JSON response (HTTP {resp.status_code}): {resp.text[:200]!r}", provider)
        log.debug("response %s %s", provider, json.dumps(redact(data))[:2000])
        return data

    def close(self) -> None:
        self.client.close()
