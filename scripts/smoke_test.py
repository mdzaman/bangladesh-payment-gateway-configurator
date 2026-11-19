#!/usr/bin/env python3
"""End-to-end smoke test of a running bdpay cloud deployment.

    python scripts/smoke_test.py http://localhost:8080

Signs up a throwaway account, then drives a sandbox payment through the
built-in simulator the way a merchant's server and a customer's browser would:
API key, order, payment page, signed callback, verification, refund, hosted
checkout, events. Touches sandbox data only and needs the simulator switched
on (BDPAY_CLOUD_SIMULATOR=true) and sign-up open.
"""

from __future__ import annotations

import secrets
import sys
from urllib.parse import urlparse

import httpx


def main(base: str) -> int:
    base = base.rstrip("/")
    origin = urlparse(base)
    steps: list[str] = []

    def ok(text: str) -> None:
        steps.append(text)
        print(f"  ✓ {text}")

    def local(url: str) -> str:
        """The service builds absolute URLs from its public URL; follow them at the address we were given."""
        u = urlparse(url)
        return base + u.path + (f"?{u.query}" if u.query else "")

    browser = httpx.Client(base_url=base, timeout=30, follow_redirects=False)
    server = httpx.Client(base_url=base, timeout=40)

    ready = browser.get("/readyz")
    assert ready.status_code == 200 and ready.json()["ok"], f"not ready: {ready.text}"
    ok(f"ready ({', '.join(k for k, v in ready.json()['checks'].items() if v)})")
    home = browser.get("/")
    assert home.status_code == 200 and '<div id="root">' in home.text, "dashboard is not being served"
    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    ok("dashboard served with security headers")

    email = f"smoke-{secrets.token_hex(5)}@example.com"
    me = browser.post(
        "/api/auth/signup",
        json={
            "email": email,
            "password": secrets.token_urlsafe(18),
            "name": "Smoke Test",
            "workspace_name": f"Smoke {secrets.token_hex(3)}",
        },
    )
    assert me.status_code == 200, me.text
    slug, csrf = me.json()["workspaces"][0]["slug"], me.json()["csrf_token"]
    env = f"/api/workspaces/{slug}/sandbox"
    dash = {"X-CSRF-Token": csrf, "Origin": f"{origin.scheme}://{origin.netloc}"}
    ok(f"signed up, workspace '{slug}'")

    gateways = browser.get(f"{env}/gateways").json()["gateways"]
    assert any(g["id"] == "simulator" and g["enabled"] for g in gateways), "the simulator is not enabled"
    key = browser.post(f"{env}/api-keys", json={"name": "smoke"}, headers=dash).json()["key"]
    auth = {"Authorization": f"Bearer {key}"}
    ok("simulator enabled, API key created")

    order_id = "SMOKE" + secrets.token_hex(4).upper()
    r = server.post(
        "/v1/orders",
        json={
            "order_id": order_id,
            "amount": "1250.50",
            "gateway": "simulator",
            "customer": {"name": "Rahim", "phone": "01712345678"},
        },
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["status"] == "created", r.text
    pay_url = server.post(f"/v1/orders/{order_id}/pay", headers=auth).json()["redirect_url"]
    assert server.post(f"/v1/orders/{order_id}/pay", headers=auth).json()["redirect_url"] == pay_url
    ok(f"order {order_id} created; asking to pay twice returns the same page")

    page = browser.get(local(pay_url))
    assert page.status_code == 200 and "Simulated provider" in page.text
    back = browser.post(local(pay_url), data={"action": "approve"})
    assert back.status_code == 303, back.text
    callback = local(back.headers["location"])
    path, _, query = callback.partition("?")
    parts = path.split("/")
    parts[-2] = parts[-2][:-1] + ("0" if parts[-2][-1] != "0" else "1")  # one character of the signature
    forged = "/".join(parts) + "?" + query
    assert browser.get(forged).status_code == 403, "a callback link with a wrong signature was accepted"
    assert server.get(f"/v1/orders/{order_id}", headers=auth).json()["status"] == "pending"
    result = browser.get(callback)
    assert result.status_code == 303, result.text
    got = server.get(f"/v1/orders/{order_id}", headers=auth).json()
    assert got["status"] == "paid" and got["amount"] == "1250.50", got
    assert "01712345678" not in str(got["events"]), "a phone number reached the audit trail unmasked"
    ok("forged callback refused; the real one was verified and the order is paid")

    refund = server.post(
        f"/v1/orders/{order_id}/refund",
        json={"amount": "250.50"},
        headers={**auth, "Idempotency-Key": f"refund-{order_id}"},
    )
    again = server.post(
        f"/v1/orders/{order_id}/refund",
        json={"amount": "250.50"},
        headers={**auth, "Idempotency-Key": f"refund-{order_id}"},
    )
    assert refund.json()["ok"] and again.headers.get("idempotent-replayed") == "true"
    assert server.get(f"/v1/orders/{order_id}", headers=auth).json()["refunded"] == "250.50"
    ok("partial refund applied once, even though it was sent twice")

    tamper = "SMOKE" + secrets.token_hex(4).upper()
    server.post("/v1/orders", json={"order_id": tamper, "amount": "500", "gateway": "simulator"}, headers=auth)
    url = local(server.post(f"/v1/orders/{tamper}/pay", headers=auth).json()["redirect_url"])
    browser.get(local(browser.post(url, data={"action": "wrong_amount"}).headers["location"]))
    assert server.get(f"/v1/orders/{tamper}", headers=auth).json()["status"] == "needs_review"
    ok("a payment of the wrong amount went to needs_review, not paid")

    session = server.post(
        "/v1/checkout/sessions", json={"amount": "990", "reference": "INV-SMOKE"}, headers=auth
    ).json()
    checkout = browser.get(local(session["url"]))
    assert checkout.status_code == 200 and "Choose how to pay" in checkout.text and "<script" not in checkout.text
    assert "বেছে নিন" in browser.get(local(session["url"]) + "?lang=bn").text
    go = browser.post(urlparse(session["url"]).path + "/pay", data={"gateway": "simulator"})
    assert go.status_code == 303, go.text
    done = browser.post(local(go.headers["location"]), data={"action": "approve"})
    browser.get(local(done.headers["location"]))
    assert server.get(f"/v1/checkout/sessions/{session['id']}", headers=auth).json()["status"] == "paid"
    ok("hosted checkout page (English and Bangla) took a payment")

    types = [e["type"] for e in server.get("/v1/events", headers=auth).json()["data"]]
    for expected in ("order.paid", "order.partially_refunded", "order.needs_review", "checkout.session.completed"):
        assert expected in types, f"missing event {expected}: {types}"
    ok(f"{len(types)} events recorded for webhooks")

    assert server.get(f"/v1/orders/{order_id}").status_code == 401
    assert (
        server.get(f"/v1/orders/{order_id}", headers={"Authorization": "Bearer bdp_test_" + "x" * 40}).status_code
        == 401
    )
    overview = browser.get(f"{env}/overview").json()
    assert overview["stats"]["paid_count"] == 2 and overview["stats"]["needs_review_now"] == 1
    ok("API refuses missing and wrong keys; dashboard figures add up")

    running = browser.get(f"{env}/worker").json()["running"]
    print(f"  {'✓' if running else '!'} background worker {'is running' if running else 'is NOT running'}")
    print(f"\nAll {len(steps)} checks passed against {base}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1]))
    except AssertionError as e:
        print(f"  ✗ {e}")
        sys.exit(1)
