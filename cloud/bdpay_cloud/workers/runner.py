"""The background worker: ``bdpay-cloud worker``.

One loop, four jobs: deliver webhooks, reconcile pending payments, send email,
keep house. Run as many copies as you like; every job claims its work with
``FOR UPDATE SKIP LOCKED``. Stops cleanly on SIGTERM (finishes the current batch).
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..engine.runtime import Runtime
from ..services import mailer
from ..services.webhooks import Dispatcher
from . import housekeeping, reconcile

log = logging.getLogger("bdpay_cloud.worker")
HOUSEKEEPING_EVERY = 60.0


class Worker:
    def __init__(self, rt: Runtime, transport=None):
        self.rt = rt
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(
            max_workers=max(1, rt.settings.worker_concurrency), thread_name_prefix="bdpay-worker"
        )
        self.dispatcher = Dispatcher(rt, transport=transport)
        self._last_housekeeping = 0.0

    def tick(self) -> int:
        """One pass over every queue. Returns how many items were handled."""
        n = self.dispatcher.run_once(limit=50, pool=self.pool)
        n += len(reconcile.run_once(self.rt, limit=25, pool=self.pool))
        n += mailer.send_pending(self.rt, limit=20)
        if time.monotonic() - self._last_housekeeping >= HOUSEKEEPING_EVERY:
            self._last_housekeeping = time.monotonic()
            housekeeping.run(self.rt)
        housekeeping.heartbeat(self.rt)
        return n

    def run_forever(self) -> None:
        def _stop(signum, _frame):
            log.info("signal %s: finishing the current batch, then stopping", signum)
            self.stop.set()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)
        log.info(
            "worker started (concurrency %s, poll %ss)",
            self.rt.settings.worker_concurrency,
            self.rt.settings.worker_poll_seconds,
        )
        while not self.stop.is_set():
            try:
                handled = self.tick()
            except Exception:
                log.exception("worker tick failed; retrying after a pause")
                handled = 0
                self.stop.wait(5)
            if handled == 0:
                self.stop.wait(self.rt.settings.worker_poll_seconds)
        self.close()
        log.info("worker stopped")

    def close(self) -> None:
        self.pool.shutdown(wait=True)
        self.dispatcher.close()
