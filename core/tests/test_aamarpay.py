import httpx

from bdpay import Status
from conftest import body, callback_parts, make_pay

ENV = {"AAMARPAY_STORE_ID": "aamarpaytest", "AAMARPAY_SIGNATURE_KEY": "dbb74894e82415a2f7ff0ec3a97e4183"}


def handler(req):
    if req.url.path.endswith("/jsonpost.php"):
        b = body(req)
        assert b["amount"] == "75.50" and b["type"] == "json"
        return httpx.Response(200, json={"result": "true", "payment_url": "https://aamar.test/pay"})
    if req.url.path.endswith("/trxcheck/request.php"):
        assert req.url.params["request_id"] == "INV400"
        return httpx.Response(200, json={"status_code": "2", "pay_status": "Successful", "amount": "75.50",
                                         "currency": "BDT", "mer_txnid": "INV400", "pg_txnid": "AMR1"})
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["aamarpay"], ENV, handler)
    pay.create_order("INV400", "75.5", "aamarpay")
    assert pay.start_payment("INV400") == "https://aamar.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV400")).success)
    o = pay.handle_callback(gw, oid, tok, "success", {"mer_txnid": "INV400", "pay_status": "Successful"})
    assert o.status == Status.PAID and o.provider_txn_id == "AMR1"
