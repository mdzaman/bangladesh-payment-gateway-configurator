"""Cross-cutting behaviour: headers, limits, health, the CLI."""

import base64

from sqlalchemy import text

from bdpay_cloud.crypto import Vault

import helpers
from conftest import TEST_KEYS
from helpers import BKASH, Dash, bearer
from helpers import run_cli as _cli


def test_security_headers_and_request_ids(client, dash):
    r = client.get("/api/config")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "script-src 'self';" in r.headers["content-security-policy"]
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
    assert r.headers["x-request-id"].startswith("req_")
    assert (
        client.get("/api/config", headers={"X-Request-Id": "trace-abc-12345"}).headers["x-request-id"]
        == "trace-abc-12345"
    )
    assert client.get("/api/config", headers={"X-Request-Id": "bad id\n"}).headers["x-request-id"].startswith("req_")


def test_errors_are_json_with_stable_codes(client):
    assert client.get("/v1/nothing-here").json()["detail"]["code"] == "NOT_FOUND"
    assert client.put("/v1/orders").json()["detail"]["code"] == "METHOD_NOT_ALLOWED"
    r = client.post("/api/auth/login", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VALIDATION"
    big = client.post("/api/auth/login", content=b"x" * (300 * 1024), headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and big.json()["detail"]["code"] == "TOO_LARGE"


def test_health_readiness_and_metrics(client, dash):
    assert client.get("/healthz").json()["ok"] is True
    ready = client.get("/readyz").json()
    assert ready == {"ok": True, "checks": {"database": True, "migrations": True}}
    client.get("/v1/gateways", headers=bearer(dash.api_key()))
    body = client.get("/metrics").text
    assert 'bdpay_http_requests_total{method="GET",route="/v1/gateways",status="200"}' in body
    assert "bdpay_http_request_seconds_bucket" in body and "bdpay_worker_backlog" in body
    assert "/p/es_" not in body and "bdp_test_" not in body  # route templates only, never raw paths


def test_metrics_can_require_a_token():
    from fastapi.testclient import TestClient

    from bdpay_cloud.api.app import create_app

    from conftest import make_settings

    app = create_app(make_settings(metrics_token="scrape-secret", docs_enabled=False))
    with TestClient(app, base_url="https://pay.test") as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer scrape-secret"}).status_code == 200
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
        assert c.get("/").json()["service"] == "bdpay cloud"
    app.state.rt.close()


def test_provider_calls_are_counted_from_the_reference_client_logs(client, dash, provider):
    provider(helpers.FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    dash.post(f"{dash.env()}/gateways/bkash/test")
    assert 'bdpay_provider_requests_total{provider="bkash",status="200"}' in client.get("/metrics").text


def test_dashboard_is_served_with_client_side_routing(tmp_path):
    from fastapi.testclient import TestClient

    from bdpay_cloud.api.app import create_app

    from conftest import make_settings

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>dashboard</title>")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    app = create_app(make_settings(web_dist=str(tmp_path)))
    with TestClient(app, base_url="https://pay.test") as c:
        for path in ("/", "/login", "/app/my-shop/sandbox/payments", "/generator"):
            r = c.get(path)
            assert r.status_code == 200 and "dashboard" in r.text and r.headers["cache-control"] == "no-cache", path
        asset = c.get("/assets/app-abc123.js")
        assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
        assert c.get("/favicon.svg").text == "<svg/>"
        assert c.get("/api/nope").status_code == 404 and c.get("/v1/nope").json()["detail"]["code"] == "NOT_FOUND"
        assert c.get("/..%2f..%2fetc/passwd").status_code in (200, 404)
        assert "root:" not in c.get("/..%2f..%2fetc/passwd").text
    app.state.rt.close()


def test_cli_doctor_migrate_and_gen_key(monkeypatch, capsys, engine):
    code, out, _ = _cli(monkeypatch, capsys, "doctor")
    assert code == 0 and "✓ database reachable (PostgreSQL" in out and "✓ schema is current" in out
    assert "✓ key ring: t1" in out and "Dhaka time" in out
    assert _cli(monkeypatch, capsys, "migrate", "--check")[0] == 0
    assert "schema already at" in _cli(monkeypatch, capsys, "migrate")[1]
    code, out, err = _cli(monkeypatch, capsys, "gen-key", "--id", "k2027")
    assert out.startswith("k2027:") and len(base64.b64decode(out.strip().split(":")[1])) == 32 and "rotate-keys" in err
    code, out, _ = _cli(
        monkeypatch, capsys, "doctor", ENV="production", PUBLIC_URL="http://pay.example.com", MAIL_BACKEND="smtp"
    )
    assert code == 1 and "✗ BDPAY_CLOUD_PUBLIC_URL must be https://" in out


def test_cli_rotate_keys(monkeypatch, capsys, rt, dash):
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    hook = dash.post(f"{dash.env()}/webhooks", {"url": "https://merchant.example/hook"}).json()
    new_ring = "t2:" + base64.b64encode(b"n" * 32).decode() + "," + TEST_KEYS
    code, out, _ = _cli(monkeypatch, capsys, "rotate-keys", keys=new_ring)
    assert code == 0 and "re-encrypted with key 't2'" in out and "you can remove: t1" in out
    with rt.engine.connect() as conn:
        tokens = (
            conn.execute(
                text("""
            SELECT secrets_enc FROM gateway_configs WHERE workspace_id = :w AND gateway = 'bkash'
            UNION ALL SELECT secret_enc FROM webhook_endpoints WHERE id = :h
            UNION ALL SELECT callback_secret_enc FROM workspace_envs WHERE workspace_id = :w"""),
                {"w": dash.workspace_id, "h": hook["id"]},
            )
            .scalars()
            .all()
        )
    assert len(tokens) == 4 and all(t.startswith("v1.t2.") for t in tokens)
    # The new ring reads everything; put the old key back first so the rest of the suite still can.
    from bdpay_cloud.crypto import aad_gateway

    secrets = Vault.from_spec(new_ring).decrypt_json(tokens[0], aad_gateway(dash.workspace_id, "sandbox", "bkash"))
    assert secrets["BKASH_APP_SECRET"] == BKASH["BKASH_APP_SECRET"]
    assert _cli(monkeypatch, capsys, "rotate-keys", keys=TEST_KEYS + "," + new_ring.split(",")[0])[0] == 0
    assert (
        rt.gateway_values(helpers.Scope(dash.workspace_id, "sandbox"), "bkash").values["BKASH_PASSWORD"]
        == BKASH["BKASH_PASSWORD"]
    )


def test_cli_rotate_keys_reports_unreadable_values_and_rotates_the_rest(monkeypatch, capsys, rt, dash):
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    foreign = Vault.from_spec("lost:" + base64.b64encode(b"z" * 32).decode())
    from bdpay_cloud.crypto import aad_gateway

    aad = aad_gateway(dash.workspace_id, "sandbox", "bkash")
    with rt.engine.begin() as conn:  # written with a key nobody has any more
        good = conn.execute(
            text("SELECT secrets_enc FROM gateway_configs WHERE workspace_id = :w AND gateway = 'bkash'"),
            {"w": dash.workspace_id},
        ).scalar()
        conn.execute(
            text("UPDATE gateway_configs SET secrets_enc = :s WHERE workspace_id = :w AND gateway = 'bkash'"),
            {"s": foreign.encrypt_json({"BKASH_APP_SECRET": "x"}, aad), "w": dash.workspace_id},
        )
    new_ring = "t3:" + base64.b64encode(b"m" * 32).decode() + "," + TEST_KEYS
    try:
        code, out, err = _cli(monkeypatch, capsys, "rotate-keys", keys=new_ring)
        assert (
            code == 1
            and "1 value(s) could not be read" in err
            and f"bkash credentials of {dash.workspace_id}/sandbox" in err
        )
        assert "Do NOT remove old keys yet" in err and "you can remove" not in out
        with rt.engine.connect() as conn:  # everything else still rotated
            env_secret = conn.execute(
                text("SELECT callback_secret_enc FROM workspace_envs WHERE workspace_id = :w AND mode = 'sandbox'"),
                {"w": dash.workspace_id},
            ).scalar()
        assert env_secret.startswith("v1.t3.")
    finally:
        with rt.engine.begin() as conn:
            conn.execute(
                text("UPDATE gateway_configs SET secrets_enc = :s WHERE workspace_id = :w AND gateway = 'bkash'"),
                {"s": good, "w": dash.workspace_id},
            )
        assert _cli(monkeypatch, capsys, "rotate-keys", keys=TEST_KEYS + "," + new_ring.split(",")[0])[0] == 0


def test_cli_create_user(monkeypatch, capsys, new_client):
    import io

    email = f"ops-{helpers.rand_b32(6)}@example.com"
    monkeypatch.setattr("sys.stdin", io.StringIO("a long operator password\n"))
    code, out, _ = _cli(
        monkeypatch,
        capsys,
        "create-user",
        "--email",
        email,
        "--name",
        "Ops Person",
        "--workspace",
        "Ops Shop",
        "--platform-admin",
        "--password-stdin",
    )
    assert code == 0 and "platform admin" in out
    d = Dash(new_client())
    assert d.login(email, "a long operator password").status_code == 200
    assert (
        d.me["user"]["is_platform_admin"]
        and d.me["user"]["email_verified"]
        and d.me["workspaces"][0]["name"] == "Ops Shop"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert (
        _cli(monkeypatch, capsys, "create-user", "--email", "x@example.com", "--name", "X", "--password-stdin")[0] == 1
    )


def test_seed_demo_fills_a_sandbox_through_the_real_engine(rt, dash, client):
    from bdpay_cloud.devtools import seed_demo

    made = seed_demo(rt, dash.slug, count=25, days=10, seed=3)
    assert sum(v for k, v in made.items() if k != "refunded") == 25 and made["paid"] >= 10
    stats = dash.get(f"{dash.env()}/overview?days=30").json()["stats"]
    assert stats["orders"] == 25 and stats["paid_count"] == made["paid"]
    assert sum(1 for d in stats["daily"] if d["paid"]) >= 3  # spread over several days
    key = dash.api_key()
    page = client.get("/v1/orders?status=paid&limit=5", headers=bearer(key)).json()
    assert page["data"][0]["metadata"]["demo"] == "1" and page["data"][0]["paid_at"]


def test_seed_demo_refuses_production(rt, dash):
    import pytest

    from bdpay_cloud.devtools import seed_demo

    rt.settings.__dict__["env"] = "production"
    try:
        with pytest.raises(RuntimeError, match="refuses to run in production"):
            seed_demo(rt, dash.slug, count=1)
    finally:
        rt.settings.__dict__["env"] = "test"


def test_cli_openapi_prints_only_the_document(monkeypatch, capsys):
    import json

    code, out, _ = _cli(monkeypatch, capsys, "openapi")
    spec = json.loads(out)  # nothing but JSON on stdout
    assert code == 0 and "/v1/orders" in spec["paths"] and spec["info"]["version"] == "1.0.0"
