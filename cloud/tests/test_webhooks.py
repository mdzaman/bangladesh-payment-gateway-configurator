"""Webhook endpoints and delivery: signed, at least once, with backoff."""

import json

import httpx
import pytest
from sqlalchemy import text

from bdpay_cloud.services.webhooks import MAX_ATTEMPTS, RETRY_SCHEDULE, Dispatcher, sign, verify_signature

import helpers
from helpers import bearer, simulate


class Receiver:
    """A merchant's webhook endpoint."""

    def __init__(self, status=200):
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.status == "timeout":
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(self.status, text="ok" if self.status == 200 else "nope")

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def endpoint(dash, url="https://merchant.example/hooks/bdpay", **extra):
    r = dash.post(f"{dash.env()}/webhooks", {"url": url, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def pay(client, key, order_id, action="approve", amount="100"):
    client.post(
        "/v1/orders", json={"order_id": order_id, "amount": amount, "gateway": "simulator"}, headers=bearer(key)
    )
    return simulate(
        client, client.post(f"/v1/orders/{order_id}/pay", headers=bearer(key)).json()["redirect_url"], action
    )


def deliver(rt, dash, receiver):
    d = Dispatcher(rt, transport=httpx.MockTransport(receiver))
    try:
        return d.run_once(only_workspace=dash.workspace_id)
    finally:
        d.close()


def test_signature_scheme():
    header = sign("whsec_abc", 1700000000, b'{"a":1}')
    assert header.startswith("t=1700000000,v1=") and len(header.split("v1=")[1]) == 64
    assert verify_signature("whsec_abc", header, b'{"a":1}', now=1700000100)
    assert not verify_signature("whsec_abc", header, b'{"a":2}', now=1700000100)  # body changed
    assert not verify_signature("whsec_other", header, b'{"a":1}', now=1700000100)  # wrong secret
    assert not verify_signature("whsec_abc", header, b'{"a":1}', now=1700000000 + 301)  # replayed too late
    assert not verify_signature("whsec_abc", "garbage", b'{"a":1}')


def test_secret_is_shown_once_and_stored_encrypted(dash, rt):
    created = endpoint(dash)
    secret = created["secret"]
    assert secret.startswith("whsec_") and created["event_types"] == ["*"]
    listing = dash.get(f"{dash.env()}/webhooks")
    assert secret not in listing.text and "secret" not in listing.json()["data"][0]
    with rt.engine.connect() as conn:
        stored = conn.execute(
            text("SELECT secret_enc FROM webhook_endpoints WHERE id = :i"), {"i": created["id"]}
        ).scalar()
    assert secret not in stored and stored.startswith("v1.")
    rotated = dash.post(f"{dash.env()}/webhooks/{created['id']}/rotate-secret").json()["secret"]
    assert rotated != secret and rotated.startswith("whsec_")


def test_a_paid_order_is_delivered_signed(dash, client, rt):
    hook = endpoint(dash)
    key = dash.api_key()
    pay(client, key, "WH1", amount="1250.50")
    receiver = Receiver()
    assert deliver(rt, dash, receiver) == 1
    req = receiver.requests[0]
    assert str(req.url) == "https://merchant.example/hooks/bdpay" and req.headers["content-type"] == "application/json"
    assert verify_signature(hook["secret"], req.headers["bdpay-signature"], req.content)
    event = json.loads(req.content)
    assert (
        event["type"] == "order.paid" == req.headers["bdpay-event-type"]
        and event["id"] == req.headers["bdpay-event-id"]
    )
    assert event["mode"] == "sandbox" and event["data"]["previous_status"] == "pending"
    order = event["data"]["order"]
    assert order["order_id"] == "WH1" and order["amount"] == "1250.50" and order["status"] == "paid"
    # What the webhook says is exactly what the API says.
    assert order == {k: v for k, v in client.get("/v1/orders/WH1", headers=bearer(key)).json().items() if k != "events"}
    delivery = dash.get(f"{dash.env()}/webhook-deliveries").json()["data"][0]
    assert delivery["status"] == "succeeded" and delivery["attempts"] == 1 and delivery["last_status_code"] == 200
    assert deliver(rt, dash, receiver) == 0  # nothing left, nothing re-sent
    assert client.get(f"/v1/events/{event['id']}", headers=bearer(key)).json() == event
    assert client.get("/v1/events?type=order.paid", headers=bearer(key)).json()["data"] == [event]


def test_every_final_state_and_refund_emits_its_event(dash, client, rt):
    endpoint(dash)
    key = dash.api_key()
    pay(client, key, "EV1", "decline")
    pay(client, key, "EV2", "cancel")
    pay(client, key, "EV3", "wrong_amount")
    pay(client, key, "EV4", "approve")
    client.post("/v1/orders/EV4/refund", json={"amount": "40"}, headers=bearer(key))
    client.post("/v1/orders/EV4/refund", json={}, headers=bearer(key))
    receiver = Receiver()
    deliver(rt, dash, receiver)
    types = sorted(b["type"] for b in receiver.bodies)
    assert types == sorted(
        [
            "order.failed",
            "order.cancelled",
            "order.needs_review",
            "order.paid",
            "order.partially_refunded",
            "order.refunded",
        ]
    )
    review = next(b for b in receiver.bodies if b["type"] == "order.needs_review")
    assert review["data"]["problems"] == ["amount 9900 != 10000"]
    refunded = next(b for b in receiver.bodies if b["type"] == "order.refunded")
    assert refunded["data"]["order"]["refunded"] == "100.00" and refunded["data"]["refund"]["ref"].startswith("SIMRF")


def test_event_type_filter_and_disabled_endpoints(dash, client, rt):
    only_paid = endpoint(dash, url="https://merchant.example/paid", event_types=["order.paid"])
    everything = endpoint(dash, url="https://merchant.example/all")
    off = endpoint(dash, url="https://merchant.example/off")
    dash.patch(f"{dash.env()}/webhooks/{off['id']}", {"enabled": False})
    key = dash.api_key()
    pay(client, key, "F1", "decline")
    pay(client, key, "F2", "approve")
    receiver = Receiver()
    deliver(rt, dash, receiver)
    seen = sorted((str(r.url).rsplit("/", 1)[1], json.loads(r.content)["type"]) for r in receiver.requests)
    assert seen == [("all", "order.failed"), ("all", "order.paid"), ("paid", "order.paid")]
    assert (
        dash.post(f"{dash.env()}/webhooks", {"url": "https://m.example/x", "event_types": ["nope"]}).status_code == 400
    )
    assert only_paid["event_types"] == ["order.paid"] and everything["event_types"] == ["*"]


def test_failures_back_off_and_eventually_give_up(dash, client, rt):
    hook = endpoint(dash)
    key = dash.api_key()
    pay(client, key, "RTY1")
    receiver = Receiver(status=500)

    def row():
        with rt.engine.connect() as conn:
            return conn.execute(
                text("""
                SELECT status, attempts, last_status_code, last_error,
                       extract(epoch FROM next_attempt_at - now())::float8 AS wait
                  FROM webhook_deliveries WHERE endpoint_id = :e"""),
                {"e": hook["id"]},
            ).one()

    def make_due():
        with rt.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE webhook_deliveries SET next_attempt_at = now() WHERE endpoint_id = :e "
                    "AND status = 'pending'"
                ),
                {"e": hook["id"]},
            )

    for attempt in range(1, MAX_ATTEMPTS):
        assert deliver(rt, dash, receiver) == 1
        r = row()
        assert (r.status, r.attempts, r.last_status_code, r.last_error) == ("pending", attempt, 500, "HTTP 500")
        expected = RETRY_SCHEDULE[attempt - 1]
        assert expected * 0.85 <= r.wait <= expected * 1.15, (attempt, r.wait)  # schedule with jitter
        assert deliver(rt, dash, receiver) == 0  # not due yet
        make_due()
    assert deliver(rt, dash, receiver) == 1
    final = row()
    assert final.status == "failed" and final.attempts == MAX_ATTEMPTS and final.wait is None
    assert len(receiver.requests) == MAX_ATTEMPTS
    assert [r.headers["bdpay-delivery-attempt"] for r in receiver.requests] == [
        str(i) for i in range(1, MAX_ATTEMPTS + 1)
    ]
    assert dash.get(f"{dash.env()}/webhooks").json()["data"][0]["consecutive_failures"] == MAX_ATTEMPTS
    # The merchant fixes their server and presses "resend".
    delivery_id = dash.get(f"{dash.env()}/webhook-deliveries?status=failed").json()["data"][0]["id"]
    assert dash.post(f"{dash.env()}/webhook-deliveries/{delivery_id}/redeliver").status_code == 200
    ok = Receiver()
    assert deliver(rt, dash, ok) == 1 and ok.bodies[0]["type"] == "order.paid"
    assert dash.get(f"{dash.env()}/webhooks").json()["data"][0]["consecutive_failures"] == 0


