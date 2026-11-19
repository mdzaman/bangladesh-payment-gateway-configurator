"""Shared test helpers (imported as a plain module by the tests)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

from bdpay_cloud.context import Scope
from bdpay_cloud.db import session_scope
from bdpay_cloud.engine.runtime import EnvInfo, Runtime
from bdpay_cloud.ids import rand_b32
from bdpay_cloud.services import workspaces


@dataclass
class Tenant:
    workspace_id: str
    slug: str
    sandbox: EnvInfo
    live: EnvInfo


def new_tenant(rt: Runtime, name: str | None = None, live: bool = False) -> Tenant:
    """A workspace created straight in the database (no user, no HTTP)."""
    with session_scope(rt.sessions) as db:
        ws = workspaces.create_workspace(db, rt.vault, rt.settings, name=name or f"Shop {rand_b32(6)}", owner=None)
        if live:
            ws.live_status = "active"
        ws_id, slug = ws.id, ws.slug
    sandbox, live_env = rt.load_env(Scope(ws_id, "sandbox")), rt.load_env(Scope(ws_id, "live"))
    assert sandbox and live_env
    return Tenant(ws_id, slug, sandbox, live_env)


def callback_parts(url: str):
    """.../callback/<gw>/<order>/<token>/<outcome> -> (prefix path, gw, order, token, outcome)"""
    p = urlparse(url).path.split("/")
    i = p.index("callback")
    return "/".join(p[:i]), p[i + 1], p[i + 2], p[i + 3], p[i + 4]


def body(request: httpx.Request) -> dict:
    raw = request.content.decode() if request.content else ""
    if not raw:
        return {}
    if "json" in request.headers.get("content-type", ""):
        return json.loads(raw)
    return {k: v[0] for k, v in parse_qs(raw).items()}


# ----------------------------------------------------------- dashboard client
PASSWORD = "correct horse battery staple"


class Dash:
    """A browser-like dashboard session: keeps the cookie and sends the CSRF token."""

    def __init__(self, client):
        self.c = client
        self.csrf = ""
        self.me: dict = {}
        self.email = ""

    def _remember(self, r):
        # A sign-in that still owes its second factor answers 200 without a session.
        if r.status_code == 200 and "csrf_token" in r.json():
            self.me = r.json()
            self.csrf = self.me["csrf_token"]
        return r

    def signup(self, email: str | None = None, name: str = "Rahim Uddin", workspace: str | None = None, **extra):
        self.email = email or f"user-{rand_b32(8)}@example.com"
        return self._remember(
            self.c.post(
                "/api/auth/signup",
                json={
                    "email": self.email,
                    "password": PASSWORD,
                    "name": name,
                    "workspace_name": workspace or f"Shop {rand_b32(6)}",
                    **extra,
                },
            )
        )

    def login(self, email: str, password: str = PASSWORD):
        self.email = email
        return self._remember(self.c.post("/api/auth/login", json={"email": email, "password": password}))

    def request(self, method: str, path: str, **kw):
        headers = {"X-CSRF-Token": self.csrf, **kw.pop("headers", {})}
        return self.c.request(method, path, headers=headers, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, json=None, **kw):
        return self.request("POST", path, json=json if json is not None else {}, **kw)

    def put(self, path, json=None, **kw):
        return self.request("PUT", path, json=json if json is not None else {}, **kw)

    def patch(self, path, json=None, **kw):
        return self.request("PATCH", path, json=json if json is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    @property
    def slug(self) -> str:
        return self.me["workspaces"][0]["slug"]

    @property
    def workspace_id(self) -> str:
        return self.me["workspaces"][0]["id"]

    def env(self, mode: str = "sandbox") -> str:
        return f"/api/workspaces/{self.slug}/{mode}"

    def api_key(self, mode: str = "sandbox", scopes=None) -> str:
        r = self.post(f"{self.env(mode)}/api-keys", {"name": "test key", "scopes": scopes})
        assert r.status_code == 200, r.text
        return r.json()["key"]

    def go_live(self):
        """Verify the email straight in the database, then accept the attestation."""
        rt = self.c.app.state.rt
        with rt.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("UPDATE users SET email_verified_at = now() WHERE email = :e"), {"e": self.email})
        r = self.post(f"/api/workspaces/{self.slug}/go-live", {"accept": True, "legal_name": "Rahim Store Ltd"})
        assert r.status_code == 200, r.text
        return r


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def run_cli(monkeypatch, capsys, *argv, keys: str | None = None, **env):
    """Run ``bdpay-cloud <argv>`` in this process against the test database:
    (exit code, stdout, stderr)."""
    from bdpay_cloud import cli
    from bdpay_cloud import settings as settings_module

    from conftest import TEST_DB, TEST_KEYS

    settings_module.get_settings.cache_clear()
    for k, v in {
        "ENV": "test",
        "DATABASE_URL": TEST_DB,
        "MASTER_KEYS": keys or TEST_KEYS,
        "PUBLIC_URL": "https://pay.test",
        "LOG_FORMAT": "text",
        **env,
    }.items():
        monkeypatch.setenv(f"BDPAY_CLOUD_{k}", v)
    monkeypatch.chdir("/")  # no stray .env file
    code = cli.main(list(argv))
    settings_module.get_settings.cache_clear()
    out = capsys.readouterr()
    return code, out.out, out.err


def last_email(rt: Runtime, to: str) -> dict | None:
    from sqlalchemy import text

    with rt.engine.connect() as conn:
        r = conn.execute(
            text("SELECT subject, body_text FROM outbound_emails WHERE to_email = :to ORDER BY id DESC LIMIT 1"),
            {"to": to},
        ).one_or_none()
    return {"subject": r.subject, "body": r.body_text} if r else None


def token_from(email: dict) -> str:
    import re

    return re.search(r"token=([0-9a-f]+)", email["body"]).group(1)


def simulate(client, redirect_url: str, action: str = "approve", follow: bool = True):
    """Act as the customer on the simulator page, then follow the redirect back
    to this service (the signed callback). Returns the callback response."""
    path = redirect_url.replace("https://pay.test", "")
    assert client.get(path).status_code == 200
    r = client.post(path, data={"action": action}, follow_redirects=False)
    if r.status_code != 303 or not follow:
        return r
    return client.get(r.headers["location"].replace("https://pay.test", ""), follow_redirects=False)


# -------------------------------------------------------------- fake providers
BKASH = {
    "BKASH_APP_KEY": "app",
    "BKASH_APP_SECRET": "sec-long-enough-12",
    "BKASH_USERNAME": "u",
    "BKASH_PASSWORD": "p-long-enough-123",
}


def own_bkash() -> dict:
    """bKash credentials no other test uses. Identical credentials share a cached
    provider token by design, so a test that counts token grants needs its own."""
    return {**BKASH, "BKASH_APP_KEY": "app-" + rand_b32(10)}


class FakeBkash:
    """bKash Tokenized Checkout as the build spec describes it."""

    def __init__(self, amount: str | None = None, execute: str = "ok", bad_credentials: bool = False):
        self.grants = 0
        self.creates = 0
        self.amount = amount
        self.execute = execute
        self.bad_credentials = bad_credentials
        self.payments: dict[str, dict] = {}
        self.calls: list[str] = []
        self.refunds: list[dict] = []
        self.create_delay = 0.0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path)
        if path.endswith("/token/grant"):
            self.grants += 1
            if self.bad_credentials or body(req).get("app_secret") != BKASH["BKASH_APP_SECRET"]:
                return httpx.Response(200, json={"statusCode": "9999", "statusMessage": "Invalid credentials"})
            return httpx.Response(200, json={"id_token": "TOKEN", "expires_in": 3600})
        if req.headers.get("authorization") != "TOKEN":
            return httpx.Response(401, json={"message": "Unauthorized"})
        b = body(req)
        if path.endswith("/create"):
            import time

            if self.create_delay:
                time.sleep(self.create_delay)
            self.creates += 1
            pid = f"TR{self.creates:04d}{b['merchantInvoiceNumber']}"
            self.payments[pid] = {
                "statusCode": "0000",
                "paymentID": pid,
                "trxID": "BK" + pid[2:],
                "transactionStatus": "Completed",
                "amount": self.amount or b["amount"],
                "currency": "BDT",
                "merchantInvoiceNumber": b["merchantInvoiceNumber"],
                "callbackURL": b["callbackURL"],
            }
            return httpx.Response(
                200, json={"statusCode": "0000", "paymentID": pid, "bkashURL": f"https://bkash.test/pay/{pid}"}
            )
        if path.endswith("/execute"):
            if self.execute == "timeout":
                raise httpx.ReadTimeout("slow", request=req)
            return httpx.Response(200, json=self.payments[b["paymentID"]])
        if path.endswith("/payment/status"):
            return httpx.Response(200, json=self.payments[b["paymentID"]])
        if path.endswith("/payment/refund"):
            self.refunds.append(b)
            if self.execute == "refund_timeout":
                raise httpx.ReadTimeout("slow", request=req)
            return httpx.Response(
                200,
                json={"statusCode": "0000", "refundTrxID": f"RF{len(self.refunds)}", "transactionStatus": "Completed"},
            )
        return httpx.Response(404)

    def callback_for(self, order_id: str, status: str = "success") -> str:
        """The URL bKash would send the customer back to."""
        pid = next(p for p, rec in self.payments.items() if rec["merchantInvoiceNumber"] == order_id)
        return f"{self.payments[pid]['callbackURL']}?paymentID={pid}&status={status}".replace("https://pay.test", "")


SSLCOMMERZ = {"SSLCOMMERZ_STORE_ID": "mystore", "SSLCOMMERZ_STORE_PASSWORD": "mystore-password"}


class FakeSsl:
    def __init__(self, api_connect: str = "DONE"):
        self.api_connect = api_connect
        self.sessions: dict[str, dict] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/gwprocess/v4/api.php"):
            b = body(req)
            self.sessions[b["tran_id"]] = b
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "GatewayPageURL": "https://ssl.test/gw/" + b["tran_id"],
                    "sessionkey": "S" + b["tran_id"],
                },
            )
        if path.endswith("validationserverAPI.php"):
            tran = req.url.params["val_id"].removeprefix("VAL")
            s = self.sessions[tran]
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "tran_id": tran,
                    "amount": s["total_amount"],
                    "currency_type": "BDT",
                    "currency_amount": s["total_amount"],
                    "bank_tran_id": "BT" + tran,
                    "val_id": "VAL" + tran,
                },
            )
        if path.endswith("merchantTransIDvalidationAPI.php"):
            if req.url.params.get("store_passwd") != SSLCOMMERZ["SSLCOMMERZ_STORE_PASSWORD"]:
                return httpx.Response(200, json={"APIConnect": "FAILED"})
            return httpx.Response(200, json={"APIConnect": self.api_connect, "no_of_trans_found": 0, "element": []})
        return httpx.Response(404)
