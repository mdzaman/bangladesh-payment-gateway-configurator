"""The hosted configurator: saving, validating and testing gateway credentials."""

import httpx

import helpers
from helpers import BKASH, SSLCOMMERZ, Dash, FakeBkash, FakeSsl, last_email, token_from


def state(dash, gateway, mode="sandbox"):
    return next(g for g in dash.get(f"{dash.env(mode)}/gateways").json()["gateways"] if g["id"] == gateway)


def test_catalog_is_public_and_complete(client):
    data = client.get("/api/catalog").json()
    ids = [g["id"] for g in data["gateways"]]
    assert ids == [
        "bkash",
        "nagad",
        "sslcommerz",
        "aamarpay",
        "shurjopay",
        "eps",
        "portpos",
        "paystation",
        "upay",
        "uddoktapay",
        "simulator",
    ]
    assert [v["id"] for v in data["via"]][:2] == ["rocket", "okwallet"]
    bkash = data["gateways"][0]
    assert [f["k"] for f in bkash["f"] if f.get("req")] == [
        "BKASH_APP_KEY",
        "BKASH_APP_SECRET",
        "BKASH_USERNAME",
        "BKASH_PASSWORD",
    ]
    assert client.get("/api/config").json()["live_mode"] == "self_serve"


def test_every_catalog_gateway_has_a_driver_and_matching_required_fields():
    from bdpay_cloud import catalog
    from bdpay_cloud.engine.drivers import KNOWN_GATEWAYS, driver_class

    for spec in catalog.load().gateways:
        assert spec.id in KNOWN_GATEWAYS
        cls = driver_class(spec.id)
        required = {f.key for f in spec.fields if f.required and f.type != "check"}
        assert set(cls.required_env) <= required | {"NAGAD_PRIVATE_KEY", "NAGAD_PUBLIC_KEY"}, spec.id
        assert spec.raw["checkout"]["label"] and spec.raw["checkout"]["label_bn"]


def test_save_never_returns_or_stores_plain_secrets(dash, rt):
    r = dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["enabled"] is True and saved["status"] == "unverified" and saved["refunds"] == "partial"
    assert saved["values"] == {"BKASH_APP_KEY": "app", "BKASH_USERNAME": "u", "BKASH_API_VERSION": "v1.2.0-beta"}
    assert saved["secrets"]["BKASH_APP_SECRET"] == {"set": True, "hint": "•••• h-12"}
    assert saved["urls"]["callback"].startswith("https://pay.test/p/es_") and saved["urls"]["ipn"] is None
    listing = dash.get(f"{dash.env()}/gateways").text
    audit = dash.get(f"/api/workspaces/{dash.slug}/audit-log").text
    for secret in (BKASH["BKASH_APP_SECRET"], BKASH["BKASH_PASSWORD"]):
        assert secret not in r.text and secret not in listing and secret not in audit
    assert '"gateway.saved"' in audit and "BKASH_APP_SECRET" in audit  # field names yes, values no


def test_blank_secret_keeps_the_stored_one_and_new_credentials_reset_status(dash, rt, provider):
    provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    assert dash.post(f"{dash.env()}/gateways/bkash/test").json()["result"] == "verified"
    # Change only the username; send the secret fields blank, as the form does.
    r = dash.put(
        f"{dash.env()}/gateways/bkash",
        {"values": {"BKASH_USERNAME": "u2", "BKASH_APP_SECRET": "", "BKASH_PASSWORD": ""}},
    )
    assert r.status_code == 200 and r.json()["status"] == "unverified"
    values = rt.gateway_values(helpers.Scope(dash.workspace_id, "sandbox"), "bkash").values
    assert values["BKASH_APP_SECRET"] == BKASH["BKASH_APP_SECRET"] and values["BKASH_USERNAME"] == "u2"
    # Saving the same values again changes nothing, so the status is kept.
    dash.post(f"{dash.env()}/gateways/bkash/test")
    assert dash.put(f"{dash.env()}/gateways/bkash", {"values": {"BKASH_USERNAME": "u2"}}).json()["status"] == "verified"


