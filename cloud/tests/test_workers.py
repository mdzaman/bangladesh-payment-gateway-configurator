"""Background work: reconciliation, the worker loop, mail, housekeeping."""

import httpx
from sqlalchemy import text

from bdpay_cloud.services import mailer
from bdpay_cloud.workers import housekeeping, reconcile
from bdpay_cloud.workers.runner import Worker

import helpers
from helpers import BKASH, FakeBkash, bearer


def start(client, key, order_id, gateway="simulator", amount="100"):
    client.post("/v1/orders", json={"order_id": order_id, "amount": amount, "gateway": gateway}, headers=bearer(key))
    return client.post(f"/v1/orders/{order_id}/pay", headers=bearer(key)).json()["redirect_url"]


def make_due(rt, workspace_id):
    with rt.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE orders SET next_check_at = now() - interval '1 second' "
                "WHERE workspace_id = :w AND status = 'pending'"
            ),
            {"w": workspace_id},
        )


def order_row(rt, workspace_id, order_id):
    with rt.engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT status, check_count, extract(epoch FROM next_check_at - now())::float8 AS wait "
                "FROM orders WHERE workspace_id = :w AND order_id = :o"
            ),
            {"w": workspace_id, "o": order_id},
        ).one()


def test_a_payment_whose_redirect_was_lost_is_found(client, dash, rt):
    key = dash.api_key()
    url = start(client, key, "LOST1").replace("https://pay.test", "")
    page = client.post(url, data={"action": "lost_redirect"})  # paid, tab closed, no callback
    assert "redirect was dropped on purpose" in page.text
    row = order_row(rt, dash.workspace_id, "LOST1")
    assert row.status == "pending" and 100 < row.wait <= 120  # first check is scheduled ~2 min out
    assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == []  # not due yet
    make_due(rt, dash.workspace_id)
    assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == [("LOST1", "paid")]
    assert order_row(rt, dash.workspace_id, "LOST1").wait is None  # settled: no further checks
    events = [e["type"] for e in client.get("/v1/events", headers=bearer(key)).json()["data"]]
    assert events == ["order.paid"]  # the merchant is told, once


def test_still_pending_orders_back_off_then_expire(client, dash, rt):
    key = dash.api_key()
    start(client, key, "SLOW1")
    waits = []
    for _ in range(4):
        make_due(rt, dash.workspace_id)
        assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == [("SLOW1", "pending")]
        waits.append(round(order_row(rt, dash.workspace_id, "SLOW1").wait / 60))
    assert waits == [3, 5, 5, 10]  # dense at first, then sparser
    kinds = [e["kind"] for e in client.get("/v1/orders/SLOW1", headers=bearer(key)).json()["events"]]
    assert kinds.count("still_pending") == 4
    with rt.engine.begin() as conn:  # a day goes by
        conn.execute(
            text("UPDATE orders SET created_at = now() - interval '25 hours' WHERE workspace_id = :w"),
            {"w": dash.workspace_id},
        )
    make_due(rt, dash.workspace_id)
    assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == [("SLOW1", "expired")]
    assert client.get("/v1/events", headers=bearer(key)).json()["data"][0]["type"] == "order.expired"
    make_due(rt, dash.workspace_id)
    assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == []


def test_a_late_payment_rescues_an_expired_order(client, dash, rt):
    key = dash.api_key()
    url = start(client, key, "LATE1")
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE orders SET created_at = now() - interval '25 hours' WHERE workspace_id = :w"),
            {"w": dash.workspace_id},
        )
    make_due(rt, dash.workspace_id)
    assert reconcile.run_once(rt, only_workspace=dash.workspace_id) == [("LATE1", "expired")]
    client.post(url.replace("https://pay.test", ""), data={"action": "lost_redirect"})  # paid after all
    assert client.post("/v1/orders/LATE1/requery", headers=bearer(key)).json()["status"] == "paid"


def test_two_workers_never_check_the_same_order(client, dash, rt):
    key = dash.api_key()
    for i in range(6):
        start(client, key, f"SKIP{i}")
    make_due(rt, dash.workspace_id)
    first = reconcile.claim(rt, limit=4, only_workspace=dash.workspace_id)
    second = reconcile.claim(rt, limit=4, only_workspace=dash.workspace_id)
    ids = [d.order_id for d in first] + [d.order_id for d in second]
    assert len(first) == 4 and len(second) == 2 and len(set(ids)) == 6


