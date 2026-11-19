"""Hosted checkout sessions and payment links, as the customer sees them."""

import re

from sqlalchemy import text

import helpers
from helpers import BKASH, FakeBkash, bearer, simulate


def session(client, key, **body):
    r = client.post("/v1/checkout/sessions", json={"amount": "1250.00", **body}, headers=bearer(key))
    assert r.status_code == 200, r.text
    return r.json()


def path(url):
    return url.replace("https://pay.test", "")


def choose(client, session_id, gateway="simulator"):
    return client.post(f"/checkout/{session_id}/pay", data={"gateway": gateway}, follow_redirects=False)


def events(client, key):
    return [e["type"] for e in client.get("/v1/events", headers=bearer(key)).json()["data"]][::-1]


def test_session_page_lists_enabled_methods_in_english_and_bangla(client, dash):
    key = dash.api_key()
    s = session(
        client,
        key,
        reference="INV1001",
        description="Blue cotton panjabi",
        customer={"name": "Rahim", "phone": "01712345678"},
    )
    assert s["status"] == "open" and s["url"] == f"https://pay.test/checkout/{s['id']}" and s["id"].startswith("cs_")
    page = client.get(path(s["url"]))
    assert page.status_code == 200
    for expected in (
        "৳1,250.00",
        "Blue cotton panjabi",
        "INV1001",
        "Choose how to pay",
        "Test payment",
        "Test mode: no real money moves",
        'lang="en"',
    ):
        assert expected in page.text, expected
    assert "<script" not in page.text and "style=" not in page.text  # nothing inline: the CSP forbids it
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert page.headers["x-frame-options"] == "DENY" and page.headers["referrer-policy"] == "no-referrer"
    assert "01712345678" not in page.text  # the page never echoes customer data
    bn = client.get(path(s["url"]) + "?lang=bn")
    for expected in ("৳১,২৫০.০০", "পেমেন্টের মাধ্যম বেছে নিন", "টেস্ট পেমেন্ট", 'lang="bn"'):
        assert expected in bn.text, expected
    assert "পেমেন্টের মাধ্যম" in client.get(path(s["url"]), headers={"Accept-Language": "bn-BD,bn;q=0.9"}).text
    assert client.get("/checkout/cs_doesnotexist0000000000000000").status_code == 404
    assert client.get("/static/hosted.css").status_code == 200


def test_pay_then_success_page_and_events(client, dash):
    key = dash.api_key()
    s = session(
        client, key, reference="INV2", success_url="https://shop.example/ok?s={session_id}&o={order_id}&r={reference}"
    )
    r = choose(client, s["id"])
    assert r.status_code == 303 and "/simulator/sim_" in r.headers["location"]
    back = simulate(client, r.headers["location"])
    assert back.status_code == 303 and back.headers["location"].startswith(f"/checkout/{s['id']}?attempt=CO")
    done = client.get(back.headers["location"])
    assert "Payment successful" in done.text and "৳1,250.00" in done.text
    got = client.get(f"/v1/checkout/sessions/{s['id']}", headers=bearer(key)).json()
    assert got["status"] == "paid" and got["paid_order_id"].startswith("CO") and len(got["paid_order_id"]) == 18
    assert f"https://shop.example/ok?s={s['id']}&amp;o={got['paid_order_id']}&amp;r=INV2" in done.text
    assert 'http-equiv="refresh"' in done.text
    order = got["orders"][0]
    assert order["status"] == "paid" and order["source"] == "checkout" and order["checkout_session_id"] == s["id"]
    assert order["metadata"] == {"checkout_session": s["id"], "reference": "INV2"}
    assert events(client, key) == ["order.paid", "checkout.session.completed"]
    assert choose(client, s["id"]).headers["location"] == f"/checkout/{s['id']}"  # paying again is not offered


def test_a_failed_try_can_be_followed_by_another_method(client, dash, provider):
    fake = provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    key = dash.api_key()
    s = session(client, key)
    page = client.get(f"/checkout/{s['id']}").text
    assert page.index("Test payment") < page.index("bKash")  # the workspace's order
    back = simulate(client, choose(client, s["id"]).headers["location"], "cancel")
    retry = client.get(back.headers["location"])
    assert "You cancelled that payment" in retry.text and "Choose how to pay" in retry.text
    r = choose(client, s["id"], "bkash")
    assert r.headers["location"].startswith("https://bkash.test/pay/")
    got = client.get(f"/v1/checkout/sessions/{s['id']}", headers=bearer(key)).json()
    assert [(o["gateway"], o["status"]) for o in got["orders"]] == [("bkash", "pending"), ("simulator", "cancelled")]
    bkash_order = got["orders"][0]["order_id"]
    assert client.get(fake.callback_for(bkash_order), follow_redirects=False).status_code == 303
    assert client.get(f"/v1/checkout/sessions/{s['id']}", headers=bearer(key)).json()["paid_order_id"] == bkash_order
    assert events(client, key) == ["order.cancelled", "order.paid", "checkout.session.completed"]


