import httpx

from bdpay import Status
from conftest import body, callback_parts, make_pay

ENV = {"SHURJOPAY_USERNAME": "sp_sandbox", "SHURJOPAY_PASSWORD": "pyyk97hu&6u6", "SHURJOPAY_PREFIX": "NOK"}


def handler(req):
    p = req.url.path
    if p.endswith("/api/get_token"):
        return httpx.Response(200, json={"token": "T", "store_id": 1, "expires_in": 3600, "sp_code": "200"})
    assert req.headers["authorization"] == "Bearer T"
    if p.endswith("/api/secret-pay"):
        b = body(req)
        assert b["prefix"] == "NOK" and b["amount"] == "120.00" and b["order_id"] == "INV500"
        return httpx.Response(200, json={"checkout_url": "https://sp.test/pay", "sp_order_id": "NOK123"})
    if p.endswith("/api/verification"):
        assert body(req)["order_id"] == "NOK123"
        return httpx.Response(200, json=[{"sp_code": "1000", "amount": "120.00", "currency": "BDT",
                                          "customer_order_id": "INV500", "bank_trx_id": "SPB1"}])
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["shurjopay"], ENV, handler)
    pay.create_order("INV500", "120", "shurjopay")
    assert pay.start_payment("INV500") == "https://sp.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV500")).success)
    o = pay.handle_callback(gw, oid, tok, "success", {"order_id": "NOK123"})
    assert o.status == Status.PAID