def test_timeouts_and_redirects_count_as_failures(dash, client, rt):
    endpoint(dash)
    key = dash.api_key()
    pay(client, key, "TO1")
    assert deliver(rt, dash, Receiver(status="timeout")) == 1
    d = dash.get(f"{dash.env()}/webhook-deliveries").json()["data"][0]
    assert d["status"] == "pending" and d["last_error"] == "timed out"
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE webhook_deliveries SET next_attempt_at = now() WHERE workspace_id = :w"),
            {"w": dash.workspace_id},
        )
    assert deliver(rt, dash, Receiver(status=302)) == 1  # redirects are not followed
    assert dash.get(f"{dash.env()}/webhook-deliveries").json()["data"][0]["last_error"] == "HTTP 302"


def test_a_crashed_worker_does_not_lose_a_delivery(dash, client, rt):
    endpoint(dash)
    pay(client, dash.api_key(), "LEASE1")
    d = Dispatcher(rt, transport=httpx.MockTransport(Receiver()))
    claimed = d.claim(only_workspace=dash.workspace_id)
    assert len(claimed) == 1 and d.claim(only_workspace=dash.workspace_id) == []  # leased, not visible to others
    d.close()  # the worker dies before sending
    with rt.engine.begin() as conn:  # ...and the lease runs out
        conn.execute(text("UPDATE webhook_deliveries SET next_attempt_at = now() WHERE id = :i"), {"i": claimed[0].id})
    receiver = Receiver()
    assert deliver(rt, dash, receiver) == 1 and receiver.bodies[0]["data"]["order"]["order_id"] == "LEASE1"


