import httpx

from bdpay import Status
from conftest import body, callback_parts, make_pay

ENV = {"BKASH_APP_KEY": "app", "BKASH_APP_SECRET": "sec", "BKASH_USERNAME": "u", "BKASH_PASSWORD": "p"}


class FakeBkash:
    def __init__(self, execute="ok", amount="250.00"):
        self.grants = 0
        self.execute = execute
        self.amount = amount
        self.calls = []

    def __call__(self, req: httpx.Request):
        path = req.url.path
        self.calls.append(path)
        if path.endswith("/token/grant"):
            assert req.headers["username"] == "u" and body(req)["app_secret"] == "sec"
            self.grants += 1
            return httpx.Response(200, json={"id_token": "TOKEN", "expires_in": 3600, "refresh_token": "R"})
        assert req.headers["authorization"] == "TOKEN" and req.headers["x-app-key"] == "app"
        b = body(req)
        if path.endswith("/create"):
            assert b["mode"] == "0011" and b["currency"] == "BDT" and b["amount"] == "250.00"
            self.invoice = b["merchantInvoiceNumber"]
            return httpx.Response(200, json={"statusCode": "0000", "paymentID": "TR001", "bkashURL": "https://bkash.test/pay"})
        record = {"statusCode": "0000", "paymentID": "TR001", "trxID": "BK123", "transactionStatus": "Completed",
                  "amount": self.amount, "currency": "BDT", "merchantInvoiceNumber": self.invoice}
        if path.endswith("/execute"):
            if self.execute == "timeout":
                raise httpx.ReadTimeout("slow", request=req)
            if self.execute == "already":
                return httpx.Response(200, json={"statusCode": "2062", "statusMessage": "already completed"})
            return httpx.Response(200, json=record)
        if path.endswith("/payment/status"):
            return httpx.Response(200, json=record)
        if path.endswith("/payment/refund"):
            assert b["trxID"] == "BK123" and b["amount"] == "50.00"
            return httpx.Response(200, json={"statusCode": "0000", "refundTrxID": "RF1", "transactionStatus": "Completed"})
        return httpx.Response(404)


def run(fake, status="success"):
    pay = make_pay(["bkash"], ENV, fake)
    pay.create_order("INV100", "250", "bkash", {"phone": "01770618575"})
    url = pay.start_payment("INV100")
    assert url == "https://bkash.test/pay"
    order = pay.store.get("INV100")
    gw, oid, tok, outcome = callback_parts(pay.callback_urls(order).success)
    return pay, pay.handle_callback(gw, oid, tok, outcome, {"paymentID": "TR001", "status": status})


def test_success_execute():
    fake = FakeBkash()
    pay, o = run(fake)
    assert o.status == Status.PAID and o.provider_txn_id == "BK123"
    assert fake.grants == 1                      # token reused for create + execute


def test_execute_timeout_falls_back_to_query():
    fake = FakeBkash(execute="timeout")
    pay, o = run(fake)
    assert o.status == Status.PAID and fake.calls[-1].endswith("/payment/status")


def test_already_executed_uses_query():
    pay, o = run(FakeBkash(execute="already"))
    assert o.status == Status.PAID


def test_cancel_and_failure_do_not_execute():
    fake = FakeBkash()
    pay, o = run(fake, "cancel")
    assert o.status == Status.CANCELLED and not any(c.endswith("/execute") for c in fake.calls)
    pay, o = run(FakeBkash(), "failure")
    assert o.status == Status.FAILED


def test_amount_mismatch():
    pay, o = run(FakeBkash(amount="1.00"))
    assert o.status == Status.NEEDS_REVIEW


def test_partial_refund():
    pay, o = run(FakeBkash())
    res = pay.refund("INV100", "50")
    assert res.ok and pay.store.get("INV100").status == Status.PARTIALLY_REFUNDED


def test_token_401_is_refreshed_once():
    fake = FakeBkash()
    state = {"n": 0}

    def handler(req):
        if req.url.path.endswith("/create") and state["n"] == 0:
            state["n"] += 1
            return httpx.Response(401, json={"message": "Unauthorized"})
        return fake(req)
    pay = make_pay(["bkash"], ENV, handler)
    pay.create_order("INV101", "250", "bkash")
    pay.start_payment("INV101")
    assert fake.grants == 2
