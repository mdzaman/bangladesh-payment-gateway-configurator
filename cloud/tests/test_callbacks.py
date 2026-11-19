"""Provider-facing endpoints: signed callbacks, IPNs, the result page."""

from sqlalchemy import text

import helpers
from helpers import BKASH, SSLCOMMERZ, Dash, FakeBkash, FakeSsl, bearer, simulate


def start(client, key, order_id="CB1", amount="250", gateway="simulator", **extra):
    r = client.post(
        "/v1/orders", json={"order_id": order_id, "amount": amount, "gateway": gateway, **extra}, headers=bearer(key)
    )
    assert r.status_code == 200, r.text
    return client.post(f"/v1/orders/{order_id}/pay", headers=bearer(key)).json()["redirect_url"]


def status(client, key, order_id):
    return client.get(f"/v1/orders/{order_id}", headers=bearer(key)).json()["status"]


def test_callback_verifies_then_shows_the_signed_result_page(client, dash):
    key = dash.api_key()
    back = simulate(client, start(client, key))
    assert back.status_code == 303
    page = client.get(back.headers["location"])
    assert page.status_code == 200 and "Payment successful" in page.text and "৳250.00" in page.text
    assert "script" not in page.text.lower()
    assert (
        "default-src 'none'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"
    )
    # The result page cannot be read by guessing: the signature is part of the path.
    path = back.headers["location"]
    assert client.get(path[:-4] + "0000").status_code == 404
    assert client.get(path.replace("/CB1/", "/CB2/")).status_code == 404
    assert "পেমেন্ট সফল হয়েছে" in client.get(path + "?lang=bn").text and "৳২৫০.০০" in client.get(path + "?lang=bn").text


def test_each_outcome_is_checked_with_the_provider_not_taken_from_the_url(client, dash):
    key = dash.api_key()
    for i, (action, expected) in enumerate(
        [("approve", "paid"), ("decline", "failed"), ("cancel", "cancelled"), ("wrong_amount", "needs_review")]
    ):
        oid = f"OUT{i}"
        simulate(client, start(client, key, oid, "100"), action)
        assert status(client, key, oid) == expected, action
    # The customer declines, then edits the return URL from /fail to /success: still not paid.
    url = start(client, key, "FORGE1", "100")
    r = simulate(client, url, "decline", follow=False)
    forged = (
        r.headers["location"]
        .replace("https://pay.test", "")
        .replace("/fail?", "/success?")
        .replace("status=fail", "status=success")
    )
    assert client.get(forged, follow_redirects=False).status_code == 303
    assert status(client, key, "FORGE1") == "failed"


def test_forged_expired_and_cross_tenant_links_are_refused(client, dash, new_client, rt):
    key = dash.api_key()
    url = start(client, key, "SIG1", "100")
    r = simulate(client, url, "approve", follow=False)
    good = r.headers["location"].replace("https://pay.test", "")
    prefix, gw, oid, token, outcome = helpers.callback_parts(good)
    query = good.split("?", 1)[1]
    bad_token = f"{prefix}/callback/{gw}/{oid}/{token[:-1]}{'0' if token[-1] != '0' else '1'}/{outcome}?{query}"
    assert client.get(bad_token).status_code == 403 and "not valid" in client.get(bad_token).text
    exp, mac = token.split(".")
    past = f"{prefix}/callback/{gw}/{oid}/{int(exp) - 200000}.{mac}/{outcome}?{query}"
    assert client.get(past).status_code == 403
    # Another tenant's environment key with this tenant's valid token: refused (different secret).
    other = Dash(new_client())
    other.signup()
    other_key = other.get(f"{other.env()}/settings").json()["environment_key"]
    cross = f"/p/{other_key}/callback/{gw}/{oid}/{token}/{outcome}?{query}"
    assert client.get(cross).status_code == 403
    assert client.get(f"/p/es_doesnotexist/callback/{gw}/{oid}/{token}/{outcome}").status_code == 404
    assert status(client, key, "SIG1") == "pending"  # none of that settled anything
    assert client.get(good, follow_redirects=False).status_code == 303 and status(client, key, "SIG1") == "paid"
    # Replaying the good link later is harmless.
    assert client.get(good, follow_redirects=False).status_code == 303 and status(client, key, "SIG1") == "paid"