def test_required_fields_and_unknown_fields(dash):
    r = dash.put(f"{dash.env()}/gateways/bkash", {"values": {"BKASH_APP_KEY": "only this"}})
    assert r.status_code == 400
    fields = {e["field"] for e in r.json()["detail"]["errors"]}
    assert fields == {"BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD"}
    assert dash.get(f"{dash.env()}/gateways").json()["gateways"][0]["id"] == "simulator"  # nothing was saved
    assert dash.put(f"{dash.env()}/gateways/bkash", {"values": {**BKASH, "NOT_A_FIELD": "x"}}).status_code == 400
    assert dash.put(f"{dash.env()}/gateways/nope", {"values": {}}).status_code == 404
    assert (
        dash.put(
            f"{dash.env()}/gateways/paystation",
            {"values": {"PAYSTATION_MERCHANT_ID": "1", "PAYSTATION_PASSWORD": "pw", "PAYSTATION_PAY_WITH_CHARGE": "7"}},
        ).status_code
        == 400
    )


def test_demo_values_are_refused_in_live_mode(dash):
    dash.go_live()
    demo = {"SSLCOMMERZ_STORE_ID": "testbox", "SSLCOMMERZ_STORE_PASSWORD": "qwerty"}
    assert dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": demo}).status_code == 200  # fine in sandbox
    r = dash.put(f"{dash.env('live')}/gateways/sslcommerz", {"values": demo})
    assert r.status_code == 400
    messages = [e["message"] for e in r.json()["detail"]["errors"]]
    assert all("sandbox demo value" in m for m in messages) and len(messages) == 2
    assert dash.put(f"{dash.env('live')}/gateways/sslcommerz", {"values": SSLCOMMERZ}).status_code == 200
    assert dash.put(f"{dash.env('live')}/gateways/simulator", {"values": {}}).status_code == 403


def test_base_url_overrides_cannot_point_inside_the_network(dash):
    upay = {
        "UPAY_MERCHANT_ID": "M",
        "UPAY_MERCHANT_KEY": "merchant-key-000",
        "UPAY_MERCHANT_CODE": "C",
        "UPAY_MERCHANT_NAME": "Shop",
        "UPAY_MERCHANT_MOBILE": "01800000000",
    }
    for bad in (
        "http://127.0.0.1:8080",
        "http://169.254.169.254/latest",
        "https://[::1]/",
        "http://10.1.2.3",
        "file:///etc/passwd",
        "https://user:pw@8.8.8.8",
    ):
        r = dash.put(f"{dash.env()}/gateways/upay", {"values": {**upay, "UPAY_BASE_URL": bad}})
        assert r.status_code == 400 and r.json()["detail"]["errors"][0]["field"] == "UPAY_BASE_URL", bad
    assert (
        dash.put(
            f"{dash.env()}/gateways/upay", {"values": {**upay, "UPAY_BASE_URL": "https://8.8.8.8/upay"}}
        ).status_code
        == 200
    )
    dash.go_live()  # live refuses plain http as well
    r = dash.put(f"{dash.env('live')}/gateways/upay", {"values": {**upay, "UPAY_BASE_URL": "http://8.8.8.8/upay"}})
    assert r.status_code == 400 and "https" in r.json()["detail"]["errors"][0]["message"]


def test_experimental_gateway_is_off_unless_the_platform_allows_it(dash):
    assert "uddoktapay" not in [g["id"] for g in dash.get(f"{dash.env()}/gateways").json()["gateways"]]
    r = dash.put(
        f"{dash.env()}/gateways/uddoktapay",
        {
            "values": {
                "UDDOKTAPAY_BASE_URL": "https://sandbox.uddoktapay.com",
                "UDDOKTAPAY_API_KEY": "k" * 40,
                "BDPAY_ALLOW_EXPERIMENTAL": True,
            }
        },
    )
    assert r.status_code == 403


