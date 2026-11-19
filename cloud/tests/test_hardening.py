"""Abuse limits and deployment switches added on top of the basic flows."""

import os

import pytest
from sqlalchemy import text

from bdpay_cloud.security.ratelimit import MemoryLimiter, RedisLimiter

import helpers
from helpers import SSLCOMMERZ, Dash, FakeSsl, bearer

REDIS_URL = os.environ.get("BDPAY_CLOUD_TEST_REDIS_URL", "redis://127.0.0.1:63790/15")


def test_memory_limiter_windows():
    limiter = MemoryLimiter()
    hits = [limiter.hit("k", 3, 60) for _ in range(5)]
    assert [h.allowed for h in hits] == [True, True, True, False, False]
    assert hits[0].remaining == 2 and hits[3].remaining == 0 and 1 <= hits[3].retry_after <= 60
    assert limiter.hit("other", 3, 60).allowed  # keys are independent
    limiter.reset()
    assert limiter.hit("k", 3, 60).allowed


def test_redis_limiter_is_shared_and_degrades_without_redis():
    limiter = RedisLimiter(REDIS_URL)
    if not limiter.ping():
        pytest.skip("redis is not running (docker compose -f docker-compose.dev.yml up -d redis)")
    key = "test-" + helpers.rand_b32(10)
    second_replica = RedisLimiter(REDIS_URL)
    results = [(limiter if i % 2 else second_replica).hit(key, 4, 60).allowed for i in range(6)]
    assert results == [True] * 4 + [False] * 2  # one budget across both "replicas"
    # Redis goes away: requests are still limited (per process), not failed and not unlimited.
    down = RedisLimiter("redis://127.0.0.1:1/0")
    assert [down.hit(key, 2, 60).allowed for _ in range(3)] == [True, True, False]
    assert down.ping() is False


def test_ipn_cannot_be_used_to_hammer_a_provider(client, dash, provider):
    calls = []
    fake = FakeSsl()

    def counting(req):
        calls.append(req.url.path)
        return fake(req)

    provider(counting)
    ipn = (
        dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ})
        .json()["urls"]["ipn"]
        .replace("https://pay.test", "")
    )
    key = dash.api_key()
    client.post("/v1/orders", json={"order_id": "HAM1", "amount": "10", "gateway": "sslcommerz"}, headers=bearer(key))
    client.post("/v1/orders/HAM1/pay", headers=bearer(key))
    before = len(calls)
    # No val_id, so the order stays pending and every accepted IPN costs one provider query.
    codes = [client.post(ipn, data={"tran_id": "HAM1"}).status_code for _ in range(20)]
    assert codes[:12] == [200] * 12 and set(codes[12:]) == {429}
    assert len(calls) - before == 12


def test_callback_link_cannot_be_replayed_endlessly(client, dash):
    key = dash.api_key()
    client.post("/v1/orders", json={"order_id": "REP1", "amount": "10", "gateway": "simulator"}, headers=bearer(key))
    url = client.post("/v1/orders/REP1/pay", headers=bearer(key)).json()["redirect_url"].replace("https://pay.test", "")
    r = client.post(url, data={"action": "decline"}, follow_redirects=False)
    link = r.headers["location"].replace("https://pay.test", "")
    codes = [client.get(link, follow_redirects=False).status_code for _ in range(34)]
    assert codes[:30] == [303] * 30 and set(codes[30:]) == {429}


def test_chunked_bodies_are_capped_too(client):
    def chunks():
        for _ in range(40):
            yield b"x" * 10_000  # 400 KB, no Content-Length

    r = client.post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "TOO_LARGE"


def test_gateway_base_urls_can_be_restricted_to_known_hosts():
    from fastapi.testclient import TestClient

    from bdpay_cloud.api.app import create_app

    from conftest import make_settings

    settings = make_settings(base_url_allowlist="upaybd.com, .uddoktapay.com")
    assert settings.base_url_host_allowed("upaybd.com") and settings.base_url_host_allowed("pg.upaybd.com")
    assert not settings.base_url_host_allowed("evil-upaybd.com") and not settings.base_url_host_allowed(
        "upaybd.com.evil.io"
    )
    app = create_app(settings)
    upay = {
        "UPAY_MERCHANT_ID": "M",
        "UPAY_MERCHANT_KEY": "merchant-key-000",
        "UPAY_MERCHANT_CODE": "C",
        "UPAY_MERCHANT_NAME": "Shop",
        "UPAY_MERCHANT_MOBILE": "01800000000",
    }
    with TestClient(app, base_url="https://pay.test") as c:
        d = Dash(c)
        d.signup()
        r = d.put(f"{d.env()}/gateways/upay", {"values": {**upay, "UPAY_BASE_URL": "https://8.8.8.8/upay"}})
        assert r.status_code == 400 and "allowed gateway hosts" in r.json()["detail"]["errors"][0]["message"]
        r = d.put(f"{d.env()}/gateways/upay", {"values": {**upay, "UPAY_BASE_URL": "https://pg.upaybd.com/api"}})
        assert r.status_code == 200
    app.state.rt.close()


def test_api_refunds_are_in_the_audit_log_with_the_key_that_asked(client, dash):
    key = dash.api_key()
    client.post("/v1/orders", json={"order_id": "AUD1", "amount": "100", "gateway": "simulator"}, headers=bearer(key))
    helpers.simulate(client, client.post("/v1/orders/AUD1/pay", headers=bearer(key)).json()["redirect_url"])
    client.post("/v1/orders/AUD1/refund", json={"amount": "10"}, headers=bearer(key))
    entry = next(
        e for e in dash.get(f"/api/workspaces/{dash.slug}/audit-log").json()["data"] if e["action"] == "order.refunded"
    )
    assert entry["actor_type"] == "api_key" and entry["actor"] == "API key test key" and entry["target_id"] == "AUD1"
    assert entry["detail"] == {"amount": "10.00", "ok": True, "ref": entry["detail"]["ref"]}


def test_archiving_a_workspace_stops_its_keys_and_hides_it(client, dash, rt):
    key = dash.api_key()
    assert dash.delete(f"/api/workspaces/{dash.slug}").status_code == 200
    assert client.get("/v1/gateways", headers=bearer(key)).status_code == 403
    assert dash.get("/api/auth/me").json()["workspaces"] == []
    assert dash.get(f"/api/workspaces/{dash.slug}").status_code == 404
    with rt.engine.connect() as conn:  # archived, not erased
        assert (
            conn.execute(text("SELECT count(*) FROM workspaces WHERE id = :w"), {"w": dash.workspace_id}).scalar() == 1
        )
