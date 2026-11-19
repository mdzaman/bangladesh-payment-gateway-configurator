"""The tenant engine below the HTTP layer: isolation, concurrency, tokens, the simulator."""

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import text

from bdpay.core import BdPayError, Status
from bdpay_cloud.context import Actor
from bdpay_cloud.db import session_scope
from bdpay_cloud.engine.drivers import credential_namespace
from bdpay_cloud.services import gateways

import helpers
from helpers import BKASH, FakeBkash


def configure(rt, env, gateway, values, enabled=True):
    with session_scope(rt.sessions) as db:
        return gateways.save(
            db, rt, env, gateway, values=values, enabled=enabled, clear=None, actor=Actor.system("test"), user_id=None
        )


def count(rt, sql, **params):
    with rt.engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def test_same_order_id_in_two_tenants_never_meet(rt):
    a, b = helpers.new_tenant(rt), helpers.new_tenant(rt)
    pay_a, pay_b = rt.pay(a.sandbox), rt.pay(b.sandbox)
    pay_a.create_order("INV1001", "100", "simulator")
    pay_b.create_order("INV1001", "999", "simulator")  # same id, other tenant: not a duplicate
    assert pay_a.store.get("INV1001").amount == "100.00" and pay_b.store.get("INV1001").amount == "999.00"
    url_a = pay_a.start_payment("INV1001")
    assert pay_b.store.get("INV1001").status == Status.CREATED
    # Tenant B's signed link does not open tenant A's order, and the other way round.
    assert pay_a.settings.secret != pay_b.settings.secret
    token_b = pay_b.callback_urls(pay_b.store.get("INV1001")).success.split("/")[-2]
    with pytest.raises(BdPayError) as e:
        pay_a.handle_callback("simulator", "INV1001", token_b, "success", {})
    assert e.value.code == "SIGNATURE_INVALID"
    assert url_a.startswith("https://pay.test/simulator/sim_")
    # Sandbox and live of the same workspace are separate worlds too.
    assert rt.pay(a.live).store.get("INV1001") is None


def test_gateway_must_be_enabled_for_new_payments_but_in_flight_ones_still_settle(rt, tenant, provider):
    fake = provider(FakeBkash())
    pay = rt.pay(tenant.sandbox)
    with pytest.raises(BdPayError) as e:
        pay.create_order("INV1", "250", "bkash")
    assert e.value.code == "CONFIG" and "not enabled" in e.value.message
    configure(rt, tenant.sandbox, "bkash", helpers.own_bkash())
    pay = rt.pay(tenant.sandbox)
    pay.create_order("INV1", "250", "bkash")
    pay.start_payment("INV1")
    # The merchant switches bKash off while the customer is still paying.
    with session_scope(rt.sessions) as db:
        gateways.set_enabled(db, rt, tenant.sandbox, "bkash", False, Actor.system())
    pay = rt.pay(tenant.sandbox)
    with pytest.raises(BdPayError):
        pay.create_order("INV2", "10", "bkash")
    _, gw, oid, tok, outcome = helpers.callback_parts(pay.callback_urls(pay.store.get("INV1")).success)
    paid = pay.handle_callback(
        gw, oid, tok, outcome, {"paymentID": pay.store.get("INV1").provider_payment_id, "status": "success"}
    )
    assert paid.status == Status.PAID and fake.grants == 1


def test_start_payment_opens_one_provider_session_even_when_called_at_once(rt, tenant, provider):
    fake = provider(FakeBkash())
    fake.create_delay = 0.25
    configure(rt, tenant.sandbox, "bkash", BKASH)
    rt.pay(tenant.sandbox).create_order("RACE1", "500", "bkash")
    barrier = threading.Barrier(6)

    def start():
        barrier.wait()
        try:
            return rt.pay(tenant.sandbox).start_payment("RACE1")
        except BdPayError as e:
            return e.code

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: start(), range(6)))
    urls = {r for r in results if r.startswith("https://")}
    assert fake.creates == 1 and len(urls) == 1  # one bKash payment, not six
    assert set(results) - urls <= {"DUPLICATE"}  # the rest were told to retry
    # Asking again later returns the same checkout URL instead of opening a second payment.
    assert rt.pay(tenant.sandbox).start_payment("RACE1") in urls and fake.creates == 1