def test_double_click_opens_one_payment(client, dash, provider):
    fake = provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    key = dash.api_key()
    s = session(client, key, gateways=["bkash"])
    assert "Test payment" not in client.get(f"/checkout/{s['id']}").text  # limited to the gateways asked for
    first = choose(client, s["id"], "bkash").headers["location"]
    fake.payments[next(iter(fake.payments))]["transactionStatus"] = "Initiated"  # the customer has not paid yet
    assert choose(client, s["id"], "bkash").headers["location"] == first
    assert fake.creates == 1
    assert choose(client, s["id"], "simulator").status_code == 400  # not offered on this session


def test_paying_twice_is_caught_before_it_happens_and_reported_if_it_does(client, dash, rt):
    key = dash.api_key()
    s = session(client, key)
    first = choose(client, s["id"]).headers["location"]
    # The customer pays but the redirect is lost, then they come back and try again.
    assert client.post(path(first), data={"action": "lost_redirect"}).status_code == 200
    again = choose(client, s["id"])
    assert again.headers["location"] == f"/checkout/{s['id']}"  # we asked the provider first: already paid
    assert "Payment successful" in client.get(again.headers["location"]).text
    # A second payment that slips through anyway (a late rescue) is announced, not hidden.
    with rt.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO orders (workspace_id, mode, order_id, gateway, amount_poisha, status, checkout_session_id)
            VALUES (:w, 'sandbox', 'COEXTRA', 'simulator', 125000, 'pending', :s)"""),
            {"w": dash.workspace_id, "s": s["id"]},
        )
    from bdpay.core import Status

    rt.pay(helpers.Scope(dash.workspace_id, "sandbox")).store.transition("COEXTRA", Status.PAID, {})
    assert events(client, key) == [
        "order.paid",
        "checkout.session.completed",
        "order.paid",
        "checkout.session.extra_payment",
    ]


def test_expiry_cancel_and_validation(client, dash, rt):
    key = dash.api_key()
    s = session(client, key, cancel_url="https://shop.example/cart?s={session_id}", expires_in_minutes=5)
    cancelled = client.post(f"/checkout/{s['id']}/cancel", follow_redirects=False)
    assert cancelled.status_code == 303
    page = client.get(cancelled.headers["location"])
    assert "Checkout cancelled" in page.text and f"https://shop.example/cart?s={s['id']}" in page.text
    assert choose(client, s["id"]).headers["location"] == f"/checkout/{s['id']}"
    s2 = session(client, key)
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE checkout_sessions SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": s2["id"]}
        )
    assert "This checkout has expired" in client.get(f"/checkout/{s2['id']}").text
    from bdpay_cloud.services import checkout

    assert checkout.expire_due(rt) >= 1
    assert client.get(f"/v1/checkout/sessions/{s2['id']}", headers=bearer(key)).json()["status"] == "expired"
    assert (
        client.post(f"/v1/checkout/sessions/{session(client, key)['id']}/expire", headers=bearer(key)).json()["status"]
        == "cancelled"
    )
    for bad in (
        {"amount": "0"},
        {"amount": "10", "gateways": ["stripe"]},
        {"amount": "10", "success_url": "javascript:x"},
        {"amount": "10", "expires_in_minutes": 1},
        {"amount": "10", "locale": "fr"},
    ):
        assert client.post("/v1/checkout/sessions", json=bad, headers=bearer(key)).status_code == 400, bad
    dash.patch(f"{dash.env()}/gateways/simulator", {"enabled": False})
    r = client.post("/v1/checkout/sessions", json={"amount": "10"}, headers=bearer(key))
    assert r.status_code == 400 and "no payment method is enabled" in r.json()["detail"]["message"]


def test_provider_trouble_is_explained_to_the_customer_without_details(client, dash, provider):
    import httpx

    def down(req):
        raise httpx.ConnectError("boom", request=req)

    provider(down)
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    s = session(client, dash.api_key())
    r = choose(client, s["id"], "bkash")
    assert r.status_code == 200 and "not responding" in r.text and "ConnectError" not in r.text
    assert "Choose how to pay" in r.text  # they can pick another method


def test_payment_link_with_customer_amount_and_details(client, dash):
    r = dash.post(
        f"{dash.env()}/payment-links",
        {"title": "Monthly tuition", "description": "Class 8, section B", "min_amount": "500", "max_amount": "5000"},
    )
    assert r.status_code == 200, r.text
    link = r.json()
    assert (
        link["url"] == f"https://pay.test/pay/{link['id']}"
        and link["amount"] is None
        and link["collect"] == ["name", "phone"]
    )
    page = client.get(f"/pay/{link['id']}")
    for expected in (
        "Monthly tuition",
        "Class 8, section B",
        "Amount (BDT)",
        "Between ৳500.00 and ৳5,000.00",
        "Your name",
        "Mobile number",
        "Test payment",
    ):
        assert expected in page.text, expected
    post = lambda **data: client.post(
        f"/pay/{link['id']}", data={"gateway": "simulator", **data}, follow_redirects=False
    )
    assert "Enter an amount between 500.00 and 5000.00" in post(amount="50", name="Karim", phone="01800000000").text
    missing = post(amount="1500", name="", phone="")
    assert missing.status_code == 400 and "Please enter your name and phone" in missing.text
    assert 'value="1500"' in missing.text  # what they typed is kept
    go = post(amount="1,500", name="Karim", phone="01800000000")
    assert go.status_code == 303 and "/simulator/sim_" in go.headers["location"]
    back = simulate(client, go.headers["location"])
    assert "Payment successful" in client.get(back.headers["location"]).text
    key = dash.api_key()
    paid = client.get("/v1/orders?status=paid", headers=bearer(key)).json()["data"][0]
    assert paid["amount"] == "1500.00" and paid["source"] == "link"
    assert paid["customer"] == {"name": "Karim", "phone": "01800000000", "product": "Monthly tuition"}
    assert paid["metadata"]["payment_link"] == link["id"]
    assert dash.get(f"{dash.env()}/payment-links").json()["data"][0]["paid_count"] == 1


def test_fixed_amount_link_limits_and_deactivation(client, dash):
    link = dash.post(
        f"{dash.env()}/payment-links", {"title": "Eid hamper", "amount": "2500", "collect": [], "max_payments": 1}
    ).json()
    page = client.get(f"/pay/{link['id']}").text
    assert "৳2,500.00" in page and "Amount (BDT)" not in page and "Your name" not in page
    go = client.post(f"/pay/{link['id']}", data={"gateway": "simulator", "amount": "1"}, follow_redirects=False)
    simulate(client, go.headers["location"])  # the typed amount is ignored: 2500 it is
    assert dash.get(f"{dash.env()}/orders?status=paid").json()["data"][0]["amount"] == "2500.00"
    closed = client.get(f"/pay/{link['id']}")
    assert closed.status_code == 410 and "This payment link is closed" in closed.text  # one payment only
    other = dash.post(f"{dash.env()}/payment-links", {"title": "Open link", "amount": "10"}).json()
    assert dash.patch(f"{dash.env()}/payment-links/{other['id']}", {"active": False}).json()["active"] is False
    assert client.get(f"/pay/{other['id']}").status_code == 410
    assert client.get("/pay/plink_nope").status_code == 404
    assert (
        dash.post(f"{dash.env()}/payment-links", {"title": "Bad", "min_amount": "500", "max_amount": "5"}).status_code
        == 400
    )
    assert dash.post(f"{dash.env()}/payment-links", {"title": "Bad", "collect": ["nid"]}).status_code == 400


def test_dashboard_test_payment(client, dash):
    r = dash.post(f"{dash.env()}/orders", {"gateway": "simulator", "amount": "75.50"})
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["order_id"]
    assert re.fullmatch(r"DB[0-9A-Z]{16}", order_id) and r.json()["order"]["source"] == "dashboard"
    simulate(client, r.json()["redirect_url"])
    detail = dash.get(f"{dash.env()}/orders/{order_id}").json()
    assert detail["status"] == "paid" and detail["refund_capability"] == "partial"
    assert [e["type"] for e in detail["webhook_events"]] == ["order.paid"]
    refund = dash.post(f"{dash.env()}/orders/{order_id}/refund", {"amount": "5.50", "reason": "test"})
    assert refund.json()["order"]["refunded"] == "5.50"
    overview = dash.get(f"{dash.env()}/overview").json()
    assert overview["stats"]["paid_count"] == 1 and overview["stats"]["paid_amount"] == "75.50"
    assert overview["stats"]["success_rate"] == 1.0 and len(overview["stats"]["daily"]) == 30
    assert overview["stats"]["by_gateway"][0]["gateway"] == "simulator"
    actions = [e["action"] for e in dash.get(f"/api/workspaces/{dash.slug}/audit-log").json()["data"]]
    assert "order.started_from_dashboard" in actions and "order.refunded" in actions
