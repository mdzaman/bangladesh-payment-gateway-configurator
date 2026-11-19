"""End-to-end through the HTTP routes with a fake bKash."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bdpay.web import build_router
from conftest import make_pay


def test_routes_flow():
    import test_bkash as tb
    fake = tb.FakeBkash()
    pay = make_pay(["bkash"], tb.ENV, fake)
    paid = []
    app = FastAPI()
    app.include_router(build_router(pay, on_paid=paid.append))
    c = TestClient(app)
    assert c.get("/bdpay/gateways").json()[0]["id"] == "bkash"
    r = c.post("/bdpay/orders", json={"order_id": "INV777", "amount": "250", "gateway": "bkash"})
    assert r.status_code == 200, r.text
    url = c.post("/bdpay/orders/INV777/pay").json()["redirect_url"]
    assert url == "https://bkash.test/pay"
    cb = pay.callback_urls(pay.store.get("INV777")).success.replace("https://shop.test", "")
    r = c.get(cb + "?paymentID=TR001&status=success", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/result/INV777")
    assert c.get("/bdpay/orders/INV777").json()["status"] == "paid"
    assert len(paid) == 1
    # duplicate return: still paid, hook not fired twice
    c.get(cb + "?paymentID=TR001&status=success", follow_redirects=False)
    assert len(paid) == 1
    # forged link
    assert c.get("/bdpay/callback/bkash/INV777/1.bad/success").status_code == 403
