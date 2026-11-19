"""Prometheus metrics. One registry per process."""

from __future__ import annotations

import json
import logging

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter("bdpay_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_SECONDS = Histogram(
    "bdpay_http_request_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)
PROVIDER_REQUESTS = Counter(
    "bdpay_provider_requests_total", "Calls to payment providers", ["provider", "status"], registry=REGISTRY
)
PROVIDER_SECONDS = Histogram(
    "bdpay_provider_request_seconds",
    "Provider call duration",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    registry=REGISTRY,
)
WEBHOOK_DELIVERIES = Counter(
    "bdpay_webhook_deliveries_total", "Webhook delivery attempts", ["result"], registry=REGISTRY
)
RECONCILE_CHECKS = Counter("bdpay_reconcile_checks_total", "Pending orders re-checked", ["result"], registry=REGISTRY)
WORKER_BACKLOG = Gauge("bdpay_worker_backlog", "Due items waiting for a worker", ["queue"], registry=REGISTRY)
RATE_LIMITED = Counter("bdpay_rate_limited_total", "Requests refused by a rate limit", ["bucket"], registry=REGISTRY)


class _ProviderLogTap(logging.Handler):
    """The reference HTTP client logs one JSON line per provider call
    (provider, status, ms). Counting those lines gives provider metrics without
    touching the reference code."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = json.loads(record.getMessage())
            PROVIDER_REQUESTS.labels(data["provider"], str(data["status"])).inc()
            PROVIDER_SECONDS.labels(data["provider"]).observe(data["ms"] / 1000)
        except Exception:  # noqa: S110 - a log line that is not a provider call
            pass


_tap = _ProviderLogTap(level=logging.INFO)


def install_provider_tap() -> None:
    logger = logging.getLogger("bdpay.http")
    if _tap not in logger.handlers:
        logger.addHandler(_tap)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)


def render() -> bytes:
    return generate_latest(REGISTRY)
