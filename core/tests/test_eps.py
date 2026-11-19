import httpx

from bdpay import Status
from conftest import callback_parts, make_pay

ENV = {"EPS_USERNAME": "u@x.com", "EPS_PASSWORD": "p", "EPS_HASH_KEY": "hk", "EPS_MERCHANT_ID": "M", "EPS_STORE_ID": "S"}


def handler(req):
    p = req.url.path
    assert req.headers.get("x-hash")
    if p.endswith("/Auth/GetToken"):
        return httpx.Response(200, json={"token": "JWT"})
    assert req.headers["authorization"] == "Bearer JWT"
    if p.endswith("/InitializeEPS"):
        return httpx.Response(200, json={"TransactionId": "E1", "RedirectURL": "https://eps.test/pay"})
    if p.endswith("/CheckMerchantTransactionStatus"):
        return httpx.Response(200, json={"Status": "Success", "TotalAmount": "300.00",
                                         "MerchantTransactionId": "INV600", "EpsTransactionId": "EPS9"})
    return httpx.Response(404)


def test_flow():
    pay = make_pay(["eps"], ENV, handler)
    pay.create_order("INV600", "300", "eps")
    assert pay.start_payment("INV600") == "https://eps.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV600")).success)
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.PAID
