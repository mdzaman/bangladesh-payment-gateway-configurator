"""Tenant isolation, checked object by object: nothing that belongs to
workspace A can be read or changed through workspace B, by id or by guess."""

import pytest

from helpers import Dash, bearer, simulate


@pytest.fixture
def two(dash, new_client, client):
    """Workspace A with one of everything, and an unrelated workspace B."""
    a, b = dash, Dash(new_client())
    b.signup()
    key_a, key_b = a.api_key(), b.api_key()
    client.post("/v1/orders", json={"order_id": "ISO1", "amount": "500", "gateway": "simulator"}, headers=bearer(key_a))
    simulate(client, client.post("/v1/orders/ISO1/pay", headers=bearer(key_a)).json()["redirect_url"])
    made = {
        "key_id": a.get(f"{a.env()}/api-keys").json()["data"][0]["id"],
        "webhook": a.post(f"{a.env()}/webhooks", {"url": "https://a.example/hook"}).json()["id"],
        "session": client.post("/v1/checkout/sessions", json={"amount": "10"}, headers=bearer(key_a)).json()["id"],
        "link": a.post(f"{a.env()}/payment-links", {"title": "A's link", "amount": "10"}).json()["id"],
        "event": client.get("/v1/events", headers=bearer(key_a)).json()["data"][0]["id"],
    }
    made["delivery"] = a.post(f"{a.env()}/webhooks/{made['webhook']}/test").json()["event_id"]
    return a, b, key_a, key_b, made


def test_b_cannot_reach_a_through_a_s_urls(two):
    a, b, _, _, made = two
    for path in (
        "/gateways",
        "/orders",
        "/orders/ISO1",
        "/api-keys",
        "/webhooks",
        "/events",
        "/webhook-deliveries",
        "/checkout-sessions",
        "/payment-links",
        "/overview",
        "/checklist",
        "/settings",
    ):
        assert b.get(a.env() + path).status_code == 404, path
    assert b.put(f"{a.env()}/gateways/sslcommerz", {"values": {}}).status_code == 404
    assert b.post(f"{a.env()}/orders/ISO1/refund", {}).status_code == 404
    assert b.delete(f"{a.env()}/api-keys/{made['key_id']}").status_code == 404
    assert b.post(f"/api/workspaces/{a.slug}/go-live", {"accept": True, "legal_name": "Mallory"}).status_code == 404
    assert b.delete(f"/api/workspaces/{a.slug}").status_code == 404


def test_a_s_object_ids_do_nothing_inside_b(two):
    a, b, _, _, made = two
    env = b.env()
    assert b.get(f"{env}/orders/ISO1").status_code == 404
    assert b.post(f"{env}/orders/ISO1/requery").status_code == 404
    assert b.post(f"{env}/orders/ISO1/refund", {}).status_code == 404
    assert b.delete(f"{env}/api-keys/{made['key_id']}").status_code == 404
    assert b.patch(f"{env}/webhooks/{made['webhook']}", {"enabled": False}).status_code == 404
    assert b.delete(f"{env}/webhooks/{made['webhook']}").status_code == 404
    assert b.post(f"{env}/webhooks/{made['webhook']}/rotate-secret").status_code == 404
    assert b.post(f"{env}/webhooks/{made['webhook']}/test").status_code == 404
    assert b.patch(f"{env}/payment-links/{made['link']}", {"active": False}).status_code == 404
    assert b.get(f"{env}/webhook-deliveries?endpoint_id={made['webhook']}").json()["data"] == []
    # Everything of A is still exactly as it was.
    assert a.get(f"{a.env()}/orders/ISO1").json()["status"] == "paid"
    assert a.get(f"{a.env()}/webhooks").json()["data"][0]["enabled"] is True
    assert a.get(f"{a.env()}/payment-links").json()["data"][0]["active"] is True
    assert a.get(f"{a.env()}/api-keys").json()["data"][0]["revoked_at"] is None


def test_api_keys_are_walled_off_too(two, client):
    _, _, key_a, key_b, made = two
    assert client.get(f"/v1/checkout/sessions/{made['session']}", headers=bearer(key_b)).status_code == 404
    assert client.post(f"/v1/checkout/sessions/{made['session']}/expire", headers=bearer(key_b)).status_code == 404
    assert client.get(f"/v1/events/{made['event']}", headers=bearer(key_b)).status_code == 404
    assert client.get("/v1/events", headers=bearer(key_b)).json()["data"] == []
    assert client.get("/v1/payment-links", headers=bearer(key_b)).json()["data"] == []
    assert client.get(f"/v1/checkout/sessions/{made['session']}", headers=bearer(key_a)).status_code == 200


def test_sandbox_and_live_of_one_workspace_do_not_mix(dash, client):
    dash.go_live()
    sandbox_key, live_key = dash.api_key("sandbox"), dash.api_key("live")
    client.post(
        "/v1/orders", json={"order_id": "MODE1", "amount": "10", "gateway": "simulator"}, headers=bearer(sandbox_key)
    )
    assert client.get("/v1/orders/MODE1", headers=bearer(sandbox_key)).status_code == 200
    assert client.get("/v1/orders/MODE1", headers=bearer(live_key)).status_code == 404
    assert client.get("/v1/orders", headers=bearer(live_key)).json()["data"] == []
    assert dash.get(f"{dash.env('live')}/orders").json()["data"] == []
    assert len(dash.get(f"{dash.env('sandbox')}/api-keys").json()["data"]) == 1
    assert dash.get(f"{dash.env('live')}/api-keys").json()["data"][0]["display"].startswith("bdp_live_")
    sandbox_env = dash.get(f"{dash.env('sandbox')}/settings").json()["environment_key"]
    live_env = dash.get(f"{dash.env('live')}/settings").json()["environment_key"]
    assert sandbox_env.startswith("es_") and live_env.startswith("el_") and sandbox_env != live_env
    assert dash.get(f"/api/workspaces/{dash.slug}/staging/orders").status_code == 404


def test_platform_admin_routes_are_hidden_from_everyone_else(dash, rt, new_client):
    assert dash.get("/api/admin/workspaces").status_code == 404
    assert dash.post(f"/api/admin/workspaces/{dash.workspace_id}/suspend").status_code == 404
    from sqlalchemy import text

    admin = Dash(new_client())
    admin.signup()
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_platform_admin = true WHERE email = :e"), {"e": admin.email})
    listing = admin.get(f"/api/admin/workspaces?q={dash.slug}").json()["data"]
    assert [w["slug"] for w in listing] == [dash.slug] and listing[0]["members"] == 1
    assert (
        admin.post(f"/api/admin/workspaces/{dash.workspace_id}/suspend?reason=fraud+review").json()["suspended"] is True
    )
    assert dash.get("/api/auth/me").json()["workspaces"][0]["suspended_reason"] == "fraud review"
    assert admin.post(f"/api/admin/workspaces/{dash.workspace_id}/unsuspend").json()["suspended"] is False
    assert (
        admin.post(f"/api/admin/workspaces/{dash.workspace_id}/approve-live").status_code == 409
    )  # no attestation yet