def test_failed_start_can_be_retried(rt, tenant, provider):
    state = {"fail": True}
    fake = FakeBkash()

    def flaky(req):
        if req.url.path.endswith("/create") and state["fail"]:
            return httpx.Response(200, json={"statusCode": "2001", "statusMessage": "Invalid amount"})
        return fake(req)

    provider(flaky)
    configure(rt, tenant.sandbox, "bkash", BKASH)
    pay = rt.pay(tenant.sandbox)
    pay.create_order("RETRY1", "50", "bkash")
    with pytest.raises(BdPayError) as e:
        pay.start_payment("RETRY1")
    assert e.value.code == "PROVIDER_DECLINED" and pay.store.get("RETRY1").status == Status.CREATED
    assert "PROVIDER_DECLINED" in count(
        rt,
        "SELECT last_error FROM gateway_configs WHERE workspace_id = :w AND gateway = 'bkash'",
        w=tenant.workspace_id,
    )
    state["fail"] = False
    assert pay.start_payment("RETRY1").startswith("https://bkash.test/")
    assert (
        count(
            rt,
            "SELECT status FROM gateway_configs WHERE workspace_id = :w AND gateway = 'bkash'",
            w=tenant.workspace_id,
        )
        == "verified"
    )


def test_duplicate_callbacks_on_many_replicas_produce_exactly_one_event(rt, tenant, provider):
    provider(FakeBkash())
    configure(rt, tenant.sandbox, "bkash", BKASH)
    pay = rt.pay(tenant.sandbox)
    pay.create_order("DUP1", "250", "bkash")
    pay.start_payment("DUP1")
    order = pay.store.get("DUP1")
    _, gw, oid, tok, outcome = helpers.callback_parts(pay.callback_urls(order).success)
    barrier = threading.Barrier(8)

    def hit(_):
        barrier.wait()
        return (
            rt.pay(tenant.sandbox)
            .handle_callback(gw, oid, tok, outcome, {"paymentID": order.provider_payment_id, "status": "success"})
            .status
        )

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(hit, range(8))) == {Status.PAID}
    assert (
        count(rt, "SELECT count(*) FROM events WHERE workspace_id = :w AND type = 'order.paid'", w=tenant.workspace_id)
        == 1
    )
    kinds = [e["kind"] for e in pay.store.events("DUP1")]
    assert kinds.count("transition") == 2  # created->pending, pending->paid; nothing twice


def test_amount_tamper_ends_in_needs_review_with_an_event(rt, tenant, provider):
    provider(FakeBkash(amount="1.00"))
    configure(rt, tenant.sandbox, "bkash", BKASH)
    pay = rt.pay(tenant.sandbox)
    pay.create_order("TAMPER1", "5000", "bkash")
    pay.start_payment("TAMPER1")
    order = pay.store.get("TAMPER1")
    _, gw, oid, tok, outcome = helpers.callback_parts(pay.callback_urls(order).success)
    result = pay.handle_callback(gw, oid, tok, outcome, {"paymentID": order.provider_payment_id, "status": "success"})
    assert result.status == Status.NEEDS_REVIEW
    payload = count(
        rt, "SELECT payload FROM events WHERE workspace_id = :w AND type = 'order.needs_review'", w=tenant.workspace_id
    )
    assert payload["data"]["problems"] == ["amount 100 != 500000"]
    assert (
        count(rt, "SELECT count(*) FROM events WHERE workspace_id = :w AND type = 'order.paid'", w=tenant.workspace_id)
        == 0
    )


