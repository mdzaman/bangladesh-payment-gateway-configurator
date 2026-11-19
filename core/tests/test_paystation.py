import httpx

from bdpay import Status
from conftest import body, callback_parts, make_pay

ENV = {"PAYSTATION_MERCHANT_ID": "104-1", "PAYSTATION_PASSWORD": "pw"}


def handler(req):
    if req.url.path.endswith("/initiate-payment"):
        assert body(req)["payment_amount"] == "60.00"
        return httpx.Response(200, json={"status_code": "200", "payment_url": "https://ps.test/pay"})
    if req.url.path.endswith("/transaction-status"):
        assert req.headers["merchantid"] == "104-1"
        return httpx.Response(200, json={"status_code": "200", "data": {
            "trx_status": "Success", "payment_amount": "60.00", "invoice_number": "INV800", "trx_id": "PS1"}})
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["paystation"], ENV, handler)
    pay.create_order("INV800", "60", "paystation")
    assert pay.start_payment("INV800") == "https://ps.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV800")).success)
    assert pay.handle_callback(gw, oid, tok, "success", {"invoice_number": "INV800"}).status == Status.PAID
