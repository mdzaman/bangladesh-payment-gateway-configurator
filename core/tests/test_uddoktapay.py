import httpx
import pytest

from bdpay import BdPayError, Status
from conftest import callback_parts, make_pay

ENV = {"UDDOKTAPAY_BASE_URL": "https://sandbox.uddoktapay.com",
       "UDDOKTAPAY_API_KEY": "982d381360a69d419689740d9f2e26ce36fb7a50", "BDPAY_ALLOW_EXPERIMENTAL": "1"}


def handler(req):
    if req.url.path.endswith("/api/checkout-v2"):
        return httpx.Response(200, json={"status": True, "payment_url": "https://ud.test/pay"})
    if req.url.path.endswith("/api/verify-payment"):
        return httpx.Response(200, json={"status": "COMPLETED", "amount": "15.00", "invoice_id": "UD1",
                                         "metadata": {"order_id": "INV950"}, "transaction_id": "T1"})
    return httpx.Response(404)


def test_requires_opt_in():
    with pytest.raises(BdPayError):
        make_pay(["uddoktapay"], {k: v for k, v in ENV.items() if k != "BDPAY_ALLOW_EXPERIMENTAL"}, handler)


def test_flow():
    pay = make_pay(["uddoktapay"], ENV, handler)
    pay.create_order("INV950", "15", "uddoktapay")
    assert pay.start_payment("INV950") == "https://ud.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV950")).success)
    assert pay.handle_callback(gw, oid, tok, "success", {"invoice_id": "UD1"}).status == Status.PAID