def test_experimental_gateway_needs_the_compliance_box_when_allowed():
    from fastapi.testclient import TestClient

    from bdpay_cloud.api.app import create_app

    from conftest import make_settings

    app = create_app(make_settings(allow_experimental=True))
    with TestClient(app, base_url="https://pay.test") as c:
        d = Dash(c)
        d.signup()
        values = {"UDDOKTAPAY_BASE_URL": "https://8.8.8.8", "UDDOKTAPAY_API_KEY": "k" * 40}
        r = d.put(f"{d.env()}/gateways/uddoktapay", {"values": values})
        assert r.status_code == 400 and r.json()["detail"]["errors"][0]["message"] == "Tick the compliance box"
        r = d.put(f"{d.env()}/gateways/uddoktapay", {"values": {**values, "BDPAY_ALLOW_EXPERIMENTAL": True}})
        assert r.status_code == 200 and r.json()["values"]["BDPAY_ALLOW_EXPERIMENTAL"] == "1"
    app.state.rt.close()


def test_nagad_keys_are_validated(dash):
    r = dash.put(
        f"{dash.env()}/gateways/nagad",
        {
            "values": {
                "NAGAD_MERCHANT_ID": "683002007104225",
                "NAGAD_PRIVATE_KEY": "not a key",
                "NAGAD_PUBLIC_KEY": "!!",
            }
        },
    )
    assert r.status_code == 400
    assert {e["field"] for e in r.json()["detail"]["errors"]} == {"NAGAD_PRIVATE_KEY", "NAGAD_PUBLIC_KEY"}


def test_connection_test_reports_what_was_really_checked(dash, provider):
    fake = provider(FakeBkash(bad_credentials=True))
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    r = dash.post(f"{dash.env()}/gateways/bkash/test").json()
    assert r["result"] == "rejected" and "Invalid credentials" in r["message"]
    assert state(dash, "bkash")["status"] == "rejected" and state(dash, "bkash")["last_test"]["result"] == "rejected"
    fake.bad_credentials = False
    assert dash.post(f"{dash.env()}/gateways/bkash/test").json()["result"] == "verified"

    ssl = provider(FakeSsl())
    dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ})
    assert dash.post(f"{dash.env()}/gateways/sslcommerz/test").json()["result"] == "verified"
    dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": {"SSLCOMMERZ_STORE_PASSWORD": "wrong-password-1"}})
    r = dash.post(f"{dash.env()}/gateways/sslcommerz/test").json()
    assert r["result"] == "rejected" and "rejected the store ID or store password" in r["message"]
    ssl.api_connect = "INACTIVE"
    dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ})
    assert "inactive" in dash.post(f"{dash.env()}/gateways/sslcommerz/test").json()["message"]

    # aamarPay has no call that proves credentials without a payment: say so, do not pretend.
    provider(lambda req: httpx.Response(200, text="ok"))
    dash.put(
        f"{dash.env()}/gateways/aamarpay", {"values": {"AAMARPAY_STORE_ID": "s", "AAMARPAY_SIGNATURE_KEY": "k" * 32}}
    )
    r = dash.post(f"{dash.env()}/gateways/aamarpay/test").json()
    assert r["result"] == "reachable" and "run one test payment" in r["message"]
    assert state(dash, "aamarpay")["status"] == "unverified"

    def down(req):
        raise httpx.ConnectError("no route", request=req)

    provider(down)
    assert dash.post(f"{dash.env()}/gateways/aamarpay/test").json()["result"] == "unreachable"
    assert dash.post(f"{dash.env()}/gateways/simulator/test").json()["result"] == "verified"


def test_toggle_reorder_and_remove(dash, provider, rt):
    provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ})
    order = [g["id"] for g in dash.get(f"{dash.env()}/gateways").json()["gateways"] if g["configured"]]
    assert order == ["simulator", "bkash", "sslcommerz"]
    r = dash.post(f"{dash.env()}/gateways-order", {"order": ["sslcommerz", "bkash"]})
    assert [g["id"] for g in r.json()["gateways"] if g["configured"]] == ["sslcommerz", "bkash", "simulator"]
    assert [o["id"] for o in dash.get(f"{dash.env()}/checkout-options").json()["data"]] == [
        "sslcommerz",
        "bkash",
        "simulator",
    ]
    assert dash.patch(f"{dash.env()}/gateways/bkash", {"enabled": False}).json()["enabled"] is False
    assert [o["id"] for o in dash.get(f"{dash.env()}/checkout-options").json()["data"]] == ["sslcommerz", "simulator"]
    assert dash.patch(f"{dash.env()}/gateways/bkash", {"enabled": True}).json()["enabled"] is True
    # A gateway with a payment in flight cannot be removed.
    key = dash.api_key()
    c = dash.c
    c.post("/v1/orders", json={"order_id": "KEEP1", "amount": "10", "gateway": "bkash"}, headers=helpers.bearer(key))
    c.post("/v1/orders/KEEP1/pay", headers=helpers.bearer(key))
    r = dash.delete(f"{dash.env()}/gateways/bkash")
    assert r.status_code == 409 and "still in progress" in r.json()["detail"]["message"]
    assert dash.delete(f"{dash.env()}/gateways/sslcommerz").status_code == 200
    assert "sslcommerz" not in [
        g["id"] for g in dash.get(f"{dash.env()}/gateways").json()["gateways"] if g["configured"]
    ]


