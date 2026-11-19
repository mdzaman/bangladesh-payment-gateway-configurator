"""The merchant API: keys, orders, idempotency, compatibility with self-hosted bdpay."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from helpers import BKASH, Dash, FakeBkash, bearer, simulate


@pytest.fixture
def key(dash):
    return dash.api_key()


def order(client, key, order_id="INV1001", amount="1250.00", gateway="simulator", **extra):
    return client.post(
        "/v1/orders", json={"order_id": order_id, "amount": amount, "gateway": gateway, **extra}, headers=bearer(key)
    )


def test_keys_are_shown_once_hashed_at_rest_and_scoped_to_one_environment(dash, rt, client):
    key = dash.api_key()
    assert key.startswith("bdp_test_") and len(key) == 49
    listing = dash.get(f"{dash.env()}/api-keys").json()["data"]
    assert listing[0]["display"] == f"{key[:13]}…{key[-4:]}" and key not in str(listing)
    with rt.engine.connect() as conn:
        stored = conn.execute(
            text("SELECT key_hash FROM api_keys WHERE workspace_id = :w"), {"w": dash.workspace_id}
        ).scalar()
    assert key not in stored and len(stored) == 64
    assert client.get("/v1/gateways", headers=bearer(key)).json() == [
        {"id": "simulator", "label": "Simulator", "stability": "stable", "refunds": "partial"}
    ]


def test_authentication_failures(client, dash, key):
    for headers in (
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Bearer bdp_test_" + "x" * 40},
        {"Authorization": "Basic abc"},
    ):
        r = client.get("/v1/gateways", headers=headers)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"] == "Bearer"
    key_id = dash.get(f"{dash.env()}/api-keys").json()["data"][0]["id"]
    assert dash.delete(f"{dash.env()}/api-keys/{key_id}").status_code == 200
    assert client.get("/v1/gateways", headers=bearer(key)).status_code == 401  # revoked takes effect at once


def test_live_keys_need_an_active_live_mode(dash, client, rt):
    assert dash.post(f"{dash.env('live')}/api-keys", {"name": "too early"}).status_code == 403
    dash.go_live()
    live = dash.api_key("live")
    assert live.startswith("bdp_live_")
    assert client.get("/v1/gateways", headers=bearer(live)).json() == []
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE workspaces SET suspended_at = now() WHERE id = :w"), {"w": dash.workspace_id})
    r = client.get("/v1/gateways", headers=bearer(live))
    assert r.status_code == 403 and "suspended" in r.json()["detail"]["message"]


def test_order_lifecycle_through_the_simulator(client, dash, key):
    r = order(client, key, customer={"name": "Rahim", "phone": "01712345678"}, metadata={"cart": "42"})
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["status"] == "created" and created["amount"] == "1250.00" and created["amount_poisha"] == 125000
    assert created["mode"] == "sandbox" and created["metadata"] == {"cart": "42"} and created["refunded"] == "0.00"
    url = client.post("/v1/orders/INV1001/pay", headers=bearer(key)).json()["redirect_url"]
    assert client.get("/v1/orders/INV1001", headers=bearer(key)).json()["status"] == "pending"
    back = simulate(client, url)
    assert back.status_code == 303 and "/result/INV1001/" in back.headers["location"]
    got = client.get("/v1/orders/INV1001", headers=bearer(key)).json()
    assert got["status"] == "paid" and got["provider_txn_id"].startswith("SIMTX") and got["paid_at"]
    assert [e["kind"] for e in got["events"]] == ["created", "transition", "transition"]
    assert "01712345678" not in str(got["events"])
    refund = client.post(
        "/v1/orders/INV1001/refund", json={"amount": "250", "reason": "returned item"}, headers=bearer(key)
    ).json()
    assert (
        refund["ok"] and refund["order"]["status"] == "partially_refunded" and refund["order"]["refunded"] == "250.00"
    )
    r = client.post("/v1/orders/INV1001/refund", json={"amount": "5000"}, headers=bearer(key))
    assert r.status_code == 400 and "refund must be between" in r.json()["detail"]["message"]
    full = client.post("/v1/orders/INV1001/refund", json={}, headers=bearer(key)).json()
    assert full["order"]["status"] == "refunded" and full["order"]["refunded"] == "1250.00"


def test_create_is_idempotent_by_order_id_and_conflicts_on_different_details(client, key):
    assert order(client, key, "A1", "10").status_code == 200
    assert order(client, key, "A1", "10.00").json()["order_id"] == "A1"  # same order again: fine
    r = order(client, key, "A1", "11")
    assert r.status_code == 409 and r.json()["detail"] == {
        **r.json()["detail"],
        "code": "DUPLICATE",
        "provider": "core",
    }


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"order_id": "bad id!", "amount": "10", "gateway": "simulator"}, "order_id"),
        ({"order_id": "x" * 41, "amount": "10", "gateway": "simulator"}, "order_id"),
        ({"order_id": "অর্ডার১", "amount": "10", "gateway": "simulator"}, "order_id"),
        ({"order_id": "B1", "amount": "0", "gateway": "simulator"}, "greater than zero"),
        ({"order_id": "B1", "amount": "-5", "gateway": "simulator"}, "greater than zero"),
        ({"order_id": "B1", "amount": "abc", "gateway": "simulator"}, "invalid amount"),
        ({"order_id": "B1", "amount": "NaN", "gateway": "simulator"}, "invalid amount"),
        ({"order_id": "B1", "amount": "99999999999", "gateway": "simulator"}, "larger than"),
        ({"order_id": "B1", "amount": "10", "gateway": "bkash"}, "not enabled"),
        ({"order_id": "B1", "amount": "10", "gateway": "stripe"}, "not"),
        (
            {"order_id": "B1", "amount": "10", "gateway": "simulator", "customer": {"phon": "017"}},
            "unknown customer field",
        ),
        ({"order_id": "B1", "amount": "10", "gateway": "simulator", "metadata": {"a": {"b": 1}}}, "metadata"),
        ({"order_id": "B1", "amount": "10", "gateway": "simulator", "return_url": "javascript:alert(1)"}, "return_url"),
        ({"order_id": "B1", "amount": "10", "gateway": "simulator", "surprise": 1}, "surprise"),
        ({"order_id": "B1", "gateway": "simulator"}, "amount"),
    ],
)
def test_validation_errors_use_the_reference_error_shape(client, key, body, fragment):
    r = client.post("/v1/orders", json=body, headers=bearer(key))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert set(detail) >= {"code", "provider", "message"} and fragment in detail["message"], detail
    assert detail["request_id"] == r.headers["x-request-id"]


def test_amounts_can_be_numbers_and_are_stored_as_exact_poisha(client, key):
    assert order(client, key, "N1", 1250).json()["amount"] == "1250.00"
    assert order(client, key, "N2", 99.5).json()["amount_poisha"] == 9950
    assert order(client, key, "N3", "1,250.50").json()["amount"] == "1250.50"


def test_pay_returns_the_same_url_and_missing_orders_are_404(client, key):
    order(client, key, "P1", "10")
    first = client.post("/v1/orders/P1/pay", headers=bearer(key)).json()["redirect_url"]
    assert client.post("/v1/orders/P1/pay", headers=bearer(key)).json()["redirect_url"] == first
    for method, path in (
        ("GET", "/v1/orders/NOPE"),
        ("POST", "/v1/orders/NOPE/pay"),
        ("POST", "/v1/orders/NOPE/requery"),
    ):
        r = client.request(method, path, headers=bearer(key))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "NOT_FOUND"
    simulate(client, first, "decline")
    r = client.post("/v1/orders/P1/pay", headers=bearer(key))
    assert r.status_code == 400 and "order is failed" in r.json()["detail"]["message"]


def test_listing_filters_search_and_pagination(client, key):
    for i in range(7):
        order(client, key, f"L{i:03d}", "10", customer={"phone": f"0171000000{i}"})
    url = client.post("/v1/orders/L003/pay", headers=bearer(key)).json()["redirect_url"]
    simulate(client, url)
    page = client.get("/v1/orders?limit=3", headers=bearer(key)).json()
    assert [o["order_id"] for o in page["data"]] == ["L006", "L005", "L004"] and page["has_more"]
    page2 = client.get(f"/v1/orders?limit=3&starting_after={page['next_cursor']}", headers=bearer(key)).json()
    assert [o["order_id"] for o in page2["data"]] == ["L003", "L002", "L001"]
    assert [o["order_id"] for o in client.get("/v1/orders?status=paid", headers=bearer(key)).json()["data"]] == ["L003"]
    assert len(client.get("/v1/orders?status=paid,created", headers=bearer(key)).json()["data"]) == 7
    assert client.get("/v1/orders?status=bogus", headers=bearer(key)).json()["data"] == []
    assert [o["order_id"] for o in client.get("/v1/orders?q=L00", headers=bearer(key)).json()["data"]][0] == "L006"
    assert [o["order_id"] for o in client.get("/v1/orders?q=01710000002", headers=bearer(key)).json()["data"]] == [
        "L002"
    ]
    assert client.get("/v1/orders?q=%25", headers=bearer(key)).json()["data"] == []  # % is a literal, not a wildcard
    assert client.get("/v1/orders?gateway=bkash", headers=bearer(key)).json()["data"] == []


def test_idempotency_key_replays_instead_of_acting_twice(client, key):
    order(client, key, "IDEM1", "1000")
    simulate(client, client.post("/v1/orders/IDEM1/pay", headers=bearer(key)).json()["redirect_url"])
    headers = {**bearer(key), "Idempotency-Key": "refund-IDEM1-a"}
    first = client.post("/v1/orders/IDEM1/refund", json={"amount": "300"}, headers=headers)
    again = client.post("/v1/orders/IDEM1/refund", json={"amount": "300"}, headers=headers)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert again.headers["idempotent-replayed"] == "true" and "idempotent-replayed" not in first.headers
    assert client.get("/v1/orders/IDEM1", headers=bearer(key)).json()["refunded"] == "300.00"  # refunded once
    # Same key, different request: refuse rather than guess.
    r = client.post("/v1/orders/IDEM1/refund", json={"amount": "301"}, headers=headers)
    assert r.status_code == 422
    # A client error is remembered too.
    bad = {**bearer(key), "Idempotency-Key": "refund-IDEM1-b"}
    assert client.post("/v1/orders/IDEM1/refund", json={"amount": "99999"}, headers=bad).status_code == 400
    r = client.post("/v1/orders/IDEM1/refund", json={"amount": "99999"}, headers=bad)
    assert r.status_code == 400 and r.headers["idempotent-replayed"] == "true"


def test_a_refund_with_unknown_outcome_is_never_rerun_by_the_same_key(client, dash, key, provider):
    fake = provider(FakeBkash(execute="refund_timeout"))
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    order(client, key, "UNK1", "500", "bkash")
    client.post("/v1/orders/UNK1/pay", headers=bearer(key))
    assert client.get(fake.callback_for("UNK1"), follow_redirects=False).status_code == 303
    headers = {**bearer(key), "Idempotency-Key": "refund-UNK1"}
    first = client.post("/v1/orders/UNK1/refund", json={"amount": "100"}, headers=headers)
    assert first.status_code == 504 and first.json()["detail"]["code"] == "TIMEOUT"
    fake.execute = "ok"  # the provider recovers...
    again = client.post("/v1/orders/UNK1/refund", json={"amount": "100"}, headers=headers)
    assert again.status_code == 504 and again.headers["idempotent-replayed"] == "true"
    assert len(fake.refunds) == 1  # ...but the refund is not sent twice
    kinds = [e["kind"] for e in client.get("/v1/orders/UNK1", headers=bearer(key)).json()["events"]]
    assert "refund_unconfirmed" in kinds


def test_concurrent_requests_with_one_idempotency_key_run_once(client, dash, key, app):
    order(client, key, "CONC1", "1000")
    simulate(client, client.post("/v1/orders/CONC1/pay", headers=bearer(key)).json()["redirect_url"])
    barrier = threading.Barrier(5)
    headers = {**bearer(key), "Idempotency-Key": "refund-CONC1"}

    def go(_):
        from fastapi.testclient import TestClient

        with TestClient(app, base_url="https://pay.test") as c:
            barrier.wait()
            return c.post("/v1/orders/CONC1/refund", json={"amount": "100"}, headers=headers).status_code

    with ThreadPoolExecutor(5) as pool:
        codes = list(pool.map(go, range(5)))
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}
    assert client.get("/v1/orders/CONC1", headers=bearer(key)).json()["refunded"] == "100.00"


def test_restricted_keys(dash, client):
    read_only = dash.api_key(scopes=["orders:read"])
    full = dash.api_key()
    order(client, full, "S1", "10")
    assert client.get("/v1/orders/S1", headers=bearer(read_only)).status_code == 200
    r = order(client, read_only, "S2", "10")
    assert r.status_code == 403 and "orders:write" in r.json()["detail"]["message"]
    assert client.post("/v1/orders/S1/refund", json={}, headers=bearer(read_only)).status_code == 403
    assert client.get("/v1/events", headers=bearer(read_only)).status_code == 403
    assert dash.post(f"{dash.env()}/api-keys", {"name": "bad", "scopes": ["everything"]}).status_code == 400


def test_api_rate_limit(client, key, app):
    app.state.rt.settings.__dict__["api_rate_per_minute"] = 5
    try:
        codes = [client.get("/v1/gateways", headers=bearer(key)).status_code for _ in range(7)]
        r = client.get("/v1/gateways", headers=bearer(key))
    finally:
        app.state.rt.settings.__dict__["api_rate_per_minute"] = 600
    assert codes == [200] * 5 + [429, 429]
    assert r.json()["detail"]["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
    assert client.get("/v1/gateways", headers=bearer(key)).status_code == 200  # limit lifted, same window


def test_drop_in_compatibility_with_self_hosted_bdpay(client, key):
    """The exact calls from the build spec, part 7.1, with the key in X-BDPAY-ADMIN."""
    h = {"X-BDPAY-ADMIN": key}
    assert client.get("/bdpay/gateways", headers=h).json()[0]["id"] == "simulator"
    r = client.post(
        "/bdpay/orders",
        json={"order_id": "INV777", "amount": "250", "gateway": "simulator", "customer": {"phone": "01700000000"}},
        headers=h,
    )
    assert r.status_code == 200
    reference_keys = [
        "order_id",
        "gateway",
        "amount",
        "currency",
        "status",
        "provider_payment_id",
        "provider_txn_id",
        "refunded",
    ]
    assert list(r.json())[:8] == reference_keys and r.json()["amount"] == "250.00"
    url = client.post("/bdpay/orders/INV777/pay", headers=h).json()["redirect_url"]
    simulate(client, url)
    got = client.get("/bdpay/orders/INV777", headers=h).json()
    assert got["status"] == "paid" and isinstance(got["events"], list)
    assert client.post("/bdpay/orders/INV777/requery", headers=h).json()["status"] == "paid"
    assert (
        client.post(
            "/bdpay/orders/INV777/refund", json={"amount": "50.00", "reason": "returned item"}, headers=h
        ).json()["ok"]
        is True
    )
    assert (
        client.post(
            "/bdpay/orders", json={"order_id": "INV777", "amount": "1", "gateway": "simulator"}, headers=h
        ).json()["detail"]["code"]
        == "DUPLICATE"
    )
    assert client.get("/bdpay/orders/INV777").status_code == 401  # no header, no access
    assert client.get("/bdpay/checkout/sessions/x", headers=h).status_code == 404  # only the reference paths


def test_one_tenants_key_cannot_touch_another_tenants_orders(client, dash, key, new_client):
    other = Dash(new_client())
    other.signup()
    other_key = other.api_key()
    order(client, key, "MINE1", "10")
    for method, path in (
        ("GET", "/v1/orders/MINE1"),
        ("POST", "/v1/orders/MINE1/pay"),
        ("POST", "/v1/orders/MINE1/requery"),
        ("POST", "/v1/orders/MINE1/refund"),
    ):
        r = client.request(method, path, headers=bearer(other_key), json={} if method == "POST" else None)
        assert r.status_code == 404, path
    assert client.get("/v1/orders", headers=bearer(other_key)).json()["data"] == []
    assert order(client, other_key, "MINE1", "77").json()["amount"] == "77.00"  # their own, separate MINE1
    assert client.get("/v1/orders/MINE1", headers=bearer(key)).json()["amount"] == "10.00"


def test_openapi_documents_only_the_merchant_api(client):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert {"/v1/orders", "/v1/orders/{order_id}/pay", "/v1/checkout/sessions", "/bdpay/orders", "/healthz"} <= paths
    assert not any(p.startswith(("/api/", "/p/", "/simulator", "/checkout/")) for p in paths)
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert client.get("/docs").status_code == 200