def test_provider_tokens_are_cached_encrypted_and_shared_only_by_identical_credentials(rt, provider):
    fake = provider(FakeBkash())
    a, b, c = helpers.new_tenant(rt), helpers.new_tenant(rt), helpers.new_tenant(rt)
    app_key = "shared-app-" + helpers.rand_b32(6)
    same = {**BKASH, "BKASH_APP_KEY": app_key}
    configure(rt, a.sandbox, "bkash", same)
    configure(rt, b.sandbox, "bkash", same)  # the very same merchant account
    configure(rt, c.sandbox, "bkash", {**same, "BKASH_PASSWORD": "a-different-password"})
    for t, oid in ((a, "T1"), (a, "T2"), (b, "T3")):
        pay = rt.pay(t.sandbox)
        pay.create_order(oid, "10", "bkash")
        pay.start_payment(oid)
    assert fake.grants == 1  # one token for a, a again, and b
    # Knowing the app key is not enough: different credentials never see that token.
    pay = rt.pay(c.sandbox)
    pay.create_order("T4", "10", "bkash")
    pay.start_payment("T4")
    assert fake.grants == 2
    assert credential_namespace("sandbox", "bkash", same) != credential_namespace("live", "bkash", same)
    stored = count(rt, "SELECT string_agg(value_enc, ' ') FROM provider_tokens")
    assert "TOKEN" not in stored and stored.startswith("v1.")


def test_token_refresh_is_single_flight(rt, tenant):
    calls = []
    barrier = threading.Barrier(6)
    cache = rt.tokens.scoped("single-flight-" + helpers.rand_b32(8))

    def fetch():
        calls.append(1)
        import time

        time.sleep(0.2)
        return "tok", 3600

    def get(_):
        barrier.wait()
        return cache.get_or_fetch("k", fetch)

    with ThreadPoolExecutor(6) as pool:
        assert set(pool.map(get, range(6))) == {"tok"}
    assert len(calls) == 1
    cache.invalidate("k")
    assert cache.get_or_fetch("k", lambda: ("tok2", 3600)) == "tok2"


def test_secrets_are_encrypted_at_rest_and_bound_to_their_tenant(rt, tenant):
    configure(rt, tenant.sandbox, "bkash", BKASH)
    with rt.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT config, secrets_enc, secret_hints FROM gateway_configs "
                "WHERE workspace_id = :w AND gateway = 'bkash'"
            ),
            {"w": tenant.workspace_id},
        ).one()
    blob = f"{row.config} {row.secrets_enc} {row.secret_hints}"
    assert BKASH["BKASH_APP_SECRET"] not in blob and BKASH["BKASH_PASSWORD"] not in blob
    assert row.config["BKASH_APP_KEY"] == "app" and "BKASH_APP_SECRET" not in row.config
    # Copy tenant A's ciphertext into tenant B's row: it must not decrypt there.
    other = helpers.new_tenant(rt)
    configure(rt, other.sandbox, "bkash", BKASH)
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE gateway_configs SET secrets_enc = :s WHERE workspace_id = :w AND gateway = 'bkash'"),
            {"s": row.secrets_enc, "w": other.workspace_id},
        )
    from bdpay_cloud.crypto import VaultError

    with pytest.raises(VaultError):
        rt.gateway_values(other.sandbox.scope, "bkash")
    with rt.engine.begin() as conn:  # leave no damaged row behind for other tests
        conn.execute(
            text("DELETE FROM gateway_configs WHERE workspace_id = :w AND gateway = 'bkash'"), {"w": other.workspace_id}
        )


def test_simulator_full_cycle_and_refunds(rt, tenant):
    pay = rt.pay(tenant.sandbox)
    pay.create_order("SIM1", "1250.50", "simulator", {"name": "Rahim", "phone": "01712345678"})
    url = pay.start_payment("SIM1")
    sim_id = url.rsplit("/", 1)[1]
    assert pay.requery("SIM1").status == Status.PENDING
    with rt.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE simulator_payments SET status = 'completed', paid_poisha = amount_poisha, "
                "txn_id = 'SIMTX1' WHERE id = :id"
            ),
            {"id": sim_id},
        )
    assert pay.requery("SIM1").status == Status.PAID
    assert pay.refund("SIM1", "250.50").ok and pay.store.get("SIM1").status == Status.PARTIALLY_REFUNDED
    with pytest.raises(BdPayError):
        pay.refund("SIM1", "1000.01")  # more than what is left
    assert pay.refund("SIM1").ok
    done = pay.store.get("SIM1")
    assert done.status == Status.REFUNDED and done.refunded_poisha == 125050
    # The audit trail masks the phone number wherever it appears.
    assert "01712345678" not in str(pay.store.events("SIM1"))