def test_return_url_templates(client, dash):
    key = dash.api_key()
    url = start(client, key, "RET1", "100", return_url="https://shop.example/thanks?order={order_id}&status={status}")
    back = simulate(client, url)
    assert back.headers["location"] == "https://shop.example/thanks?order=RET1&status=paid"
    # Workspace default, used when the order sets none.
    dash.patch(f"{dash.env()}/settings", {"result_url": "https://shop.example/done/{order_id}/{status}"})
    back = simulate(client, start(client, key, "RET2", "100"), "cancel")
    assert back.headers["location"] == "https://shop.example/done/RET2/cancelled"


def test_real_gateway_callback_and_duplicate(client, dash, provider):
    fake = provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    key = dash.api_key()
    assert start(client, key, "BK1", "250", "bkash").startswith("https://bkash.test/pay/")
    callback = fake.callback_for("BK1")
    assert "/p/es_" in callback and "/callback/bkash/BK1/" in callback
    assert client.get(callback, follow_redirects=False).status_code == 303
    assert status(client, key, "BK1") == "paid"
    executes = sum(1 for c in fake.calls if c.endswith("/execute"))
    client.get(callback, follow_redirects=False)  # browser back button
    assert sum(1 for c in fake.calls if c.endswith("/execute")) == executes  # settled orders are not re-executed
    assert client.get(fake.callback_for("BK1", "cancel"), follow_redirects=False).status_code == 303
    assert status(client, key, "BK1") == "paid"  # cannot go backwards


def test_ipn_is_only_a_hint_and_is_reverified(client, dash, provider):
    fake = provider(FakeSsl())
    saved = dash.put(f"{dash.env()}/gateways/sslcommerz", {"values": SSLCOMMERZ}).json()
    ipn = saved["urls"]["ipn"].replace("https://pay.test", "")
    assert ipn.endswith("/ipn/sslcommerz")
    key = dash.api_key()
    start(client, key, "IPN1", "1500", "sslcommerz")
    assert fake.sessions["IPN1"]["ipn_url"] == saved["urls"]["ipn"]  # the provider was given our IPN URL
    r = client.post(ipn, data={"tran_id": "IPN1", "val_id": "VALIPN1", "status": "VALID", "amount": "1.00"})
    assert r.json() == {"ok": True, "status": "paid"}
    assert client.get("/v1/orders/IPN1", headers=bearer(key)).json()["amount"] == "1500.00"
    assert client.post(ipn, data={"tran_id": "IPN1", "val_id": "VALIPN1"}).json()["status"] == "paid"
    assert client.post(ipn, data={"tran_id": "UNKNOWN", "val_id": "x"}).status_code == 404
    assert client.post(ipn, data={}).status_code == 400
    assert client.post(ipn.replace("/ipn/sslcommerz", "/ipn/bkash"), data={"tran_id": "IPN1"}).status_code in (400, 404)


def test_a_paying_customer_is_recorded_even_if_the_workspace_was_suspended_meanwhile(client, dash, rt):
    key = dash.api_key()
    url = start(client, key, "SUS1", "100")
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE workspaces SET suspended_at = now() WHERE id = :w"), {"w": dash.workspace_id})
    assert (
        client.post(
            "/v1/orders", json={"order_id": "SUS2", "amount": "1", "gateway": "simulator"}, headers=bearer(key)
        ).status_code
        == 403
    )  # no new business
    assert simulate(client, url).status_code == 303  # but the payment in flight lands
    with rt.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM orders WHERE workspace_id = :w AND order_id = 'SUS1'"),
                {"w": dash.workspace_id},
            ).scalar()
            == "paid"
        )


def test_callback_tokens_never_reach_the_access_log(client, dash, caplog):
    key = dash.api_key()
    url = start(client, key, "LOG1", "100")
    with caplog.at_level("INFO"):
        r = simulate(client, url, "approve", follow=False)
        callback = r.headers["location"].replace("https://pay.test", "")
        client.get(callback, follow_redirects=False)
    token = helpers.callback_parts(callback)[3]
    ours = [rec.getMessage() for rec in caplog.records if rec.name.startswith("bdpay")]
    assert ours and not any(token in line for line in ours)
    assert any("/p/{env_key}/callback/{gateway}/{order_id}/{token}/{outcome}" in line for line in ours)
