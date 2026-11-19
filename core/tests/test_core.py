import pytest

from bdpay import BdPayError, Status, fmt_amount, to_poisha
from bdpay.core import Capabilities, Driver, PaymentSession, Verified
from bdpay.http import redact
from bdpay.security import check_callback, sign_callback
from conftest import callback_parts, make_pay


def test_money_round_trip():
    assert to_poisha("100") == 10000
    assert to_poisha("100.5") == 10050
    assert to_poisha(1250.255) == 125026
    assert to_poisha("1,250.00") == 125000
    assert fmt_amount(10050) == "100.50"
    assert fmt_amount(5) == "0.05"
    with pytest.raises(BdPayError):
        to_poisha("abc")
    with pytest.raises(BdPayError):
        to_poisha("NaN")


def test_signed_callback():
    tok = sign_callback("k" * 40, "bkash", "INV1")
    assert check_callback("k" * 40, "bkash", "INV1", tok)
    assert not check_callback("k" * 40, "bkash", "INV2", tok)
    assert not check_callback("k" * 40, "nagad", "INV1", tok)
    assert not check_callback("other" * 8, "bkash", "INV1", tok)
    assert not check_callback("k" * 40, "bkash", "INV1", "garbage")


def test_redaction():
    r = redact({"store_passwd": "x", "id_token": "y", "msg": "call 01712345678", "n": {"app_secret": 1}})
    assert r["store_passwd"] == "***" and r["id_token"] == "***" and r["n"]["app_secret"] == "***"
    assert r["msg"] == "call 017****5678"


class FakeDriver(Driver):
    id = "fake"
    capabilities = Capabilities(refunds="partial")
    answer = None

    def create_payment(self, order, urls):
        return PaymentSession("P-" + order.order_id, "https://provider.test/pay")

    def verify_return(self, order, params):
        return self.answer

    def query(self, order):
        return self.answer


def fake_pay():
    pay = make_pay([], {}, lambda r: None)
    pay.drivers["fake"] = FakeDriver(pay.settings.gateway("fake"), pay.http, pay.tokens)
    return pay, pay.drivers["fake"]


def test_paid_flow_and_duplicate_callback_is_noop():
    pay, d = fake_pay()
    pay.create_order("INV1", "10.00", "fake")
    assert pay.start_payment("INV1") == "https://provider.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV1")).success)
    d.answer = Verified(Status.PAID, 1000, "BDT", "INV1", "P-INV1", "TX1")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.PAID
    d.answer = Verified(Status.FAILED)
    assert pay.handle_callback(gw, oid, tok, "fail", {}).status == Status.PAID   # cannot go backwards
    assert pay.store.get("INV1").provider_txn_id == "TX1"


def test_amount_tamper_goes_to_review():
    pay, d = fake_pay()
    pay.create_order("INV2", "500.00", "fake")
    pay.start_payment("INV2")
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV2")).success)
    d.answer = Verified(Status.PAID, 100, "BDT", "INV2")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.NEEDS_REVIEW


def test_wrong_order_ref_goes_to_review():
    pay, d = fake_pay()
    pay.create_order("INV3", "5", "fake")
    pay.start_payment("INV3")
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV3")).success)
    d.answer = Verified(Status.PAID, 500, "BDT", "SOMEONE_ELSE")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.NEEDS_REVIEW


def test_forged_callback_rejected():
    pay, d = fake_pay()
    pay.create_order("INV4", "5", "fake")
    with pytest.raises(BdPayError) as e:
        pay.handle_callback("fake", "INV4", "123.abc", "success", {})
    assert e.value.code == "SIGNATURE_INVALID"


def test_order_idempotent_and_conflict():
    pay, d = fake_pay()
    a = pay.create_order("INV5", "5", "fake")
    b = pay.create_order("INV5", "5.00", "fake")
    assert a.order_id == b.order_id
    with pytest.raises(BdPayError) as e:
        pay.create_order("INV5", "6", "fake")
    assert e.value.code == "DUPLICATE"


def test_refund_limits():
    pay, d = fake_pay()
    pay.create_order("INV6", "100", "fake")
    pay.start_payment("INV6")
    d.answer = Verified(Status.PAID, 10000, "BDT", "INV6")
    pay.requery("INV6")
    from bdpay.core import RefundResult
    d.refund = lambda order, amount, reason: RefundResult(True, "R1", "completed")
    with pytest.raises(BdPayError):
        pay.refund("INV6", "100.01")
    pay.refund("INV6", "40")
    assert pay.store.get("INV6").status == Status.PARTIALLY_REFUNDED
    pay.refund("INV6")
    o = pay.store.get("INV6")
    assert o.status == Status.REFUNDED and o.refunded_poisha == 10000


def test_live_requires_strong_secret():
    from bdpay import Settings
    with pytest.raises(BdPayError):
        Settings.from_env({"BDPAY_ENV": "live", "BDPAY_SECRET": "short"})


def test_live_refuses_plain_http():
    from bdpay.http import Http
    with pytest.raises(BdPayError):
        Http("live").request("x", "GET", "http://example.com")