def test_roles_sandbox_versus_live(dash, rt, new_client):
    dash.go_live()

    def member(role):
        m = Dash(new_client())
        m.signup()
        dash.post(f"/api/workspaces/{dash.slug}/invitations", {"email": m.email, "role": role})
        assert m.post("/api/auth/accept-invitation", {"token": token_from(last_email(rt, m.email))}).status_code == 200
        return m

    analyst, developer, admin = member("analyst"), member("developer"), member("admin")
    sandbox, live = f"/api/workspaces/{dash.slug}/sandbox", f"/api/workspaces/{dash.slug}/live"
    body = {"values": SSLCOMMERZ}
    assert analyst.get(f"{sandbox}/gateways").status_code == 200  # everyone can look
    assert analyst.put(f"{sandbox}/gateways/sslcommerz", body).status_code == 403
    assert analyst.get(f"{sandbox}/api-keys").status_code == 403  # but not at keys or webhooks
    assert developer.put(f"{sandbox}/gateways/sslcommerz", body).status_code == 200
    assert developer.put(f"{live}/gateways/sslcommerz", body).status_code == 403  # live credentials need an admin
    assert developer.post(f"{live}/api-keys", {"name": "x"}).status_code == 403
    assert developer.post(f"{sandbox}/api-keys", {"name": "x"}).status_code == 200
    assert admin.put(f"{live}/gateways/sslcommerz", body).status_code == 200
    assert admin.post(f"/api/workspaces/{dash.slug}/go-live", {"accept": True, "legal_name": "X Y"}).status_code == 403


def test_checklist(dash, provider, rt):
    items = {i["id"]: i for i in dash.get(f"{dash.env()}/checklist").json()["items"]}
    assert items["gateway"]["state"] == "done" and items["api_key"]["state"] == "todo"
    assert items["webhook"]["state"] == "todo" and items["needs_review"]["state"] == "done"
    provider(FakeSsl())
    dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ})
    dash.api_key()
    items = {i["id"]: i for i in dash.get(f"{dash.env()}/checklist").json()["items"]}
    assert items["api_key"]["state"] == "done"
    assert items["sslcommerz.ipn"]["state"] == "manual" and "/ipn/sslcommerz" in items["sslcommerz.ipn"]["detail"]
    assert items["sslcommerz.payment"]["state"] == "todo"
    after = dash.post(f"{dash.env()}/checklist", {"id": "sslcommerz.ipn", "done": True}).json()["items"]
    assert {i["id"]: i for i in after}["sslcommerz.ipn"]["state"] == "done"
    after = dash.post(f"{dash.env()}/checklist", {"id": "sslcommerz.ipn", "done": False}).json()["items"]
    assert {i["id"]: i for i in after}["sslcommerz.ipn"]["state"] == "manual"


def test_environment_settings(dash):
    assert dash.get(f"{dash.env()}/settings").json()["result_url"] is None
    r = dash.patch(
        f"{dash.env()}/settings", {"result_url": "https://shop.example/thanks?order={order_id}&status={status}"}
    )
    assert r.status_code == 200 and r.json()["environment_key"].startswith("es_")
    assert dash.patch(f"{dash.env()}/settings", {"result_url": "javascript:alert(1)"}).status_code == 400
    assert dash.patch(f"{dash.env()}/settings", {"result_url": None}).json()["result_url"] is None