def test_test_event_and_endpoint_management(dash, rt):
    hook = endpoint(dash, description="orders service")
    assert dash.post(f"{dash.env()}/webhooks/{hook['id']}/test").json()["event_id"].startswith("evt_")
    receiver = Receiver()
    deliver(rt, dash, receiver)
    assert receiver.bodies[0]["type"] == "ping"
    r = dash.patch(
        f"{dash.env()}/webhooks/{hook['id']}", {"url": "https://merchant.example/v2", "event_types": ["order.paid"]}
    )
    assert r.json()["url"] == "https://merchant.example/v2" and r.json()["event_types"] == ["order.paid"]
    assert dash.delete(f"{dash.env()}/webhooks/{hook['id']}").status_code == 200
    assert dash.get(f"{dash.env()}/webhooks").json()["data"] == []
    assert dash.delete(f"{dash.env()}/webhooks/{hook['id']}").status_code == 404
    for _ in range(10):
        endpoint(dash, url=f"https://merchant.example/{helpers.rand_b32(5)}")
    assert dash.post(f"{dash.env()}/webhooks", {"url": "https://merchant.example/one-too-many"}).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/hook",
        "http://localhost/hook",
        "https://10.0.0.8/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::ffff:127.0.0.1]/x",
        "gopher://merchant.example/",
        "https://u:p@merchant.example/",
    ],
)
def test_webhook_urls_cannot_point_inside_the_network(dash, url):
    r = dash.post(f"{dash.env()}/webhooks", {"url": url})
    assert r.status_code == 400 and r.json()["detail"]["message"].startswith("Endpoint URL")


def test_delivery_itself_is_guarded_even_if_a_private_url_got_into_the_database(dash, client, rt):
    hook = endpoint(dash)
    with rt.engine.begin() as conn:  # as if DNS changed after it was saved
        conn.execute(
            text("UPDATE webhook_endpoints SET url = 'http://127.0.0.1:9/steal' WHERE id = :i"), {"i": hook["id"]}
        )
    pay(client, dash.api_key(), "SSRF1")
    d = Dispatcher(rt)  # the real, guarded transport
    try:
        assert d.run_once(only_workspace=dash.workspace_id) == 1
    finally:
        d.close()
    delivery = dash.get(f"{dash.env()}/webhook-deliveries").json()["data"][0]
    assert delivery["status"] == "pending" and "private, local or reserved" in delivery["last_error"]


def test_live_webhooks_need_https(dash):
    dash.go_live()
    assert dash.post(f"{dash.env('live')}/webhooks", {"url": "http://merchant.example/hook"}).status_code == 400
    assert dash.post(f"{dash.env()}/webhooks", {"url": "http://merchant.example/hook"}).status_code == 200
    assert dash.post(f"{dash.env('live')}/webhooks", {"url": "https://merchant.example/hook"}).status_code == 200