def test_provider_errors_during_reconcile_do_not_stop_the_batch(client, dash, rt, provider):
    fake = provider(FakeBkash())
    dash.put(f"{dash.env()}/gateways/bkash", {"values": BKASH})
    key = dash.api_key()
    start(client, key, "ERRA", "bkash")
    start(client, key, "ERRB", "simulator")

    def down(req):
        raise httpx.ConnectError("no route", request=req)

    provider(down)
    make_due(rt, dash.workspace_id)
    results = dict(reconcile.run_once(rt, only_workspace=dash.workspace_id))
    assert results == {"ERRA": "error:PROVIDER_UNAVAILABLE", "ERRB": "pending"}
    assert order_row(rt, dash.workspace_id, "ERRA").status == "pending"  # it will simply be asked again
    provider(fake)
    make_due(rt, dash.workspace_id)
    assert dict(reconcile.run_once(rt, only_workspace=dash.workspace_id))["ERRA"] == "paid"


def test_worker_tick_runs_every_queue_and_reports_a_heartbeat(client, dash, rt):
    key = dash.api_key()
    dash.post(f"{dash.env()}/webhooks", {"url": "https://merchant.example/hook"})
    assert dash.get(f"{dash.env()}/worker").json()["running"] in (True, False)
    url = start(client, key, "TICK1")
    client.post(url.replace("https://pay.test", ""), data={"action": "lost_redirect"})
    make_due(rt, dash.workspace_id)
    seen = []
    worker = Worker(rt, transport=httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(200))[1]))
    try:
        assert worker.tick() >= 1  # reconcile finds the payment...
        assert worker.tick() >= 1  # ...and the next pass delivers its event
    finally:
        worker.close()
    assert any(b"order.paid" in r.content and b"TICK1" in r.content for r in seen)
    assert dash.get(f"{dash.env()}/worker").json()["running"] is True
    items = {i["id"]: i["state"] for i in dash.get(f"{dash.env()}/checklist").json()["items"]}
    assert items["worker"] == "done" and items["webhook"] == "done"


def test_email_outbox_retries_then_gives_up(rt, monkeypatch):
    to = f"mail-{helpers.rand_b32(8)}@example.com"
    from bdpay_cloud.db import session_scope

    with session_scope(rt.sessions) as db:
        mailer.send_password_reset(db, rt.settings, to, "abc123")
    sent = []
    monkeypatch.setattr(mailer, "deliver", lambda settings, *a: sent.append(a))
    with rt.engine.begin() as conn:  # keep other tests' mail out of the way
        conn.execute(
            text("UPDATE outbound_emails SET status = 'sent' WHERE to_email <> :to AND status = 'pending'"), {"to": to}
        )
    assert mailer.send_pending(rt) == 1 and sent[0][0] == to and "reset-password?token=abc123" in sent[0][2]
    assert "<a href=" in sent[0][3]
    assert mailer.send_pending(rt) == 0

    with session_scope(rt.sessions) as db:
        mailer.send_password_reset(db, rt.settings, to, "def456")

    def broken(*a):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(mailer, "deliver", broken)
    for attempt in range(1, mailer.MAX_ATTEMPTS + 1):
        assert mailer.send_pending(rt) == 1
        with rt.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status, attempts, last_error FROM outbound_emails WHERE to_email = :to "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"to": to},
            ).one()
            conn.execute(text("UPDATE outbound_emails SET next_attempt_at = now() WHERE status = 'pending'"))
        assert row.attempts == attempt and "smtp down" in row.last_error
    assert row.status == "failed" and mailer.send_pending(rt) == 0


def test_housekeeping(rt, dash, client):
    key = dash.api_key()
    r = client.post("/v1/checkout/sessions", json={"amount": "10"}, headers=bearer(key)).json()
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE checkout_sessions SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": r["id"]}
        )
        conn.execute(
            text(
                "INSERT INTO provider_tokens (key, value_enc, expires_at) VALUES "
                "('old-' || :k, 'x', now() - interval '1 hour')"
            ),
            {"k": helpers.rand_b32(8)},
        )
        conn.execute(
            text(
                "INSERT INTO idempotency_keys (workspace_id, mode, key, request_hash, created_at) VALUES "
                "(:w, 'sandbox', 'stale', 'h', now() - interval '3 days')"
            ),
            {"w": dash.workspace_id},
        )
    done = housekeeping.run(rt)
    assert done["sessions_expired"] >= 1 and done["tokens"] >= 1 and done["idempotency"] >= 1
    assert client.get(f"/v1/checkout/sessions/{r['id']}", headers=bearer(key)).json()["status"] == "expired"
