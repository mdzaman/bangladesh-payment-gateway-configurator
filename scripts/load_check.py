#!/usr/bin/env python3
"""A quick concurrency check of the write path (not a benchmark).

    python scripts/load_check.py http://localhost:8080 [orders] [concurrency]

Creates sandbox orders and starts each payment through the simulator from many
threads, then reads every order back. Reports throughput, latency percentiles
and whether anything was lost or duplicated.
"""

from __future__ import annotations

import secrets
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def main(base: str, total: int, concurrency: int) -> int:
    base = base.rstrip("/")
    browser = httpx.Client(base_url=base, timeout=30)
    me = browser.post(
        "/api/auth/signup",
        json={
            "email": f"load-{secrets.token_hex(5)}@example.com",
            "password": secrets.token_urlsafe(18),
            "name": "Load Check",
        },
    ).json()
    slug, csrf = me["workspaces"][0]["slug"], me["csrf_token"]
    # The default limit is 600 requests a minute per key, so spread the load over several keys.
    keys = [
        browser.post(
            f"/api/workspaces/{slug}/sandbox/api-keys", json={"name": f"load {i}"}, headers={"X-CSRF-Token": csrf}
        ).json()["key"]
        for i in range(16)
    ]
    run = secrets.token_hex(3).upper()
    clients = [
        httpx.Client(
            base_url=base,
            timeout=60,
            headers={"Authorization": f"Bearer {k}"},
            limits=httpx.Limits(max_connections=concurrency),
        )
        for k in keys
    ]
    latencies: list[float] = []
    errors: list[str] = []

    def one(i: int) -> None:
        client = clients[i % len(clients)]
        order_id = f"LOAD{run}{i:05d}"
        for method, path, body in (
            ("POST", "/v1/orders", {"order_id": order_id, "amount": "100", "gateway": "simulator"}),
            ("POST", f"/v1/orders/{order_id}/pay", None),
        ):
            started = time.perf_counter()
            r = client.request(method, path, json=body)
            latencies.append((time.perf_counter() - started) * 1000)
            if r.status_code != 200:
                errors.append(f"{r.status_code} {path}")

    started = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(one, range(total)))
    elapsed = time.perf_counter() - started

    listed = 0
    cursor = None
    while True:
        page = (
            clients[0]
            .get(
                "/v1/orders",
                params={"limit": 100, "status": "pending", **({"starting_after": cursor} if cursor else {})},
            )
            .json()
        )
        listed += len(page["data"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    latencies.sort()
    pct = lambda p: latencies[min(len(latencies) - 1, int(len(latencies) * p))]  # noqa: E731
    print(f"{total} orders created and started with {concurrency} concurrent clients in {elapsed:.1f} s")
    print(f"  {len(latencies) / elapsed:.0f} requests/s   ({total / elapsed:.0f} payments started per second)")
    median = statistics.median(latencies)
    print(f"  latency ms: median {median:.0f}, p95 {pct(0.95):.0f}, p99 {pct(0.99):.0f}, max {latencies[-1]:.0f}")
    print(f"  errors: {len(errors)}{'  ' + str(sorted(set(errors))[:5]) if errors else ''}")
    print(f"  orders pending afterwards: {listed} (expected {total})")
    return 0 if not errors and listed == total else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(
        main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 400, int(sys.argv[3]) if len(sys.argv) > 3 else 32)
    )
