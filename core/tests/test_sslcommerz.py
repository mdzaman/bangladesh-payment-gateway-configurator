import hashlib

import httpx
import pytest

from bdpay import BdPayError, Status
from bdpay.drivers.sslcommerz import check_verify_sign
from conftest import body, callback_parts, make_pay

ENV = {"SSLCOMMERZ_STORE_ID": "testbox", "SSLCOMMERZ_STORE_PASSWORD": "qwerty"}


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def signed(params, passwd="qwerty"):
    keys = list(params)
    d = dict(params)
    d["store_passwd"] = md5(passwd)
    s = "&".join(f"{k}={d[k]}" for k in sorted(d))
    return {**params, "verify_key": ",".join(keys), "verify_sign": md5(s)}


class FakeSsl:
    def __init__(self, amount="1500.00", status="VALID"):
        self.amount, self.status = amount, status

    def __call__(self, req):
        p = req.url.path
        if p.endswith("/gwprocess/v4/api.php"):
            b = body(req)
            assert b["total_amount"] == "1500.00" and b["store_id"] == "testbox" and b["ipn_url"].endswith("/ipn/sslcommerz")
            return httpx.Response(200, json={"status": "SUCCESS", "GatewayPageURL": "https://ssl.test/gw", "sessionkey": "S1"})
        if p.endswith("validationserverAPI.php"):
            assert req.url.params["val_id"] == "VAL1"
            return httpx.Response(200, json={"status": self.status, "tran_id": "INV200", "amount": self.amount,
                                             "currency_type": "BDT", "currency_amount": self.amount,
                                             "bank_tran_id": "BT1", "val_id": "VAL1"})
        if p.endswith("merchantTransIDvalidationAPI.php"):
            if "refund_amount" in req.url.params:
                return httpx.Response(200, json={"APIConnect": "DONE", "status": "success", "refund_ref_id": "RR1"})
            return httpx.Response(200, json={"APIConnect": "DONE", "no_of_trans_found": 1, "element": [
                {"status": "FAILED", "tran_id": "INV200", "amount": "1500.00", "currency_type": "BDT", "currency_amount": "1500.00"}]})
        return httpx.Response(404)


def setup(fake):
    pay = make_pay(["sslcommerz"], ENV, fake)
    pay.create_order("INV200", "1500", "sslcommerz")
    assert pay.start_payment("INV200") == "https://ssl.test/gw"
    return pay, callback_parts(pay.callback_urls(pay.store.get("INV200")).success)


def test_verify_sign_helper():
    p = signed({"tran_id": "A", "val_id": "B", "amount": "10.00"})
    assert check_verify_sign(p, "qwerty")
    assert not check_verify_sign({**p, "amount": "99.00"}, "qwerty")


def test_success_is_validated_server_side():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    o = pay.handle_callback(gw, oid, tok, "success", signed({"tran_id": "INV200", "val_id": "VAL1", "status": "VALID"}))
    assert o.status == Status.PAID and o.provider_txn_id == "BT1"


def test_tampered_post_rejected():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    bad = signed({"tran_id": "INV200", "val_id": "VAL1", "amount": "1500.00"})
    bad["amount"] = "1.00"
    with pytest.raises(BdPayError) as e:
        pay.handle_callback(gw, oid, tok, "success", bad)
    assert e.value.code == "SIGNATURE_INVALID"


def test_validation_amount_mismatch():
    pay, (gw, oid, tok, _) = setup(FakeSsl(amount="15.00"))
    o = pay.handle_callback(gw, oid, tok, "success", {"tran_id": "INV200", "val_id": "VAL1"})
    assert o.status == Status.NEEDS_REVIEW


def test_ipn_then_duplicate_validated():
    fake = FakeSsl()
    pay, _ = setup(fake)
    assert pay.handle_ipn("sslcommerz", {"tran_id": "INV200", "val_id": "VAL1"}).status == Status.PAID
    fake.status = "VALIDATED"
    assert pay.handle_ipn("sslcommerz", {"tran_id": "INV200", "val_id": "VAL1"}).status == Status.PAID


def test_fail_without_val_id_queries():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    assert pay.handle_callback(gw, oid, tok, "fail", {"tran_id": "INV200"}).status == Status.FAILED


def test_refund():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    pay.handle_callback(gw, oid, tok, "success", {"tran_id": "INV200", "val_id": "VAL1"})
    assert pay.refund("INV200", "500").ok
