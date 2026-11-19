import httpx

from bdpay import Status
from conftest import body, callback_parts, make_pay

ENV = {"UPAY_BASE_URL": "https://upay.test", "UPAY_MERCHANT_ID": "MID", "UPAY_MERCHANT_KEY": "KEY",
       "UPAY_MERCHANT_CODE": "MC", "UPAY_MERCHANT_NAME": "Shop", "UPAY_MERCHANT_MOBILE": "01800000000"}


def handler(req):
    p = req.url.path
    if p.endswith("/merchant-auth/"):
        return httpx.Response(200, json={"code": "MAS200", "data": {"token": "UT"}})
    assert req.headers["authorization"] == "UPAY UT"
    if p.endswith("/merchant-payment-init/"):
        assert body(req)["amount"] == "20.00"
        return httpx.Response(200, json={"code": "MPIS200", "data": {"gateway_url": "https://upay.test/pay"}})
    if p.endswith("/single-payment-status/INV900/"):
        return httpx.Response(200, json={"data": {"status": "success", "amount": "20.00", "txn_id": "INV900", "trx_id": "U1"}})
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["upay"], ENV, handler)
    pay.create_order("INV900", "20", "upay")
    assert pay.start_payment("INV900") == "https://upay.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV900")).success)
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.PAID