def test_simulator_cannot_exist_in_live(rt):
    t = helpers.new_tenant(rt, live=True)
    with pytest.raises(Exception) as e:
        configure(rt, t.live, "simulator", {})
    assert "sandbox" in str(e.value.message)
    with pytest.raises(BdPayError):
        rt.pay(t.live).create_order("L1", "10", "simulator")


def test_two_refunds_of_one_order_cannot_overlap(rt, tenant):
    pay = rt.pay(tenant.sandbox)
    with pay.store.exclusive("X1", "refund"):
        with pytest.raises(BdPayError) as e:
            with rt.pay(tenant.sandbox).store.exclusive("X1", "refund"):
                pass
        assert e.value.code == "DUPLICATE"
    with rt.pay(tenant.sandbox).store.exclusive("X1", "refund"):  # free again
        pass


def test_refund_with_unknown_outcome_is_flagged(rt, tenant, provider):
    fake = provider(FakeBkash(execute="refund_timeout"))
    configure(rt, tenant.sandbox, "bkash", BKASH)
    pay = rt.pay(tenant.sandbox)
    pay.create_order("RFT1", "300", "bkash")
    pay.start_payment("RFT1")
    assert pay.requery("RFT1").status == Status.PAID
    with pytest.raises(BdPayError) as e:
        pay.refund("RFT1", "100")
    assert e.value.code == "TIMEOUT" and len(fake.refunds) == 1  # a refund call is never retried blindly
    assert pay.store.get("RFT1").status == Status.PAID
    assert "refund_unconfirmed" in [ev["kind"] for ev in pay.store.events("RFT1")]


def test_nagad_keys_come_from_settings_not_files(rt, tenant, provider):
    import base64
    import json

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nagad = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    merchant_pem = merchant.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    nagad_pub = base64.b64encode(
        nagad.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    seen = {}

    def handler(req):
        p = req.url.path
        seen["ip"] = req.headers["x-km-ip-v4"]
        if "/check-out/initialize/" in p:
            reply = json.dumps({"paymentReferenceId": "REF9", "challenge": "CH9"})
            enc = base64.b64encode(merchant.public_key().encrypt(reply.encode(), padding.PKCS1v15())).decode()
            sig = base64.b64encode(nagad.sign(reply.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
            return httpx.Response(200, json={"sensitiveData": enc, "signature": sig})
        if "/check-out/complete/REF9" in p:
            return httpx.Response(200, json={"status": "Success", "callBackUrl": "https://nagad.test/pay"})
        if p.endswith("/verify/payment/REF9"):
            return httpx.Response(
                200, json={"status": "Success", "amount": "99.00", "orderId": "NGD1", "issuerPaymentRefNo": "NG777"}
            )
        return httpx.Response(404)

    provider(handler)
    saved = configure(
        rt,
        tenant.sandbox,
        "nagad",
        {"NAGAD_MERCHANT_ID": "683002007104225", "NAGAD_PRIVATE_KEY": merchant_pem, "NAGAD_PUBLIC_KEY": nagad_pub},
    )
    assert (
        saved["secrets"]["NAGAD_PRIVATE_KEY"]["set"] and "fingerprint" in saved["secrets"]["NAGAD_PRIVATE_KEY"]["hint"]
    )
    pay = rt.pay(tenant.sandbox)
    pay.create_order("NGD1", "99", "nagad")
    assert pay.start_payment("NGD1") == "https://nagad.test/pay"
    assert pay.requery("NGD1").status == Status.PAID
    assert seen["ip"] == "203.0.113.10"  # the deployment's outbound IP, which Nagad whitelists
