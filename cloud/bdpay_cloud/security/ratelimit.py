"""Fixed-window rate limiting.

With ``BDPAY_CLOUD_REDIS_URL`` set, counters live in Redis and a limit holds
across all replicas. Without it, each process counts for itself, which is
correct for a single node and approximate for several. If Redis becomes
unreachable the in-process counter takes over instead of failing requests.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import NamedTuple

log = logging.getLogger("bdpay_cloud.ratelimit")


class Hit(NamedTuple):
    allowed: bool
    remaining: int
    retry_after: int  # seconds until the window resets


class MemoryLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key -> (window start, count)
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: int) -> Hit:
        now = int(time.time())
        start = now - now % window
        with self._lock:
            w_start, count = self._windows.get(key, (start, 0))
            if w_start != start:
                w_start, count = start, 0
            count += 1
            self._windows[key] = (w_start, count)
            if time.monotonic() - self._last_sweep > 60:
                self._sweep(now)
        return Hit(count <= limit, max(0, limit - count), max(1, start + window - now))

    def _sweep(self, now: int) -> None:
        self._last_sweep = time.monotonic()
        if len(self._windows) > 10000:
            cutoff = now - 3600
            self._windows = {k: v for k, v in self._windows.items() if v[0] >= cutoff}

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {c, ttl}
"""


class RedisLimiter:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)
        self._script = self._client.register_script(_LUA)
        self._fallback = MemoryLimiter()
        self._warned_at = 0.0

    def hit(self, key: str, limit: int, window: int) -> Hit:
        now = int(time.time())
        try:
            count, ttl = self._script(keys=[f"bdpay:rl:{key}:{now - now % window}"], args=[window])
            return Hit(int(count) <= limit, max(0, limit - int(count)), max(1, int(ttl)))
        except Exception as e:  # Redis down: degrade to per-process limits, do not fail the request
            if time.monotonic() - self._warned_at > 30:
                self._warned_at = time.monotonic()
                log.warning("redis rate limiter unavailable, using in-process limits: %s", e.__class__.__name__)
            return self._fallback.hit(key, limit, window)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def reset(self) -> None:
        self._fallback.reset()


def make_limiter(redis_url: str) -> MemoryLimiter | RedisLimiter:
    return RedisLimiter(redis_url) if redis_url else MemoryLimiter()
