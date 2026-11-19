import httpx

from bdpay import Status
from conftest import make_pay

ENV = {"PORTPOS_APP_KEY": "ak", "PORTPOS_SECRET_KEY": "sk"}


def handler(req):
    assert req.headers["authorization"].startswith("Bearer ")
    if req.method == "POST" and req.url.path.endswith("/invoice"):
        return httpx.Response(200, json={"result": "success", "data": {"invoice_id": "PP1",
                                                                       "action": {"url": "https://portpos.test/pay"}}})
    if req.url.path.endswith("/invoice/PP1"):
        return httpx.Response(200, json={"result": "success", "data": {"order": {
            "status": "ACCEPTED", "amount": 45, "currency": "BDT", "reference": "INV700"}}})
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["portpos"], ENV, handler)
    pay.create_order("INV700", "45", "portpos")
    assert pay.start_payment("INV700") == "https://portpos.test/pay"
    assert pay.handle_ipn("portpos", {"invoice": "PP1"}).status == Status.PAID
