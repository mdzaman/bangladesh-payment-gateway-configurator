# BDPAY — Build Spec for AI Coding Tools

**What this file is:** a complete, self-contained specification plus the full reference source code for **bdpay**, an open-source, plug-in payment integration for Bangladesh (bKash, Nagad, SSLCOMMERZ, aamarPay, shurjoPay, EPS, PortPos, PayStation, Upay, UddoktaPay, and every wallet, card and bank reached through those aggregators).

**How to use it:** upload this one file to Claude Code, Cursor, Windsurf, Lovable, Replit Agent, Bolt or any coding assistant and paste the prompt in Part 0. The assistant rebuilds the project exactly, runs the tests, and then extends it in your stack. Parts 1 to 9 are the rules the code must keep; Part 10 is the source.

Not affiliated with any payment provider. Merchant-side software only: money always moves through a licensed provider into the merchant's own account.

---

## Part 0 — Prompts to paste

### 0.1 Rebuild the reference project (Python)

```
You are building "bdpay" from the attached BDPAY_BUILD_SPEC.md.
1. Create every file in Part 10 at exactly the path shown, with exactly the content shown.
2. Create a virtualenv, run: pip install -r requirements.txt -r requirements-dev.txt
3. Run: python -m pytest -q   (all tests must pass, offline, no credentials)
4. Run: python -m bdpay doctor
5. Copy .env.example to .env, set BDPAY_GATEWAYS to the gateways I name, fill sandbox values from Part 6.
6. Start: uvicorn app:app --reload  and open http://localhost:8000
Do not change any rule in Parts 3-5. If a test fails, fix the code, not the test, unless the test contradicts this spec.
```

### 0.2 Add bdpay to my existing app

```
Using BDPAY_BUILD_SPEC.md, integrate Bangladeshi payments into this repository.
- My stack is: <FastAPI | Django | Flask | Laravel | Node/Express | Next.js | WordPress | Flutter app + API>.
- Python stacks: vendor the bdpay/ package (Part 10) and mount it as shown in Part 7.
- Any other stack: run bdpay as a separate service (Docker, Part 8) and call its REST API (Part 7.1)
  from my backend. Never put gateway secrets in a browser or mobile app.
- Gateways: <bkash, nagad, sslcommerz, ...>.
- Add a checkout button on <page>, fulfil the order in on_paid, show the result page.
- Keep every rule in Part 4. Add tests for my integration.
```

### 0.3 Port the core to another language

```
Port bdpay to <TypeScript | PHP | Go | Java>. Keep the same public API (Part 3.2), the same
state machine (Part 3.4), the same per-gateway protocol (Part 5) and port every test in tests/.
Use only the standard library + one HTTP client. Same env variable names.
```

### 0.4 Add a new gateway

```
Add a driver for <gateway> to bdpay following Part 9 (driver checklist). Create
bdpay/drivers/<id>.py, register it in bdpay/registry.py, add tests/test_<id>.py using a fake
provider with httpx.MockTransport, and document the env variables in README and .env.example.
```

---

## Part 1 — Goals

1. One interface for every Bangladeshi payment gateway: `create order → redirect → verify → paid`.
2. Fully configurable from environment variables; no secret ever in code.
3. Works three ways: as a Python library, as a FastAPI router, or as a standalone REST service any language can call.
4. Sandbox and live are one switch (`BDPAY_ENV`); live mode adds hard safety checks.
5. Offline test suite with fake gateways for every driver; CI on GitHub Actions with a secrets scan.
6. One-command deploy (`docker compose up -d --build`) with a reconcile worker.

Non-goals: holding funds, pooling merchants' money, capturing card numbers, reverse-engineering private APIs.

## Part 2 — Gateway landscape (what is integrable)

| Gateway | Type | Direct API | Sandbox | Refund by API | Driver status |
|---|---|---|---|---|---|
| bKash Tokenized Checkout | MFS | Yes | Public demo wallet; credentials from bKash | Partial | stable |
| Nagad | MFS | Merchant pack only | Keys by email | Panel | stable (verify against pack) |
| SSLCOMMERZ | Aggregator: cards, banks, all wallets | Yes | Self-register | Partial | stable |
| aamarPay | Aggregator | Yes | Public test store | Panel | stable |
| shurjoPay v2.1 | Aggregator | Yes | Public sandbox user | Panel | stable |
| EPS | Licensed PSO aggregator | Yes | Merchant portal | Panel | beta |
| PortPos (ex-PortWallet) | Aggregator | Yes | Sandbox panel | Partial | beta |
| PayStation | Aggregator | Yes | On request | Panel | beta |
| Upay (UCB) | MFS | Onboarding pack only | On request | Full only | beta |
| UddoktaPay | Self-hosted wallet automation | Yes | Public sandbox key | None | experimental, opt-in |
| Rocket, OK Wallet, CellFin, Tap, mCash, SureCash | MFS | No public merchant API | — | — | via aggregators |
| Visa / Mastercard / Amex, internet banking | Cards / banks | Via aggregator hosted page | — | — | via aggregators |
| Bangla QR | National QR | Issued by bank/MFS; no e-commerce API | — | — | out of scope (reconcile from statements) |
| NPSB / BEFTN / RTGS | Interbank rails | Bank corporate API only | — | — | out of scope |
| Stripe / PayPal | Global | Not available to BD-domiciled merchants | — | — | out of scope |

"beta" means the driver follows public and community references; check every field against the provider's onboarding pack before going live. All base URLs can be overridden with `<GATEWAY>_BASE_URL`.

## Part 3 — Architecture

### 3.1 File tree

```
bdpay-service/
├─ bdpay/
│  ├─ __init__.py        public exports
│  ├─ core.py            Money helpers, Status, Order, Verified, Driver base class, BdPayError
│  ├─ config.py          Settings + GatewayConfig from env / .env
│  ├─ http.py            httpx wrapper: timeouts, safe retries, TLS always on, redaction
│  ├─ store.py           SQLite orders + audit events + token cache (single-flight)
│  ├─ security.py        HMAC-signed, expiring callback URLs
│  ├─ registry.py        gateway id -> driver class (lazy import)
│  ├─ service.py         BdPay facade: create_order, start_payment, handle_callback, handle_ipn, requery, refund, reconcile
│  ├─ web.py             FastAPI router (REST API + provider callbacks + result page)
│  ├─ __main__.py        CLI: doctor | reconcile | requery ID | serve
│  └─ drivers/           bkash, nagad, sslcommerz, aamarpay, shurjopay, eps, portpos, paystation, upay, uddoktapay
├─ app.py                runnable service + sandbox test console
├─ tests/                one fake-provider test file per driver + core + web
├─ Dockerfile, docker-compose.yml, .github/workflows/ci.yml
├─ .env.example, requirements.txt, requirements-dev.txt, pyproject.toml, LICENSE (MIT), README.md
```

### 3.2 Public API

```python
pay = BdPay.from_env()                                   # reads BDPAY_* and gateway env vars
order = pay.create_order(order_id, amount, gateway, customer={})   # amount saved server-side (poisha)
url   = pay.start_payment(order_id)                      # redirect customer here
order = pay.handle_callback(gateway, order_id, token, outcome, params)  # always re-verifies
order = pay.handle_ipn(gateway, params)                  # IPN = hint, re-verifies
order = pay.requery(order_id)                            # ask provider again
res   = pay.refund(order_id, amount=None, reason="")    # full if amount None
pay.reconcile(older_than_minutes=10, expire_after_hours=24)
```

### 3.3 Driver contract (`bdpay/core.py`)

```python
class Driver(ABC):
    id, label, stability, capabilities, required_env
    def create_payment(order, urls) -> PaymentSession(provider_payment_id, redirect_url, raw)
    def verify_return(order, params) -> Verified      # MUST call the provider server-to-server
    def query(order) -> Verified                      # authoritative status
    def refund(order, amount_poisha, reason) -> RefundResult   # optional
    def ipn_lookup(params) -> ("order_id"|"provider_payment_id", value) | None
```

`Verified(status, amount_poisha, currency, order_ref, provider_payment_id, provider_txn_id, raw)`.

### 3.4 State machine (forward only)

```
created → pending → paid | failed | cancelled | expired | needs_review
failed | cancelled | expired → paid | needs_review      (late verified payment rescues an order)
paid → partially_refunded → refunded
```

Any other transition is ignored and logged as `ignored_transition`. Repeating the current status is a no-op. This makes duplicate callbacks and IPNs harmless.

### 3.5 Money

Integer poisha everywhere (`to_poisha("1,250.50") == 125050`). Format `"1250.50"` only when talking to providers. Currency is BDT only.

## Part 4 — Non-negotiable rules

1. **Server-side amount truth.** The amount stored by `create_order` is the only amount accepted. If the provider's verified amount, currency (BDT/050) or order reference differs, the order goes to `needs_review`, never `paid`.
2. **Redirects and IPNs are untrusted.** Every callback and IPN triggers a server-to-server verification (Part 5).
3. **Signed callback URLs.** `/bdpay/callback/{gateway}/{order_id}/{exp}.{hmac}/{outcome}`; HMAC-SHA256 over `gateway|order_id|exp` with `BDPAY_SECRET`, 24 h expiry, constant-time compare.
4. **Idempotency.** Orders are unique by `order_id`; recreating with the same gateway and amount returns the existing order; a different amount raises `DUPLICATE`. One payment attempt per order ID.
5. **Token cache.** bKash, shurjoPay, EPS and Upay tokens are cached in the store and refreshed 5 minutes before expiry, with a per-key lock (single flight). On HTTP 401, invalidate and retry once. Never grant a token per request.
6. **HTTP policy.** Connect 5 s, read 30 s. Retries (3, jittered backoff) only on reads/queries. TLS verification cannot be disabled. Live mode refuses `http://` URLs.
7. **Secrets.** Only from env/.env or key files (`keys/`, git-ignored). Logs mask keys containing secret/password/token/key/signature/hash and Bangladeshi mobile numbers (`017****5678`).
8. **Live mode guards.** `BDPAY_SECRET` ≥ 32 chars; public URL must be https; test console off unless `BDPAY_CONSOLE=1`.
9. **Experimental drivers** need `BDPAY_ALLOW_EXPERIMENTAL=1`.
10. **Audit.** Every create, transition, ignored transition, refund and still-pending check is written to `events` with redacted detail.

## Part 5 — Per-gateway protocol

### bKash Tokenized Checkout (`bkash`)
- Base: sandbox `https://tokenized.sandbox.bka.sh`, live `https://tokenized.pay.bka.sh`, path `/{BKASH_API_VERSION=v1.2.0-beta}/tokenized/checkout`.
- Grant: `POST /token/grant`, headers `username`, `password`; body `{app_key, app_secret}` → `id_token`, `expires_in` 3600.
- Every call: headers `Authorization: <id_token>`, `X-APP-Key: <app_key>`.
- Create: `POST /create {mode:"0011", payerReference, callbackURL, amount:"250.00", currency:"BDT", intent:"sale", merchantInvoiceNumber:<order_id>}` → `paymentID`, `bkashURL` (check statusCode 0000 and non-null URL).
- Callback: `callbackURL?paymentID=…&status=success|failure|cancel`. cancel → cancelled, failure → failed, no execute.
- success → `POST /execute {paymentID}`. If it times out, errors or returns a non-0000 code (e.g. 2062 already executed) → `POST /payment/status {paymentID}`. Paid only if `transactionStatus == "Completed"` and amount + `merchantInvoiceNumber` match.
- Refund: `POST /payment/refund {paymentID, trxID, amount, sku, reason}` → `refundTrxID`.
- Env: `BKASH_APP_KEY, BKASH_APP_SECRET, BKASH_USERNAME, BKASH_PASSWORD, [BKASH_API_VERSION, BKASH_BASE_URL]`.

### Nagad (`nagad`)
- Base: live `https://api.mynagad.com/api/dfs`; sandbox `http://sandbox.mynagad.com:10080/remote-payment-gateway-1.0/api/dfs` (override with `NAGAD_BASE_URL` from your pack).
- Headers: `X-KM-Api-Version: v-0.2.0`, `X-KM-Client-Type: PC_WEB`, `X-KM-IP-V4: <ip>`, `Content-Type: application/json`.
- Crypto: `sensitiveData` = Base64(RSA-PKCS#1 v1.5 encrypt(compact JSON) with Nagad public key); `signature` = Base64(SHA256withRSA(compact JSON) with merchant private key). Responses: decrypt with merchant private key, verify signature with Nagad public key.
- Initialize: `POST /check-out/initialize/{merchantId}/{orderId}` body `{dateTime, sensitiveData{merchantId, datetime(YmdHis Asia/Dhaka), orderId, challenge}, signature, [accountNumber]}` → decrypt → `paymentReferenceId`, `challenge`.
- Complete: `POST /check-out/complete/{paymentReferenceId}` body `{sensitiveData{merchantId, orderId, amount, currencyCode:"050", challenge}, signature, merchantCallbackURL, additionalMerchantInfo}` → `status:"Success"`, `callBackUrl`.
- Verify: `GET /verify/payment/{paymentReferenceId}` → `status` Success = paid; amount and orderId must match.
- Order IDs: letters and digits only, max 20. Keys accepted as PEM or bare base64. Production: whitelist server IPv4 and callback domain.
- Env: `NAGAD_MERCHANT_ID, NAGAD_PRIVATE_KEY_PATH, NAGAD_PUBLIC_KEY_PATH, [NAGAD_MERCHANT_NUMBER, NAGAD_CLIENT_IP, NAGAD_BASE_URL]`.

### SSLCOMMERZ v4 (`sslcommerz`)
- Host: sandbox `https://sandbox.sslcommerz.com`, live `https://securepay.sslcommerz.com`.
- Init: `POST /gwprocess/v4/api.php` (form) with `store_id, store_passwd, total_amount, currency=BDT, tran_id=<order_id>, success_url, fail_url, cancel_url, ipn_url, cus_*, shipping_method=NO, product_*` → `status:SUCCESS`, `GatewayPageURL`, `sessionkey`.
- verify_sign: take fields named in `verify_key`, add `store_passwd = md5(passwd)`, sort keys, join `k=v` with `&`, md5 must equal `verify_sign`.
- Validate: `GET /validator/api/validationserverAPI.php?val_id&store_id&store_passwd&format=json`. `VALID` or `VALIDATED` (already validated) = paid; compare `tran_id`, `currency_amount`, `currency_type`.
- No val_id (fail/cancel) → query `GET /validator/api/merchantTransIDvalidationAPI.php?tran_id=…`.
- Refund: same endpoint with `bank_tran_id, refund_amount, refund_remarks`.
- Env: `SSLCOMMERZ_STORE_ID, SSLCOMMERZ_STORE_PASSWORD`. Register the IPN URL in My Store → IPN Settings.

### aamarPay (`aamarpay`)
- Host: sandbox `https://sandbox.aamarpay.com`, live `https://secure.aamarpay.com`.
- Init: `POST /jsonpost.php {store_id, signature_key, tran_id, amount, currency, desc, cus_*, success_url, fail_url, cancel_url, type:"json"}` → `result:"true"`, `payment_url`.
- Verify: `GET /api/v1/trxcheck/request.php?request_id=<order_id>&store_id&signature_key&type=json`; `status_code` 2 paid, 0 pending, 3 expired, 7 failed.
- Env: `AAMARPAY_STORE_ID, AAMARPAY_SIGNATURE_KEY`.

### shurjoPay v2.1 (`shurjopay`)
- Base: sandbox `https://sandbox.shurjopayment.com`, live `https://engine.shurjopayment.com`.
- `POST /api/get_token {username,password}` → `token, store_id` (cached) → `POST /api/secret-pay` (Bearer) with `prefix, token, store_id, return_url, cancel_url, amount, order_id, currency, customer_*` → `checkout_url, sp_order_id` → `POST /api/verification {order_id: sp_order_id}` → `sp_code` 1000 paid, 1001 failed, 1002 cancelled.
- Env: `SHURJOPAY_USERNAME, SHURJOPAY_PASSWORD, SHURJOPAY_PREFIX`.

### EPS (`eps`, beta)
- Base: sandbox `https://sandboxpgapi.eps.com.bd`, live `https://pgapi.eps.com.bd`. Header `x-hash = Base64(HMAC-SHA512(hash_key, value))`.
- `POST /v1/Auth/GetToken {userName,password}` (x-hash of userName) → JWT → `POST /v1/EPSEngine/InitializeEPS` (x-hash of merchantTransactionId) → `RedirectURL` → `GET /v1/EPSEngine/CheckMerchantTransactionStatus?merchantTransactionId=…` → `Status` Success.
- Env: `EPS_USERNAME, EPS_PASSWORD, EPS_HASH_KEY, EPS_MERCHANT_ID, EPS_STORE_ID`.

### PortPos (`portpos`, beta)
- Base: sandbox `https://api-sandbox.portpos.com/payment/v2`, live `https://api.portpos.com/payment/v2`. Auth `Bearer base64(app_key:md5(secret_key + unix_ts))`.
- `POST /invoice` → `data.invoice_id`, `data.action.url` → `GET /invoice/{id}` → `data.order.status` ACCEPTED = paid. IPN carries `invoice`.
- Env: `PORTPOS_APP_KEY, PORTPOS_SECRET_KEY`.

### PayStation (`paystation`, beta)
- Base: sandbox `https://sandbox.paystation.com.bd`, live `https://api.paystation.com.bd`.
- `POST /initiate-payment` (form: merchantId, password, invoice_number, payment_amount, callback_url, cust_*) → `payment_url` → `POST /transaction-status` (header merchantId, body invoice_number) → `data.trx_status` Success.
- Env: `PAYSTATION_MERCHANT_ID, PAYSTATION_PASSWORD, [PAYSTATION_PAY_WITH_CHARGE]`.

### Upay (`upay`, beta)
- Base URL from onboarding pack (`UPAY_BASE_URL`). `POST /payment/merchant-auth/ {merchant_id, merchant_key}` → token → header `Authorization: UPAY <token>` → `POST /payment/merchant-payment-init/` (date = today Asia/Dhaka) → `data.gateway_url` → `GET /payment/single-payment-status/{txn_id}/`. Full refunds only by API. Production is IPv4 only.

### UddoktaPay (`uddoktapay`, experimental)
- Header `RT-UDDOKTAPAY-API-KEY`. `POST {base}/api/checkout-v2` → `payment_url`; return carries `invoice_id` → `POST {base}/api/verify-payment` → `status` COMPLETED. Opt-in only (`BDPAY_ALLOW_EXPERIMENTAL=1`) because wallet-number automation can breach MFS terms and Bangladesh Bank licensing rules.

## Part 6 — Configuration

| Variable | Meaning |
|---|---|
| `BDPAY_ENV` | `sandbox` or `live` |
| `BDPAY_GATEWAYS` | comma list, e.g. `bkash,nagad,sslcommerz` |
| `BDPAY_PUBLIC_URL` | public https base URL of this service (providers call it) |
| `BDPAY_SECRET` | ≥ 32 chars; signs callback URLs |
| `BDPAY_ADMIN_TOKEN` | if set, REST API needs header `X-BDPAY-ADMIN` |
| `BDPAY_DB` | SQLite path (default `bdpay.sqlite3`) |
| `BDPAY_RESULT_URL` | optional, e.g. `https://shop/thanks?order={order_id}&status={status}` |
| `BDPAY_CONSOLE` | `1` to allow the test console in live |
| `BDPAY_ALLOW_EXPERIMENTAL` | `1` to enable UddoktaPay |
| `BDPAY_LOG_LEVEL` | `INFO` default |

Published sandbox data: bKash demo wallet `01770618575`, OTP `123456`, PIN `12121` (bKash demo page). aamarPay store `aamarpaytest` / `dbb74894e82415a2f7ff0ec3a97e4183`. shurjoPay `sp_sandbox` / `pyyk97hu&6u6`, prefix `NOK`. SSLCOMMERZ: self-register for a sandbox store (docs samples use `testbox` / `qwerty`). UddoktaPay sandbox key `982d381360a69d419689740d9f2e26ce36fb7a50`. Nagad, Upay, EPS, PortPos, PayStation: from the provider. Community bKash sandbox app credentials circulate online but are not official; get your own.

## Part 7 — Integration

### 7.1 REST (any language)

| Method | Path | Body / result |
|---|---|---|
| POST | `/bdpay/orders` | `{order_id, amount, gateway, customer}` → order |
| POST | `/bdpay/orders/{id}/pay` | → `{redirect_url}` |
| GET | `/bdpay/orders/{id}` | order + events |
| POST | `/bdpay/orders/{id}/requery` | order |
| POST | `/bdpay/orders/{id}/refund` | `{amount?, reason}` |
| GET | `/bdpay/gateways` | enabled gateways |
| GET/POST | `/bdpay/callback/{gw}/{id}/{token}/{outcome}` | provider return → 303 to result |
| POST | `/bdpay/ipn/{gw}` | provider IPN |

Errors: `{"detail": {"code", "provider", "message"}}` with HTTP 400/402/403/404/409/502/503/504.

### 7.2 FastAPI
```python
from bdpay import BdPay
from bdpay.web import build_router
pay = BdPay.from_env()
app.include_router(build_router(pay, on_paid=fulfil, admin_token=os.environ.get("BDPAY_ADMIN_TOKEN")))
```

### 7.3 Django / Flask
Use `BdPay` directly; route `/bdpay/callback/<gateway>/<order_id>/<token>/<outcome>` (GET and POST, CSRF-exempt) to `pay.handle_callback(...)` with merged query+form params, and `/bdpay/ipn/<gateway>` to `pay.handle_ipn(...)`.

### 7.4 PHP / Laravel / WordPress / Node / mobile
Run the Docker service; call 7.1 from your backend. Mobile apps call your backend only.

## Part 8 — Test and deploy

- `python -m pytest -q` — offline, fake providers via `httpx.MockTransport`; covers success, cancel, failure, execute timeout → query, duplicate callback, amount tamper, forged link, token 401 refresh, verify_sign tamper, VALIDATED replay, refunds, Nagad RSA round trip.
- `python -m bdpay doctor` — config, keys, DNS/TCP reachability, Dhaka clock, host IP.
- `docker compose up -d --build` — service + reconcile worker (every 10 min), SQLite on a volume, keys mounted read-only, non-root user, health check.
- AWS: ECS Fargate/App Runner, Secrets Manager, NAT gateway Elastic IP for Nagad/Upay whitelisting; Postgres/Redis store for multiple replicas.
- Go-live: live credentials; `BDPAY_ENV=live`; new secret; https public URL; SSLCOMMERZ IPN set; Nagad/Upay IP whitelisted; one minimum real payment + refund per gateway; reconcile running; watch `needs_review`.

## Part 9 — Driver checklist (for new gateways)

1. Subclass `Driver`; set `id`, `label`, `stability`, `capabilities`, `required_env`.
2. Base URLs per environment + `<ID>_BASE_URL` override.
3. `create_payment` returns provider ID + redirect URL, raises `PROVIDER_DECLINED` with the provider's message.
4. `verify_return` rejects IDs that don't belong to the order, then calls the provider; never trusts params.
5. `query` maps provider statuses to `Status` and returns amount (poisha), currency, order ref, txn id.
6. Tokens via `self.tokens.get_or_fetch(key, grant)`.
7. Register in `registry.py`; add `tests/test_<id>.py` with a fake provider covering success and mismatch.
8. Document env vars, sandbox access and go-live steps.

## Part 10 — Reference source code

Every file below is the tested reference implementation (35 tests passing on Python 3.13). Recreate each file at its path.


### `.github/workflows/ci.yml`

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.10", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python }}" }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: python -m pytest -q
  secrets-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
```

### `app.py`

```python
"""Ready-to-run payment service + browser test console.

    pip install -r requirements.txt
    python -m bdpay doctor
    uvicorn app:app --reload        # open http://localhost:8000
"""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bdpay import BdPay
from bdpay.web import build_router

logging.basicConfig(level=os.environ.get("BDPAY_LOG_LEVEL", "INFO"))

pay = BdPay.from_env()


def on_paid(order):
    # >>> Put your fulfilment here: mark the invoice paid, send the receipt, ship the goods.
    logging.getLogger("app").info("PAID %s %s BDT via %s", order.order_id, order.amount, order.gateway)


app = FastAPI(title="bdpay", version="1.0.0")
app.include_router(build_router(pay, on_paid=on_paid, result_url=os.environ.get("BDPAY_RESULT_URL") or None,
                                admin_token=os.environ.get("BDPAY_ADMIN_TOKEN") or None))


@app.get("/health")
def health():
    return {"ok": True, "environment": pay.settings.environment, "gateways": list(pay.drivers)}


CONSOLE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>bdpay test console</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#1d2433}
.banner{padding:10px 14px;border-radius:8px;font-weight:600;margin-bottom:18px}
.sandbox{background:#e7f6ec;color:#0a6b34}.live{background:#b42318;color:#fff}
label{display:block;margin:12px 0 4px;font-weight:600}input,select{width:100%;padding:10px;border:1px solid #c9ced8;border-radius:8px;font-size:15px;box-sizing:border-box}
button{margin-top:18px;padding:12px 18px;border:0;border-radius:8px;background:#1f5eff;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
pre{background:#f4f6fa;padding:12px;border-radius:8px;overflow:auto;font-size:13px}</style></head><body>
<div class="banner __ENV__">__ENVTEXT__</div>
<h1>bdpay test console</h1>
<label>Gateway</label><select id=gw></select>
<label>Amount (BDT)</label><input id=amt value="10.00">
<label>Order ID (letters and digits)</label><input id=oid>
<label>Customer phone</label><input id=phone value="01700000000">
<label>Admin token (only if BDPAY_ADMIN_TOKEN is set)</label><input id=adm type=password>
<button onclick="go()">Create order and pay</button>
<pre id=out></pre>
<script>
const $=id=>document.getElementById(id);$("oid").value="INV"+Date.now();
fetch("/bdpay/gateways").then(r=>r.json()).then(g=>{$("gw").innerHTML=g.map(x=>`<option value="${x.id}">${x.label} (${x.stability})</option>`).join("")});
async function call(url,body){const r=await fetch(url,{method:"POST",headers:{"content-type":"application/json","x-bdpay-admin":$("adm").value},body:JSON.stringify(body||{})});const j=await r.json();if(!r.ok)throw j;return j}
async function go(){$("out").textContent="Working...";try{
 const o=await call("/bdpay/orders",{order_id:$("oid").value,amount:$("amt").value,gateway:$("gw").value,customer:{phone:$("phone").value,name:"Test Customer"}});
 const p=await call(`/bdpay/orders/${o.order_id}/pay`);$("out").textContent="Redirecting to "+p.redirect_url;location.href=p.redirect_url;
}catch(e){$("out").textContent=JSON.stringify(e,null,2)}}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def console():
    live = pay.settings.environment == "live"
    if live and os.environ.get("BDPAY_CONSOLE") != "1":
        return HTMLResponse("<h1>bdpay is running (live). Test console is off.</h1>")
    return CONSOLE.replace("__ENV__", "live" if live else "sandbox").replace(
        "__ENVTEXT__", "LIVE MODE: real money" if live else "Sandbox mode: no real money moves")
```

### `bdpay/__init__.py`

```python
"""bdpay - one Python integration for Bangladeshi payment gateways.

Not affiliated with bKash, Nagad, SSLCOMMERZ or any other provider. Merchant-side
integration software only: money always moves through the licensed provider.
"""
from .config import Settings
from .core import BdPayError, Order, Status, fmt_amount, to_poisha
from .service import BdPay

__all__ = ["BdPay", "BdPayError", "Order", "Settings", "Status", "fmt_amount", "to_poisha"]
__version__ = "1.0.0"
```

### `bdpay/__main__.py`

```python
"""Command line: python -m bdpay <doctor|reconcile|serve|requery ORDER_ID>"""
from __future__ import annotations

import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .config import Settings
from .core import BdPayError


def doctor() -> int:
    ok = True
    try:
        s = Settings.from_env()
    except BdPayError as e:
        print("✗", e)
        return 1
    print(f"environment : {s.environment}{'   <<< LIVE: real money >>>' if s.environment == 'live' else ''}")
    print(f"gateways    : {', '.join(s.gateways) or '(none) set BDPAY_GATEWAYS'}")
    print(f"public url  : {s.public_url}")
    if s.environment == "live" and not s.public_url.startswith("https://"):
        print("✗ BDPAY_PUBLIC_URL must be https:// in live mode"); ok = False
    if "localhost" in s.public_url or "127.0.0.1" in s.public_url:
        print("! public url is localhost: providers cannot reach your callback/IPN. Use ngrok or a real domain.")
    dhaka = datetime.now(timezone(timedelta(hours=6)))
    print(f"Dhaka time  : {dhaka:%Y-%m-%d %H:%M:%S} (Nagad/Upay timestamps use this)")
    from .registry import load_driver_class
    from .http import Http
    from .store import Store, TokenCache
    store = Store(":memory:")
    for name in s.gateways:
        try:
            cls = load_driver_class(name)
            d = cls(s.gateway(name), Http(s.environment), TokenCache(store))
            host = next((getattr(d, a) for a in ("url", "host", "base") if hasattr(d, a)), "")
            h = urlparse(host).hostname
            if h:
                t = time.monotonic()
                socket.create_connection((h, urlparse(host).port or (443 if host.startswith("https") else 80)), 5).close()
                print(f"✓ {name:<11} config ok, {h} reachable ({int((time.monotonic() - t) * 1000)} ms) [{d.stability}]")
            else:
                print(f"✓ {name:<11} config ok [{d.stability}]")
        except BdPayError as e:
            ok = False; print(f"✗ {name:<11} {e.message}")
        except OSError as e:
            ok = False; print(f"✗ {name:<11} network: {e}")
    try:
        ip = socket.gethostbyname(socket.gethostname())
        print(f"host ip     : {ip} (Nagad and Upay whitelist your public outbound IPv4, which may differ)")
    except OSError:
        pass
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "reconcile":
        from .service import BdPay
        for order_id, status in BdPay.from_env().reconcile():
            print(order_id, status)
        return 0
    if cmd == "requery" and len(argv) > 1:
        from .service import BdPay
        o = BdPay.from_env().requery(argv[1])
        print(o.order_id, o.status.value)
        return 0
    if cmd == "serve":
        import uvicorn
        uvicorn.run("app:app", host="0.0.0.0", port=int(argv[1]) if len(argv) > 1 else 8000)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

### `bdpay/config.py`

```python
"""Configuration. Everything comes from environment variables (a .env file is
loaded automatically if present). Secrets never live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class GatewayConfig:
    gateway: str
    environment: str
    values: Mapping[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        v = self.values.get(key)
        return v if v not in (None, "") else default

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.get(k)]
        if missing:
            from .core import BdPayError
            raise BdPayError("CONFIG", f"missing settings: {', '.join(missing)}", self.gateway)

    def __getitem__(self, key: str) -> str:
        self.require(key)
        return self.values[key]


@dataclass
class Settings:
    environment: str = "sandbox"
    gateways: tuple = ()
    secret: str = ""
    db_path: str = "bdpay.sqlite3"
    public_url: str = "http://localhost:8000"
    values: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, dotenv: bool = True) -> "Settings":
        if env is None:
            if dotenv:
                load_dotenv(os.environ.get("BDPAY_DOTENV", ".env"))
            env = os.environ
        environment = (env.get("BDPAY_ENV") or "sandbox").strip().lower()
        if environment not in ("sandbox", "live"):
            from .core import BdPayError
            raise BdPayError("CONFIG", "BDPAY_ENV must be 'sandbox' or 'live'")
        gateways = tuple(g.strip().lower() for g in (env.get("BDPAY_GATEWAYS") or "").split(",") if g.strip())
        secret = env.get("BDPAY_SECRET") or ""
        if environment == "live" and len(secret) < 32:
            from .core import BdPayError
            raise BdPayError("CONFIG", "BDPAY_SECRET must be at least 32 characters in live mode")
        if not secret:
            secret = "dev-only-insecure-secret-change-me-0000"
        return cls(
            environment=environment,
            gateways=gateways,
            secret=secret,
            db_path=env.get("BDPAY_DB") or "bdpay.sqlite3",
            public_url=(env.get("BDPAY_PUBLIC_URL") or "http://localhost:8000").rstrip("/"),
            values=dict(env),
        )

    def gateway(self, name: str) -> GatewayConfig:
        return GatewayConfig(name, self.environment, self.values)
```

### `bdpay/core.py`

```python
"""Core types shared by every gateway driver.

Money is always held as integer poisha (1 BDT = 100 poisha). Amounts are only
formatted as "100.00" strings at the edge, when talking to a provider.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Optional


class BdPayError(Exception):
    """Every error raised by bdpay. `code` is stable and safe to branch on."""

    CODES = {
        "CONFIG", "AUTH", "VALIDATION", "AMOUNT_MISMATCH", "SIGNATURE_INVALID",
        "PROVIDER_DECLINED", "PROVIDER_UNAVAILABLE", "TIMEOUT", "DUPLICATE",
        "NOT_SUPPORTED", "NOT_FOUND",
    }

    def __init__(self, code: str, message: str, provider: str = "core",
                 provider_code: Optional[str] = None, retryable: bool = False):
        assert code in self.CODES, code
        super().__init__(f"[{provider}:{code}] {message}")
        self.code = code
        self.message = message
        self.provider = provider
        self.provider_code = provider_code
        self.retryable = retryable


def to_poisha(amount: Any) -> int:
    """Convert '100', 100, 100.5, '100.50' or Decimal to integer poisha."""
    try:
        d = Decimal(str(amount).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise BdPayError("VALIDATION", f"invalid amount: {amount!r}")
    if not d.is_finite():
        raise BdPayError("VALIDATION", f"invalid amount: {amount!r}")
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(q * 100)


def fmt_amount(poisha: int) -> str:
    """Integer poisha -> provider string, e.g. 10050 -> '100.50'."""
    sign = "-" if poisha < 0 else ""
    p = abs(int(poisha))
    return f"{sign}{p // 100}.{p % 100:02d}"


class Status(str, enum.Enum):
    CREATED = "created"
    PENDING = "pending"              # customer sent to provider, not yet verified
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NEEDS_REVIEW = "needs_review"    # provider says paid but amount/order did not match
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


# Only forward moves are allowed. Anything else is ignored (idempotent).
ALLOWED = {
    Status.CREATED: {Status.PENDING, Status.PAID, Status.FAILED, Status.CANCELLED,
                     Status.EXPIRED, Status.NEEDS_REVIEW},
    Status.PENDING: {Status.PAID, Status.FAILED, Status.CANCELLED, Status.EXPIRED,
                     Status.NEEDS_REVIEW},
    # A late "paid" verification can still rescue a failed/cancelled/expired order.
    Status.FAILED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.CANCELLED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.EXPIRED: {Status.PAID, Status.NEEDS_REVIEW},
    Status.PAID: {Status.PARTIALLY_REFUNDED, Status.REFUNDED},
    Status.PARTIALLY_REFUNDED: {Status.PARTIALLY_REFUNDED, Status.REFUNDED},
    Status.NEEDS_REVIEW: set(),
    Status.REFUNDED: set(),
}


@dataclass
class Capabilities:
    refunds: str = "none"          # none | full | partial
    recurring: bool = False
    payouts: bool = False
    webhooks: str = "requery"      # signed | requery | none


@dataclass
class Order:
    order_id: str
    gateway: str
    amount_poisha: int
    currency: str = "BDT"
    status: Status = Status.CREATED
    customer: dict = field(default_factory=dict)
    provider_payment_id: Optional[str] = None
    provider_txn_id: Optional[str] = None
    refunded_poisha: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def amount(self) -> str:
        return fmt_amount(self.amount_poisha)


@dataclass
class CallbackUrls:
    success: str
    fail: str
    cancel: str
    ipn: str


@dataclass
class PaymentSession:
    provider_payment_id: Optional[str]
    redirect_url: str
    raw: Any = None


@dataclass
class Verified:
    """The provider's server-side answer about one payment."""
    status: Status
    amount_poisha: Optional[int] = None
    currency: Optional[str] = None
    order_ref: Optional[str] = None
    provider_payment_id: Optional[str] = None
    provider_txn_id: Optional[str] = None
    raw: Any = None


@dataclass
class RefundResult:
    ok: bool
    refund_ref: Optional[str] = None
    status: str = "requested"      # requested | processing | completed | failed
    raw: Any = None


class Driver(ABC):
    """Base class for a gateway driver. Subclasses never trust browser data:
    `verify_return` must always confirm the payment server-to-server."""

    id: str = ""
    label: str = ""
    stability: str = "stable"      # stable | beta | experimental
    capabilities = Capabilities()
    required_env: tuple = ()

    def __init__(self, cfg: "GatewayConfig", http: "Http", tokens: "TokenCache"):
        self.cfg = cfg
        self.http = http
        self.tokens = tokens
        self.env = cfg.environment
        cfg.require(*self.required_env)

    @abstractmethod
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession: ...

    @abstractmethod
    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified: ...

    @abstractmethod
    def query(self, order: Order) -> Verified: ...

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        raise BdPayError("NOT_SUPPORTED", "refunds are done in the provider panel", self.id)

    def ipn_lookup(self, params: Mapping[str, str]) -> Optional[tuple]:
        """Return ('order_id', value) or ('provider_payment_id', value) from an IPN body."""
        return None

    def fail(self, code: str, message: str, provider_code: Optional[str] = None,
             retryable: bool = False) -> BdPayError:
        return BdPayError(code, message, self.id, provider_code, retryable)


# Imported late to avoid circular imports in type hints.
from .config import GatewayConfig  # noqa: E402
from .http import Http  # noqa: E402
from .store import TokenCache  # noqa: E402
```

### `bdpay/drivers/__init__.py`

```python
"""One module per gateway. Each exposes a Driver subclass (see bdpay.core.Driver)."""
```

### `bdpay/drivers/aamarpay.py`

```python
"""aamarPay JSON API (developer.aamarpay.com). Success/fail posts are only a
hint; the Search Transaction API decides. status_code: 0 initiated,
2 successful, 3 expired, 7 failed."""
from __future__ import annotations

from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

HOST = {"sandbox": "https://sandbox.aamarpay.com", "live": "https://secure.aamarpay.com"}


class AamarpayDriver(Driver):
    id = "aamarpay"
    label = "aamarPay"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("AAMARPAY_STORE_ID", "AAMARPAY_SIGNATURE_KEY")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.host = (cfg.get("AAMARPAY_BASE_URL") or HOST[self.env]).rstrip("/")

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.host}/jsonpost.php", json={
            "store_id": self.cfg["AAMARPAY_STORE_ID"], "signature_key": self.cfg["AAMARPAY_SIGNATURE_KEY"],
            "tran_id": order.order_id, "amount": order.amount, "currency": "BDT",
            "desc": c.get("product") or f"Order {order.order_id}",
            "cus_name": c.get("name") or "Customer", "cus_email": c.get("email") or "customer@example.com",
            "cus_phone": c.get("phone") or "01700000000", "cus_add1": c.get("address") or "Dhaka",
            "cus_city": c.get("city") or "Dhaka", "cus_country": "Bangladesh",
            "success_url": urls.success, "fail_url": urls.fail, "cancel_url": urls.cancel,
            "opt_a": order.order_id, "type": "json",
        })
        if str(j.get("result")).lower() != "true" or not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", str(j))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("mer_txnid") and params["mer_txnid"] != order.order_id:
            raise self.fail("VALIDATION", "mer_txnid does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.host}/api/v1/trxcheck/request.php", retry=True, params={
            "request_id": order.order_id, "store_id": self.cfg["AAMARPAY_STORE_ID"],
            "signature_key": self.cfg["AAMARPAY_SIGNATURE_KEY"], "type": "json"})
        code = str(j.get("status_code", ""))
        status = {"2": Status.PAID, "0": Status.PENDING, "3": Status.EXPIRED, "7": Status.FAILED}.get(code)
        if status is None:
            status = Status.PAID if (j.get("pay_status") or "").lower() == "successful" else Status.PENDING
        amount = j.get("amount_original") or j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None,
                        j.get("currency_merchant") or j.get("currency"), j.get("mer_txnid"),
                        None, j.get("pg_txnid"), j)

    def ipn_lookup(self, params):
        return ("order_id", params["mer_txnid"]) if params.get("mer_txnid") else None
```

### `bdpay/drivers/bkash.py`

```python
"""bKash Tokenized Checkout (developer.bka.sh).

Flow: grant token (cached ~55 min) -> create (mode 0011) -> customer pays on
bkashURL -> callback ?paymentID&status -> execute -> if execute fails or times
out, query payment status. Only a Completed status with the right amount and
invoice number marks the order paid.
"""
from __future__ import annotations

from typing import Mapping

from ..core import (BdPayError, Capabilities, CallbackUrls, Driver, Order, PaymentSession,
                    RefundResult, Status, Verified, fmt_amount, to_poisha)

BASE = {"sandbox": "https://tokenized.sandbox.bka.sh", "live": "https://tokenized.pay.bka.sh"}


class BkashDriver(Driver):
    id = "bkash"
    label = "bKash Tokenized Checkout"
    capabilities = Capabilities(refunds="partial", recurring=True, webhooks="requery")
    required_env = ("BKASH_APP_KEY", "BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        version = cfg.get("BKASH_API_VERSION", "v1.2.0-beta")
        base = (cfg.get("BKASH_BASE_URL") or BASE[self.env]).rstrip("/")
        self.url = f"{base}/{version}/tokenized/checkout"
        self.app_key = cfg["BKASH_APP_KEY"]
        self.token_key = f"bkash:{self.env}:{self.app_key}"

    # ---------------------------------------------------------------- auth
    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.url}/token/grant",
                           headers={"username": self.cfg["BKASH_USERNAME"], "password": self.cfg["BKASH_PASSWORD"],
                                    "Accept": "application/json"},
                           json={"app_key": self.app_key, "app_secret": self.cfg["BKASH_APP_SECRET"]})
        if not j.get("id_token"):
            raise self.fail("AUTH", f"token grant failed: {j.get('statusMessage') or j.get('msg') or j}",
                            j.get("statusCode"))
        return j["id_token"], int(j.get("expires_in") or 3600)

    def _call(self, path: str, body: dict, retry: bool = False) -> dict:
        for attempt in (1, 2):
            token = self.tokens.get_or_fetch(self.token_key, self._grant)
            resp = self.http.request(self.id, "POST", f"{self.url}{path}", retry=retry, json=body,
                                     headers={"Authorization": token, "X-APP-Key": self.app_key,
                                              "Accept": "application/json"})
            if resp.status_code == 401 and attempt == 1:
                self.tokens.invalidate(self.token_key)   # token revoked early: get a fresh one once
                continue
            if resp.status_code >= 500:
                raise self.fail("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", retryable=True)
            try:
                return resp.json()
            except ValueError:
                raise self.fail("PROVIDER_UNAVAILABLE", f"non-JSON response: {resp.text[:200]!r}")
        raise self.fail("AUTH", "bKash rejected a fresh token (401)")

    # ---------------------------------------------------------------- payments
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        payer = (order.customer.get("phone") or order.order_id)[:255]
        j = self._call("/create", {
            "mode": "0011",
            "payerReference": payer,
            "callbackURL": urls.success,           # bKash appends ?paymentID=..&status=success|failure|cancel
            "amount": order.amount,
            "currency": "BDT",
            "intent": "sale",
            "merchantInvoiceNumber": order.order_id,
        })
        if j.get("statusCode") != "0000" or not j.get("bkashURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("statusMessage") or str(j), j.get("statusCode"))
        return PaymentSession(j["paymentID"], j["bkashURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        pid = params.get("paymentID") or order.provider_payment_id
        if order.provider_payment_id and pid != order.provider_payment_id:
            raise self.fail("VALIDATION", "paymentID does not belong to this order")
        status = (params.get("status") or "").lower()
        if params.get("_outcome") == "ipn":
            return self.query(order)
        if status == "cancel":
            return Verified(Status.CANCELLED, provider_payment_id=pid, raw=dict(params))
        if status != "success":
            return Verified(Status.FAILED, provider_payment_id=pid, raw=dict(params))
        try:
            j = self._call("/execute", {"paymentID": pid})
        except BdPayError as e:
            if e.code not in ("TIMEOUT", "PROVIDER_UNAVAILABLE"):
                raise
            j = None
        if not j or j.get("statusCode") != "0000":
            # Execute timed out, errored, or was already done (e.g. 2062): the query is authoritative.
            return self.query(order)
        return self._verified(j, pid)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self._call("/payment/status", {"paymentID": order.provider_payment_id}, retry=True)
        if j.get("statusCode") != "0000":
            raise self.fail("PROVIDER_UNAVAILABLE", j.get("statusMessage") or str(j), j.get("statusCode"))
        return self._verified(j, order.provider_payment_id)

    def _verified(self, j: dict, pid: str) -> Verified:
        ts = (j.get("transactionStatus") or "").lower()
        status = {"completed": Status.PAID, "initiated": Status.PENDING, "authorized": Status.PENDING,
                  "pending authorized": Status.PENDING, "cancelled": Status.CANCELLED,
                  "expired": Status.EXPIRED}.get(ts, Status.FAILED)
        return Verified(status, to_poisha(j["amount"]) if j.get("amount") else None, j.get("currency"),
                        j.get("merchantInvoiceNumber"), pid, j.get("trxID"), j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        if not order.provider_txn_id:
            raise self.fail("VALIDATION", "no bKash trxID stored for this order")
        j = self._call("/payment/refund", {
            "paymentID": order.provider_payment_id, "trxID": order.provider_txn_id,
            "amount": fmt_amount(amount_poisha), "sku": "bdpay", "reason": (reason or "refund")[:255],
        })
        ok = bool(j.get("refundTrxID")) and (j.get("transactionStatus") or "").lower() == "completed"
        if not ok and j.get("statusCode") not in (None, "0000"):
            raise self.fail("PROVIDER_DECLINED", j.get("statusMessage") or str(j), j.get("statusCode"))
        return RefundResult(ok, j.get("refundTrxID"), "completed" if ok else "failed", j)
```

### `bdpay/drivers/eps.py`

```python
"""EPS (Easy Payment System) - BETA. Check field names against the EPS merchant
portal / official SDKs (github.com/EPS-PG) before going live.

GetToken (JWT) -> InitializeEPS (RedirectURL) -> CheckMerchantTransactionStatus.
Every call carries x-hash = Base64(HMAC-SHA512(hash_key, <value>))."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandboxpgapi.eps.com.bd", "live": "https://pgapi.eps.com.bd"}


class EpsDriver(Driver):
    id = "eps"
    label = "EPS"
    stability = "beta"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("EPS_USERNAME", "EPS_PASSWORD", "EPS_HASH_KEY", "EPS_MERCHANT_ID", "EPS_STORE_ID")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("EPS_BASE_URL") or BASE[self.env]).rstrip("/")
        self.token_key = f"eps:{self.env}:{cfg['EPS_USERNAME']}"

    def _hash(self, value: str) -> str:
        return base64.b64encode(hmac.new(self.cfg["EPS_HASH_KEY"].encode(), value.encode(),
                                         hashlib.sha512).digest()).decode()

    def _grant(self):
        user = self.cfg["EPS_USERNAME"]
        j = self.http.json(self.id, "POST", f"{self.base}/v1/Auth/GetToken", headers={"x-hash": self._hash(user)},
                           json={"userName": user, "password": self.cfg["EPS_PASSWORD"]})
        if not j.get("token"):
            raise self.fail("AUTH", j.get("errorMessage") or str(j))
        return j["token"], 1800

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        token, c = self.tokens.get_or_fetch(self.token_key, self._grant), order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/v1/EPSEngine/InitializeEPS",
                           headers={"x-hash": self._hash(order.order_id), "Authorization": f"Bearer {token}"}, json={
            "merchantId": self.cfg["EPS_MERCHANT_ID"], "storeId": self.cfg["EPS_STORE_ID"],
            "CustomerOrderId": order.order_id, "merchantTransactionId": order.order_id,
            "transactionTypeId": 1, "totalAmount": float(order.amount),
            "successUrl": urls.success, "failUrl": urls.fail, "cancelUrl": urls.cancel,
            "customerName": c.get("name") or "Customer", "customerEmail": c.get("email") or "customer@example.com",
            "customerAddress": c.get("address") or "Dhaka", "customerCity": c.get("city") or "Dhaka",
            "customerState": c.get("city") or "Dhaka", "customerPostcode": c.get("postcode") or "1000",
            "customerCountry": "BD", "customerPhone": c.get("phone") or "01700000000",
            "productName": c.get("product") or f"Order {order.order_id}",
        })
        if not j.get("RedirectURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("ErrorMessage") or str(j), str(j.get("ErrorCode")))
        return PaymentSession(j.get("TransactionId"), j["RedirectURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        return self.query(order)

    def query(self, order: Order) -> Verified:
        token = self.tokens.get_or_fetch(self.token_key, self._grant)
        j = self.http.json(self.id, "GET", f"{self.base}/v1/EPSEngine/CheckMerchantTransactionStatus", retry=True,
                           params={"merchantTransactionId": order.order_id},
                           headers={"x-hash": self._hash(order.order_id), "Authorization": f"Bearer {token}"})
        st = (j.get("Status") or "").lower()
        status = {"success": Status.PAID, "failed": Status.FAILED, "cancel": Status.CANCELLED,
                  "cancelled": Status.CANCELLED, "pending": Status.PENDING}.get(st, Status.PENDING)
        amount = j.get("TotalAmount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        j.get("MerchantTransactionId"), None, j.get("EpsTransactionId"), j)
```

### `bdpay/drivers/nagad.py`

```python
"""Nagad merchant checkout (API version v-0.2.0).

Nagad keeps its official spec in the merchant integration pack. This driver
follows the flow several open-source SDKs agree on:
  initialize -> complete -> customer pays on callBackUrl -> verify.
sensitiveData is RSA-encrypted (PKCS#1 v1.5) with Nagad's public key and signed
(SHA256withRSA) with your merchant private key. Production needs your server
IP and callback domain whitelisted by Nagad. Check every value against the pack
Nagad sends you; base URLs are configurable for that reason.
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {
    "sandbox": "http://sandbox.mynagad.com:10080/remote-payment-gateway-1.0/api/dfs",
    "live": "https://api.mynagad.com/api/dfs",
}
DHAKA = timezone(timedelta(hours=6))      # Bangladesh has no daylight saving time


def _pem(text: str, kind: str) -> bytes:
    text = text.strip()
    if "BEGIN" in text:
        return text.encode()
    body = "\n".join(text[i:i + 64] for i in range(0, len(text), 64))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n".encode()


def load_private_key(text: str):
    try:
        return serialization.load_pem_private_key(_pem(text, "PRIVATE KEY"), password=None)
    except ValueError:
        return serialization.load_pem_private_key(_pem(text, "RSA PRIVATE KEY"), password=None)


def load_public_key(text: str):
    return serialization.load_pem_public_key(_pem(text, "PUBLIC KEY"))


def _dumps(d: dict) -> str:
    return json.dumps(d, separators=(",", ":"))


class NagadDriver(Driver):
    id = "nagad"
    label = "Nagad"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("NAGAD_MERCHANT_ID", "NAGAD_PRIVATE_KEY_PATH", "NAGAD_PUBLIC_KEY_PATH")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("NAGAD_BASE_URL") or BASE[self.env]).rstrip("/")
        self.mid = cfg["NAGAD_MERCHANT_ID"]
        self.merchant_number = cfg.get("NAGAD_MERCHANT_NUMBER")
        try:
            self.private_key = load_private_key(Path(cfg["NAGAD_PRIVATE_KEY_PATH"]).read_text())
            self.nagad_public = load_public_key(Path(cfg["NAGAD_PUBLIC_KEY_PATH"]).read_text())
        except (OSError, ValueError) as e:
            raise self.fail("CONFIG", f"cannot load Nagad keys: {e}")
        self.client_ip = cfg.get("NAGAD_CLIENT_IP", "127.0.0.1")

    # ------------------------------------------------------------- crypto
    def encrypt(self, data: dict) -> str:
        return base64.b64encode(self.nagad_public.encrypt(_dumps(data).encode(), padding.PKCS1v15())).decode()

    def sign(self, data: dict) -> str:
        return base64.b64encode(self.private_key.sign(_dumps(data).encode(), padding.PKCS1v15(),
                                                      hashes.SHA256())).decode()

    def decrypt(self, b64: str) -> str:
        return self.private_key.decrypt(base64.b64decode(b64), padding.PKCS1v15()).decode()

    def verify_signature(self, plaintext: str, b64sig: str) -> bool:
        try:
            self.nagad_public.verify(base64.b64decode(b64sig), plaintext.encode(), padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    def _headers(self, order: Order) -> dict:
        return {"Content-Type": "application/json", "X-KM-Api-Version": "v-0.2.0",
                "X-KM-Client-Type": "PC_WEB", "X-KM-IP-V4": order.customer.get("ip") or self.client_ip}

    # ------------------------------------------------------------- payments
    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        if not order.order_id.isalnum() or len(order.order_id) > 20:
            raise self.fail("VALIDATION", "Nagad order ids must be letters/digits only, max 20 characters")
        now = datetime.now(DHAKA).strftime("%Y%m%d%H%M%S")
        sensitive = {"merchantId": self.mid, "datetime": now, "orderId": order.order_id,
                     "challenge": secrets.token_hex(20)}
        body = {"dateTime": now, "sensitiveData": self.encrypt(sensitive), "signature": self.sign(sensitive)}
        if self.merchant_number:
            body["accountNumber"] = self.merchant_number
        j = self.http.json(self.id, "POST", f"{self.base}/check-out/initialize/{self.mid}/{order.order_id}",
                           json=body, headers=self._headers(order))
        if not j.get("sensitiveData"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), j.get("reason"))
        plain = self.decrypt(j["sensitiveData"])
        if j.get("signature") and not self.verify_signature(plain, j["signature"]):
            raise self.fail("SIGNATURE_INVALID", "initialize response signature is not from Nagad")
        init = json.loads(plain)
        ref, challenge = init["paymentReferenceId"], init["challenge"]
        sensitive2 = {"merchantId": self.mid, "orderId": order.order_id, "amount": order.amount,
                      "currencyCode": "050", "challenge": challenge}
        body2 = {"sensitiveData": self.encrypt(sensitive2), "signature": self.sign(sensitive2),
                 "merchantCallbackURL": urls.success,
                 "additionalMerchantInfo": {"orderId": order.order_id}}
        j2 = self.http.json(self.id, "POST", f"{self.base}/check-out/complete/{ref}", json=body2,
                            headers=self._headers(order))
        if (j2.get("status") or "").lower() != "success" or not j2.get("callBackUrl"):
            raise self.fail("PROVIDER_DECLINED", j2.get("message") or str(j2), j2.get("reason"))
        return PaymentSession(ref, j2["callBackUrl"], {"initialize": init, "complete": j2})

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        ref = params.get("payment_ref_id") or order.provider_payment_id
        if order.provider_payment_id and ref != order.provider_payment_id:
            raise self.fail("VALIDATION", "payment_ref_id does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self.http.json(self.id, "GET", f"{self.base}/verify/payment/{order.provider_payment_id}",
                           headers=self._headers(order), retry=True)
        st = (j.get("status") or "").lower()
        status = {"success": Status.PAID, "inprogress": Status.PENDING, "ready": Status.PENDING,
                  "cancelled": Status.CANCELLED, "aborted": Status.CANCELLED,
                  "expired": Status.EXPIRED}.get(st, Status.FAILED)
        return Verified(status, to_poisha(j["amount"]) if j.get("amount") else None, None,
                        j.get("orderId"), order.provider_payment_id, j.get("issuerPaymentRefNo"), j)
```

### `bdpay/drivers/paystation.py`

```python
"""PayStation - BETA. Confirm against paystation.com.bd/documentation and the
credentials PayStation issues you."""
from __future__ import annotations

from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandbox.paystation.com.bd", "live": "https://api.paystation.com.bd"}


class PaystationDriver(Driver):
    id = "paystation"
    label = "PayStation"
    stability = "beta"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("PAYSTATION_MERCHANT_ID", "PAYSTATION_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("PAYSTATION_BASE_URL") or BASE[self.env]).rstrip("/")

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/initiate-payment", data={
            "merchantId": self.cfg["PAYSTATION_MERCHANT_ID"], "password": self.cfg["PAYSTATION_PASSWORD"],
            "invoice_number": order.order_id, "currency": "BDT", "payment_amount": order.amount,
            "pay_with_charge": self.cfg.get("PAYSTATION_PAY_WITH_CHARGE", "0"), "reference": order.order_id,
            "cust_name": c.get("name") or "Customer", "cust_phone": c.get("phone") or "01700000000",
            "cust_email": c.get("email") or "customer@example.com", "cust_address": c.get("address") or "Dhaka",
            "callback_url": urls.success, "checkout_items": c.get("product") or f"Order {order.order_id}",
        })
        if str(j.get("status_code")) != "200" or not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), str(j.get("status_code")))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("invoice_number") and params["invoice_number"] != order.order_id:
            raise self.fail("VALIDATION", "invoice_number does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "POST", f"{self.base}/transaction-status", retry=True,
                           headers={"merchantId": self.cfg["PAYSTATION_MERCHANT_ID"]},
                           data={"invoice_number": order.order_id})
        d = j.get("data") or {}
        st = (d.get("trx_status") or "").lower()
        status = {"success": Status.PAID, "successful": Status.PAID, "failed": Status.FAILED,
                  "cancel": Status.CANCELLED, "cancelled": Status.CANCELLED}.get(st, Status.PENDING)
        amount = d.get("payment_amount") or d.get("request_amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        d.get("invoice_number"), None, d.get("trx_id"), j)

    def ipn_lookup(self, params):
        return ("order_id", params["invoice_number"]) if params.get("invoice_number") else None
```

### `bdpay/drivers/portpos.py`

```python
"""PortPos (formerly PortWallet) invoice API v2 - BETA. Confirm against your
PortPos sandbox panel. Auth: Bearer base64(app_key:md5(secret_key + unix_ts))."""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, fmt_amount, to_poisha)

BASE = {"sandbox": "https://api-sandbox.portpos.com/payment/v2", "live": "https://api.portpos.com/payment/v2"}


class PortposDriver(Driver):
    id = "portpos"
    label = "PortPos"
    stability = "beta"
    capabilities = Capabilities(refunds="partial", recurring=True, webhooks="requery")
    required_env = ("PORTPOS_APP_KEY", "PORTPOS_SECRET_KEY")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("PORTPOS_BASE_URL") or BASE[self.env]).rstrip("/")

    def _headers(self) -> dict:
        ts = str(int(time.time()))
        token = hashlib.md5((self.cfg["PORTPOS_SECRET_KEY"] + ts).encode()).hexdigest()
        auth = base64.b64encode(f"{self.cfg['PORTPOS_APP_KEY']}:{token}".encode()).decode()
        return {"Authorization": f"Bearer {auth}", "Content-Type": "application/json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/invoice", headers=self._headers(), json={
            "order": {"amount": float(order.amount), "currency": "BDT", "redirect_url": urls.success,
                      "ipn_url": urls.ipn, "reference": order.order_id, "validity": 3600},
            "product": {"name": c.get("product") or f"Order {order.order_id}", "description": order.order_id},
            "billing": {"customer": {"name": c.get("name") or "Customer", "email": c.get("email") or "customer@example.com",
                                     "phone": c.get("phone") or "01700000000",
                                     "address": {"street": c.get("address") or "Dhaka", "city": c.get("city") or "Dhaka",
                                                 "state": c.get("city") or "Dhaka", "zipcode": c.get("postcode") or "1000",
                                                 "country": "BGD"}}},
        })
        data = j.get("data") or {}
        url = (data.get("action") or {}).get("url")
        if not url:
            raise self.fail("PROVIDER_DECLINED", str(j.get("error") or j))
        return PaymentSession(data.get("invoice_id"), url, j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        inv = params.get("invoice") or params.get("invoice_id")
        if inv and order.provider_payment_id and inv != order.provider_payment_id:
            raise self.fail("VALIDATION", "invoice does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        j = self.http.json(self.id, "GET", f"{self.base}/invoice/{order.provider_payment_id}",
                           headers=self._headers(), retry=True)
        o = (j.get("data") or {}).get("order") or {}
        st = (o.get("status") or "").upper()
        status = {"ACCEPTED": Status.PAID, "REJECTED": Status.FAILED, "FAILED": Status.FAILED,
                  "CANCELLED": Status.CANCELLED, "EXPIRED": Status.EXPIRED}.get(st, Status.PENDING)
        amount = o.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, o.get("currency"),
                        o.get("reference") or None, order.provider_payment_id, None, j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        j = self.http.json(self.id, "POST", f"{self.base}/invoice/refund/{order.provider_payment_id}",
                           headers=self._headers(),
                           json={"refund": {"amount": float(fmt_amount(amount_poisha)), "currency": "BDT",
                                            "reason": reason}})
        ok = (j.get("result") or "").lower() == "success"
        return RefundResult(ok, ((j.get("data") or {}).get("refund") or {}).get("id"),
                            "processing" if ok else "failed", j)

    def ipn_lookup(self, params):
        inv = params.get("invoice") or params.get("invoice_id")
        return ("provider_payment_id", inv) if inv else None
```

### `bdpay/drivers/shurjopay.py`

```python
"""shurjoPay v2.1. get_token -> secret-pay -> customer pays -> verification.
sp_code 1000 = success."""
from __future__ import annotations

import json
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, Status,
                    Verified, to_poisha)

BASE = {"sandbox": "https://sandbox.shurjopayment.com", "live": "https://engine.shurjopayment.com"}


class ShurjopayDriver(Driver):
    id = "shurjopay"
    label = "shurjoPay"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("SHURJOPAY_USERNAME", "SHURJOPAY_PASSWORD", "SHURJOPAY_PREFIX")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = (cfg.get("SHURJOPAY_BASE_URL") or BASE[self.env]).rstrip("/")
        self.token_key = f"shurjopay:{self.env}:{cfg['SHURJOPAY_USERNAME']}"

    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.base}/api/get_token", json={
            "username": self.cfg["SHURJOPAY_USERNAME"], "password": self.cfg["SHURJOPAY_PASSWORD"]})
        if not j.get("token"):
            raise self.fail("AUTH", j.get("message") or str(j), str(j.get("sp_code")))
        return json.dumps({"token": j["token"], "store_id": j.get("store_id")}), int(j.get("expires_in") or 3000)

    def _auth(self) -> dict:
        return json.loads(self.tokens.get_or_fetch(self.token_key, self._grant))

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        a, c = self._auth(), order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/api/secret-pay",
                           headers={"Authorization": f"Bearer {a['token']}"}, json={
            "prefix": self.cfg["SHURJOPAY_PREFIX"], "token": a["token"], "store_id": a["store_id"],
            "return_url": urls.success, "cancel_url": urls.cancel,
            "amount": order.amount, "order_id": order.order_id, "currency": "BDT",
            "customer_name": c.get("name") or "Customer", "customer_phone": c.get("phone") or "01700000000",
            "customer_email": c.get("email") or "customer@example.com",
            "customer_address": c.get("address") or "Dhaka", "customer_city": c.get("city") or "Dhaka",
            "customer_post_code": c.get("postcode") or "1000", "client_ip": c.get("ip") or "127.0.0.1",
            "value1": order.order_id,
        })
        if not j.get("checkout_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), str(j.get("sp_code")))
        return PaymentSession(j.get("sp_order_id"), j["checkout_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("order_id") and order.provider_payment_id and params["order_id"] != order.provider_payment_id:
            raise self.fail("VALIDATION", "order_id does not belong to this order")
        return self.query(order)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        resp = self.http.request(self.id, "POST", f"{self.base}/api/verification", retry=True,
                                 headers={"Authorization": f"Bearer {self._auth()['token']}"},
                                 json={"order_id": order.provider_payment_id})
        if resp.status_code == 401:
            self.tokens.invalidate(self.token_key)
            raise self.fail("AUTH", "token expired; retry", retryable=True)
        j = resp.json()
        rec = j[0] if isinstance(j, list) and j else j if isinstance(j, dict) else {}
        code = str(rec.get("sp_code", ""))
        status = {"1000": Status.PAID, "1001": Status.FAILED, "1002": Status.CANCELLED}.get(code, Status.PENDING)
        amount = rec.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, rec.get("currency"),
                        rec.get("customer_order_id"), order.provider_payment_id, rec.get("bank_trx_id"), rec)
```

### `bdpay/drivers/sslcommerz.py`

```python
"""SSLCOMMERZ v4 (developer.sslcommerz.com/doc/v4). Cards, internet banking and
wallets (incl. bKash, Nagad, Rocket, Upay, OK Wallet...) through one gateway.

The IPN is not trusted on its own: verify_sign is checked, then the Order
Validation API is called, then tran_id/amount/currency are compared with the
stored order. VALIDATED means "already validated" and is treated as paid.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, fmt_amount, to_poisha)

HOST = {"sandbox": "https://sandbox.sslcommerz.com", "live": "https://securepay.sslcommerz.com"}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def check_verify_sign(params: Mapping[str, str], store_passwd: str) -> bool:
    keys = [k for k in (params.get("verify_key") or "").split(",") if k]
    if not keys or not params.get("verify_sign"):
        return False
    data = {k: params.get(k, "") for k in keys}
    data["store_passwd"] = _md5(store_passwd)
    s = "&".join(f"{k}={data[k]}" for k in sorted(data))
    return _md5(s) == params["verify_sign"]


class SslcommerzDriver(Driver):
    id = "sslcommerz"
    label = "SSLCOMMERZ"
    capabilities = Capabilities(refunds="partial", webhooks="requery")
    required_env = ("SSLCOMMERZ_STORE_ID", "SSLCOMMERZ_STORE_PASSWORD")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.host = (cfg.get("SSLCOMMERZ_BASE_URL") or HOST[self.env]).rstrip("/")
        self.store_id = cfg["SSLCOMMERZ_STORE_ID"]
        self.passwd = cfg["SSLCOMMERZ_STORE_PASSWORD"]

    def _auth(self) -> dict:
        return {"store_id": self.store_id, "store_passwd": self.passwd, "format": "json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        data = {
            "store_id": self.store_id, "store_passwd": self.passwd,
            "total_amount": order.amount, "currency": "BDT", "tran_id": order.order_id,
            "success_url": urls.success, "fail_url": urls.fail, "cancel_url": urls.cancel, "ipn_url": urls.ipn,
            "cus_name": c.get("name") or "Customer", "cus_email": c.get("email") or "customer@example.com",
            "cus_add1": c.get("address") or "Dhaka", "cus_city": c.get("city") or "Dhaka",
            "cus_postcode": c.get("postcode") or "1000", "cus_country": "Bangladesh",
            "cus_phone": c.get("phone") or "01700000000",
            "shipping_method": "NO", "num_of_item": "1",
            "product_name": c.get("product") or "Order " + order.order_id,
            "product_category": c.get("category") or "general", "product_profile": "general",
            "value_a": order.order_id,
        }
        j = self.http.json(self.id, "POST", f"{self.host}/gwprocess/v4/api.php", data=data)
        if (j.get("status") or "").upper() != "SUCCESS" or not j.get("GatewayPageURL"):
            raise self.fail("PROVIDER_DECLINED", j.get("failedreason") or str(j))
        return PaymentSession(j.get("sessionkey"), j["GatewayPageURL"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        if params.get("verify_sign") and not check_verify_sign(params, self.passwd):
            raise self.fail("SIGNATURE_INVALID", "verify_sign does not match")
        if params.get("tran_id") and params["tran_id"] != order.order_id:
            raise self.fail("VALIDATION", "tran_id does not belong to this order")
        val_id = params.get("val_id")
        if not val_id:
            return self.query(order)              # fail/cancel posts carry no val_id
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/validationserverAPI.php",
                           params={"val_id": val_id, **self._auth()}, retry=True)
        return self._from_record(j)

    def _from_record(self, j: dict) -> Verified:
        st = (j.get("status") or "").upper()
        status = {"VALID": Status.PAID, "VALIDATED": Status.PAID, "PENDING": Status.PENDING,
                  "FAILED": Status.FAILED, "CANCELLED": Status.CANCELLED,
                  "UNATTEMPTED": Status.PENDING, "EXPIRED": Status.EXPIRED}.get(st, Status.FAILED)
        currency = j.get("currency_type") or j.get("currency")
        amount = j.get("currency_amount") if j.get("currency_type") else j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, currency,
                        j.get("tran_id"), j.get("sessionkey"), j.get("bank_tran_id"), j)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/merchantTransIDvalidationAPI.php",
                           params={"tran_id": order.order_id, **self._auth()}, retry=True)
        if j.get("APIConnect") not in (None, "DONE"):
            raise self.fail("PROVIDER_UNAVAILABLE", f"APIConnect={j.get('APIConnect')}")
        elements = j.get("element") or []
        for e in elements:
            if (e.get("status") or "").upper() in ("VALID", "VALIDATED"):
                return self._from_record(e)
        if any((e.get("status") or "").upper() == "PENDING" for e in elements):
            return Verified(Status.PENDING, raw=j)
        if elements:
            return self._from_record(elements[0])
        return Verified(Status.PENDING, raw=j)    # not attempted yet

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        if not order.provider_txn_id:
            raise self.fail("VALIDATION", "no bank_tran_id stored for this order")
        j = self.http.json(self.id, "GET", f"{self.host}/validator/api/merchantTransIDvalidationAPI.php",
                           params={"bank_tran_id": order.provider_txn_id, "refund_amount": fmt_amount(amount_poisha),
                                   "refund_remarks": (reason or "refund")[:250], **self._auth()})
        ok = j.get("APIConnect") == "DONE" and (j.get("status") or "").lower() in ("success", "processing")
        return RefundResult(ok, j.get("refund_ref_id"), "processing" if ok else "failed", j)

    def ipn_lookup(self, params):
        return ("order_id", params["tran_id"]) if params.get("tran_id") else None
```

### `bdpay/drivers/uddoktapay.py`

```python
"""UddoktaPay - EXPERIMENTAL.

COMPLIANCE WARNING: UddoktaPay-style tools verify payments sent to personal or
agent wallet numbers. MFS terms generally ban commercial collection into
personal accounts, and the Payment and Settlement Systems Act 2024 requires a
Bangladesh Bank licence to provide payment services. Use only with a merchant
wallet and legal advice. Disabled unless BDPAY_ALLOW_EXPERIMENTAL=1."""
from __future__ import annotations

from typing import Mapping

from ..core import (BdPayError, Capabilities, CallbackUrls, Driver, Order, PaymentSession,
                    Status, Verified, to_poisha)


class UddoktapayDriver(Driver):
    id = "uddoktapay"
    label = "UddoktaPay (experimental)"
    stability = "experimental"
    capabilities = Capabilities(refunds="none", webhooks="requery")
    required_env = ("UDDOKTAPAY_BASE_URL", "UDDOKTAPAY_API_KEY")

    def __init__(self, cfg, http, tokens):
        if cfg.get("BDPAY_ALLOW_EXPERIMENTAL") != "1":
            raise BdPayError("CONFIG", "uddoktapay is experimental: set BDPAY_ALLOW_EXPERIMENTAL=1 after reading "
                                       "the compliance warning in bdpay/drivers/uddoktapay.py", "uddoktapay")
        super().__init__(cfg, http, tokens)
        self.base = cfg["UDDOKTAPAY_BASE_URL"].rstrip("/")

    def _h(self) -> dict:
        return {"RT-UDDOKTAPAY-API-KEY": self.cfg["UDDOKTAPAY_API_KEY"], "Accept": "application/json"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        c = order.customer
        j = self.http.json(self.id, "POST", f"{self.base}/api/checkout-v2", headers=self._h(), json={
            "full_name": c.get("name") or "Customer", "email": c.get("email") or "customer@example.com",
            "amount": order.amount, "metadata": {"order_id": order.order_id},
            "redirect_url": urls.success, "return_type": "GET", "cancel_url": urls.cancel, "webhook_url": urls.ipn,
        })
        if not j.get("payment_url"):
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j))
        return PaymentSession(None, j["payment_url"], j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        inv = params.get("invoice_id")
        if not inv:
            return Verified(Status.CANCELLED if params.get("_outcome") == "cancel" else Status.PENDING)
        j = self.http.json(self.id, "POST", f"{self.base}/api/verify-payment", headers=self._h(),
                           json={"invoice_id": inv}, retry=True)
        st = (j.get("status") or "").upper()
        status = {"COMPLETED": Status.PAID, "PENDING": Status.PENDING, "ERROR": Status.FAILED}.get(st, Status.FAILED)
        ref = (j.get("metadata") or {}).get("order_id")
        amount = j.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None, ref,
                        inv, j.get("transaction_id"), j)

    def query(self, order: Order) -> Verified:
        if not order.provider_payment_id:
            return Verified(Status.PENDING)
        return self.verify_return(order, {"invoice_id": order.provider_payment_id})

    def ipn_lookup(self, params):
        meta = params.get("metadata") or {}
        oid = meta.get("order_id") if isinstance(meta, dict) else None
        return ("order_id", oid) if oid else None
```

### `bdpay/drivers/upay.py`

```python
"""Upay (UCB Fintech) - BETA. Credentials, base URL and the v4.x API PDF come
only through Upay merchant onboarding; set UPAY_BASE_URL from that pack.
Production accepts IPv4 only. Refunds by API are full refunds only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from ..core import (Capabilities, CallbackUrls, Driver, Order, PaymentSession, RefundResult,
                    Status, Verified, to_poisha)

DHAKA = timezone(timedelta(hours=6))


class UpayDriver(Driver):
    id = "upay"
    label = "Upay"
    stability = "beta"
    capabilities = Capabilities(refunds="full", webhooks="requery")
    required_env = ("UPAY_BASE_URL", "UPAY_MERCHANT_ID", "UPAY_MERCHANT_KEY", "UPAY_MERCHANT_CODE",
                    "UPAY_MERCHANT_NAME", "UPAY_MERCHANT_MOBILE")

    def __init__(self, cfg, http, tokens):
        super().__init__(cfg, http, tokens)
        self.base = cfg["UPAY_BASE_URL"].rstrip("/")
        self.token_key = f"upay:{self.env}:{cfg['UPAY_MERCHANT_ID']}"

    def _grant(self):
        j = self.http.json(self.id, "POST", f"{self.base}/payment/merchant-auth/", json={
            "merchant_id": self.cfg["UPAY_MERCHANT_ID"], "merchant_key": self.cfg["UPAY_MERCHANT_KEY"]})
        token = (j.get("data") or {}).get("token")
        if not token:
            raise self.fail("AUTH", j.get("message") or str(j), j.get("code"))
        return token, 3000

    def _h(self) -> dict:
        return {"Authorization": f"UPAY {self.tokens.get_or_fetch(self.token_key, self._grant)}"}

    def create_payment(self, order: Order, urls: CallbackUrls) -> PaymentSession:
        j = self.http.json(self.id, "POST", f"{self.base}/payment/merchant-payment-init/", headers=self._h(), json={
            "date": datetime.now(DHAKA).strftime("%Y-%m-%d"),       # must be today's date in Asia/Dhaka
            "txn_id": order.order_id, "invoice_id": order.order_id, "amount": order.amount,
            "merchant_id": self.cfg["UPAY_MERCHANT_ID"], "merchant_name": self.cfg["UPAY_MERCHANT_NAME"],
            "merchant_code": self.cfg["UPAY_MERCHANT_CODE"], "merchant_mobile": self.cfg["UPAY_MERCHANT_MOBILE"],
            "merchant_country_code": "BD", "merchant_city": self.cfg.get("UPAY_MERCHANT_CITY", "Dhaka"),
            "merchant_category_code": self.cfg.get("UPAY_MERCHANT_CATEGORY_CODE", "5999"),
            "transaction_currency_code": "BDT", "redirect_url": urls.success,
        })
        url = (j.get("data") or {}).get("gateway_url")
        if not url:
            raise self.fail("PROVIDER_DECLINED", j.get("message") or str(j), j.get("code"))
        return PaymentSession(order.order_id, url, j)

    def verify_return(self, order: Order, params: Mapping[str, str]) -> Verified:
        return self.query(order)

    def query(self, order: Order) -> Verified:
        j = self.http.json(self.id, "GET", f"{self.base}/payment/single-payment-status/{order.order_id}/",
                           headers=self._h(), retry=True)
        d = j.get("data") or {}
        st = (d.get("status") or "").lower()
        status = {"success": Status.PAID, "successful": Status.PAID, "failed": Status.FAILED,
                  "cancelled": Status.CANCELLED, "expired": Status.EXPIRED}.get(st, Status.PENDING)
        amount = d.get("amount")
        return Verified(status, to_poisha(amount) if amount not in (None, "") else None, None,
                        d.get("txn_id") or d.get("invoice_id"), order.order_id, d.get("trx_id"), j)

    def refund(self, order: Order, amount_poisha: int, reason: str) -> RefundResult:
        j = self.http.json(self.id, "POST", f"{self.base}/payment/refund/", headers=self._h(),
                           json={"txn_id": order.order_id, "reason": reason})
        ok = str(j.get("code", "")).upper().endswith("200") or (j.get("data") or {}).get("status") == "success"
        return RefundResult(ok, (j.get("data") or {}).get("refund_id"), "processing" if ok else "failed", j)
```

### `bdpay/http.py`

```python
"""HTTP client policy: timeouts, safe retries, TLS always on, redacted logs."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Optional

import httpx

from .core import BdPayError

log = logging.getLogger("bdpay.http")

_SECRET_KEY = re.compile(r"(secret|passw|passwd|token|key|signature|authorization|sensitive|hash)", re.I)
_MSISDN = re.compile(r"\b(01[3-9])(\d{4})(\d{4})\b")


def redact(obj: Any) -> Any:
    """Mask secrets and Bangladeshi mobile numbers before anything is logged."""
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _MSISDN.sub(lambda m: f"{m.group(1)}****{m.group(3)}", obj)
    return obj


class Http:
    def __init__(self, environment: str, transport: Optional[httpx.BaseTransport] = None,
                 connect_timeout: float = 5.0, read_timeout: float = 30.0, retries: int = 3):
        self.environment = environment
        self.retries = retries
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            verify=True,              # never configurable: TLS verification stays on
            follow_redirects=False,
            headers={"User-Agent": "bdpay-python/1.0"},
        )

    def request(self, provider: str, method: str, url: str, *, retry: bool = False,
                **kwargs: Any) -> httpx.Response:
        if self.environment == "live" and not url.startswith("https://"):
            raise BdPayError("CONFIG", f"live mode refuses non-HTTPS URL: {url}", provider)
        attempts = self.retries if retry else 1
        last: Optional[Exception] = None
        for i in range(attempts):
            started = time.monotonic()
            try:
                resp = self.client.request(method, url, **kwargs)
                log.info(json.dumps({
                    "provider": provider, "method": method, "url": url.split("?")[0],
                    "status": resp.status_code, "ms": int((time.monotonic() - started) * 1000),
                }))
                if resp.status_code >= 500 and i < attempts - 1:
                    last = BdPayError("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", provider, retryable=True)
                    self._sleep(i)
                    continue
                return resp
            except httpx.TimeoutException:
                last = BdPayError("TIMEOUT", f"{method} {url.split('?')[0]} timed out", provider, retryable=True)
            except httpx.TransportError as e:
                last = BdPayError("PROVIDER_UNAVAILABLE", f"network error: {e.__class__.__name__}", provider, retryable=True)
            if i < attempts - 1:
                self._sleep(i)
        assert last is not None
        raise last

    @staticmethod
    def _sleep(i: int) -> None:
        time.sleep(min(4.0, 0.25 * (2 ** i)) + random.uniform(0, 0.2))

    def json(self, provider: str, method: str, url: str, **kwargs: Any) -> Any:
        resp = self.request(provider, method, url, **kwargs)
        if resp.status_code >= 500:
            raise BdPayError("PROVIDER_UNAVAILABLE", f"HTTP {resp.status_code}", provider, retryable=True)
        try:
            data = resp.json()
        except ValueError:
            raise BdPayError("PROVIDER_UNAVAILABLE",
                             f"non-JSON response (HTTP {resp.status_code}): {resp.text[:200]!r}", provider)
        log.debug("response %s %s", provider, json.dumps(redact(data))[:2000])
        return data

    def close(self) -> None:
        self.client.close()
```

### `bdpay/registry.py`

```python
"""Gateway registry. Drivers are imported lazily, so a project only needs the
driver files for the gateways it enables in BDPAY_GATEWAYS."""
from __future__ import annotations

import importlib

from .core import BdPayError

DRIVERS = {
    "bkash": "bdpay.drivers.bkash:BkashDriver",
    "nagad": "bdpay.drivers.nagad:NagadDriver",
    "sslcommerz": "bdpay.drivers.sslcommerz:SslcommerzDriver",
    "aamarpay": "bdpay.drivers.aamarpay:AamarpayDriver",
    "shurjopay": "bdpay.drivers.shurjopay:ShurjopayDriver",
    "eps": "bdpay.drivers.eps:EpsDriver",
    "portpos": "bdpay.drivers.portpos:PortposDriver",
    "paystation": "bdpay.drivers.paystation:PaystationDriver",
    "upay": "bdpay.drivers.upay:UpayDriver",
    "uddoktapay": "bdpay.drivers.uddoktapay:UddoktapayDriver",
}


def load_driver_class(name: str):
    target = DRIVERS.get(name)
    if not target:
        raise BdPayError("CONFIG", f"unknown gateway '{name}'. Known: {', '.join(sorted(DRIVERS))}")
    module, _, cls = target.partition(":")
    try:
        return getattr(importlib.import_module(module), cls)
    except ModuleNotFoundError as e:
        raise BdPayError("CONFIG", f"driver file for '{name}' is missing ({module}): {e}")
```

### `bdpay/security.py`

```python
"""Signed, expiring callback URLs. A forged or replayed return URL for another
order fails here before any provider call is made."""
from __future__ import annotations

import hashlib
import hmac
import time


def sign_callback(secret: str, gateway: str, order_id: str, ttl_seconds: int = 86400) -> str:
    exp = int(time.time()) + ttl_seconds
    mac = hmac.new(secret.encode(), f"{gateway}|{order_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{mac}"


def check_callback(secret: str, gateway: str, order_id: str, token: str) -> bool:
    try:
        exp_s, mac = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    good = hmac.new(secret.encode(), f"{gateway}|{order_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, mac)
```

### `bdpay/service.py`

```python
"""BdPay: the one object your application talks to.

    pay = BdPay.from_env()
    pay.create_order("INV-1001", "1250.00", "bkash", {"name": "Rahim", "phone": "01700000000"})
    url = pay.start_payment("INV-1001")          # redirect the customer here
    # ...provider sends the customer back to /bdpay/callback/... which calls:
    order = pay.handle_callback(gateway, order_id, token, outcome, params)
    if order.status == Status.PAID: fulfil()
"""
from __future__ import annotations

import logging
from typing import Mapping, Optional

from .config import Settings
from .core import (BdPayError, CallbackUrls, Driver, Order, RefundResult, Status,
                   Verified, fmt_amount, to_poisha)
from .http import Http
from .registry import load_driver_class
from .security import check_callback, sign_callback
from .store import Store, TokenCache

log = logging.getLogger("bdpay")


class BdPay:
    def __init__(self, settings: Settings, store: Optional[Store] = None, transport=None,
                 callback_prefix: str = "/bdpay"):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.tokens = TokenCache(self.store)
        self.http = Http(settings.environment, transport=transport)
        self.callback_prefix = callback_prefix.rstrip("/")
        self.drivers: dict = {}
        for name in settings.gateways:
            cls = load_driver_class(name)
            self.drivers[name] = cls(settings.gateway(name), self.http, self.tokens)
        if not self.drivers:
            log.warning("no gateways enabled: set BDPAY_GATEWAYS, e.g. BDPAY_GATEWAYS=bkash,sslcommerz")

    @classmethod
    def from_env(cls, **kwargs) -> "BdPay":
        return cls(Settings.from_env(), **kwargs)

    # ------------------------------------------------------------------ helpers
    def driver(self, gateway: str) -> Driver:
        d = self.drivers.get(gateway)
        if not d:
            raise BdPayError("CONFIG", f"gateway '{gateway}' is not enabled (BDPAY_GATEWAYS)")
        return d

    def _order(self, order_id: str) -> Order:
        o = self.store.get(order_id)
        if not o:
            raise BdPayError("NOT_FOUND", f"order {order_id} not found")
        return o

    def callback_urls(self, order: Order) -> CallbackUrls:
        tok = sign_callback(self.settings.secret, order.gateway, order.order_id)
        base = f"{self.settings.public_url}{self.callback_prefix}/callback/{order.gateway}/{order.order_id}/{tok}"
        return CallbackUrls(success=f"{base}/success", fail=f"{base}/fail", cancel=f"{base}/cancel",
                            ipn=f"{self.settings.public_url}{self.callback_prefix}/ipn/{order.gateway}")

    # ------------------------------------------------------------------ API
    def create_order(self, order_id: str, amount, gateway: str, customer: Optional[dict] = None,
                     currency: str = "BDT") -> Order:
        """Record the order on the server. The amount saved here is the only
        amount bdpay will ever accept as paid."""
        self.driver(gateway)
        if currency != "BDT":
            raise BdPayError("VALIDATION", "only BDT is supported")
        if not order_id or len(order_id) > 40 or not all(c.isalnum() or c in "-_" for c in order_id):
            raise BdPayError("VALIDATION", "order_id must be 1-40 chars of letters, digits, - or _")
        poisha = to_poisha(amount)
        if poisha <= 0:
            raise BdPayError("VALIDATION", "amount must be greater than zero")
        return self.store.create_order(Order(order_id=order_id, gateway=gateway, amount_poisha=poisha,
                                             currency=currency, customer=customer or {}))

    def start_payment(self, order_id: str) -> str:
        order = self._order(order_id)
        if order.status not in (Status.CREATED, Status.PENDING):
            raise BdPayError("VALIDATION", f"order is {order.status.value}; cannot start payment")
        session = self.driver(order.gateway).create_payment(order, self.callback_urls(order))
        self.store.set_provider_ids(order.order_id, payment_id=session.provider_payment_id)
        self.store.transition(order.order_id, Status.PENDING, {"provider_payment_id": session.provider_payment_id})
        return session.redirect_url

    def handle_callback(self, gateway: str, order_id: str, token: str, outcome: str,
                        params: Mapping[str, str]) -> Order:
        """Customer came back from the provider. Never trusted: re-verified server-side."""
        if not check_callback(self.settings.secret, gateway, order_id, token):
            raise BdPayError("SIGNATURE_INVALID", "callback link is invalid or expired")
        order = self._order(order_id)
        if order.gateway != gateway:
            raise BdPayError("VALIDATION", "gateway does not match the order")
        if order.status in (Status.PAID, Status.REFUNDED, Status.PARTIALLY_REFUNDED, Status.NEEDS_REVIEW):
            return order                              # already settled: duplicate return is a no-op
        params = dict(params)
        params.setdefault("_outcome", outcome)
        verified = self.driver(gateway).verify_return(order, params)
        return self._apply(order, verified, source=f"callback:{outcome}")

    def handle_ipn(self, gateway: str, params: Mapping[str, str]) -> Optional[Order]:
        """Server-to-server notification. Used only as a hint to re-verify."""
        d = self.driver(gateway)
        ref = d.ipn_lookup(params)
        if not ref:
            raise BdPayError("VALIDATION", "IPN does not identify an order")
        kind, value = ref
        order = self.store.get(value) if kind == "order_id" else self.store.find_by_provider_id(gateway, value)
        if not order or order.gateway != gateway:
            raise BdPayError("NOT_FOUND", "IPN refers to an unknown order")
        if order.status in (Status.PAID, Status.REFUNDED, Status.PARTIALLY_REFUNDED, Status.NEEDS_REVIEW):
            return order
        verified = d.verify_return(order, {**dict(params), "_outcome": "ipn"})
        return self._apply(order, verified, source="ipn")

    def requery(self, order_id: str) -> Order:
        order = self._order(order_id)
        if order.status in (Status.REFUNDED, Status.NEEDS_REVIEW):
            return order
        verified = self.driver(order.gateway).query(order)
        return self._apply(order, verified, source="requery")

    def refund(self, order_id: str, amount=None, reason: str = "customer request") -> RefundResult:
        order = self._order(order_id)
        if order.status not in (Status.PAID, Status.PARTIALLY_REFUNDED):
            raise BdPayError("VALIDATION", f"order is {order.status.value}; only paid orders can be refunded")
        d = self.driver(order.gateway)
        refundable = order.amount_poisha - order.refunded_poisha
        poisha = refundable if amount is None else to_poisha(amount)
        if poisha <= 0 or poisha > refundable:
            raise BdPayError("VALIDATION", f"refund must be between 0.01 and {fmt_amount(refundable)}")
        if d.capabilities.refunds == "none":
            raise BdPayError("NOT_SUPPORTED", "this gateway refunds through its merchant panel", d.id)
        if d.capabilities.refunds == "full" and poisha != refundable:
            raise BdPayError("NOT_SUPPORTED", "this gateway only supports full refunds by API", d.id)
        result = d.refund(order, poisha, reason)
        self.store.event(order_id, "refund", order.status.value, None,
                         {"amount_poisha": poisha, "ok": result.ok, "ref": result.refund_ref, "status": result.status})
        if result.ok:
            total = order.refunded_poisha + poisha
            to = Status.REFUNDED if total >= order.amount_poisha else Status.PARTIALLY_REFUNDED
            self.store.transition(order_id, to, {"refund_ref": result.refund_ref}, refunded_poisha=total)
        return result

    def reconcile(self, older_than_minutes: int = 10, expire_after_hours: int = 24) -> list:
        """Re-check every payment left pending (customer closed the tab, callback lost...)."""
        out = []
        for order in self.store.stale(older_than_minutes * 60):
            try:
                updated = self.requery(order.order_id)
                if updated.status == Status.PENDING and order.created_at < __import__("time").time() - expire_after_hours * 3600:
                    updated, _ = self.store.transition(order.order_id, Status.EXPIRED, {"reason": "reconcile timeout"})
                out.append((order.order_id, updated.status.value))
            except BdPayError as e:
                out.append((order.order_id, f"error: {e.code}"))
        return out

    # ------------------------------------------------------------------ core rule
    def _apply(self, order: Order, v: Verified, source: str) -> Order:
        if v.provider_payment_id or v.provider_txn_id:
            self.store.set_provider_ids(order.order_id, payment_id=None if order.provider_payment_id else v.provider_payment_id,
                                        txn_id=v.provider_txn_id)
        detail = {"source": source, "provider_status": v.status.value, "amount_poisha": v.amount_poisha,
                  "currency": v.currency, "order_ref": v.order_ref, "txn": v.provider_txn_id}
        if v.status == Status.PAID:
            problems = []
            if v.amount_poisha is None or v.amount_poisha != order.amount_poisha:
                problems.append(f"amount {v.amount_poisha} != {order.amount_poisha}")
            if v.currency and v.currency.upper() not in ("BDT", "050"):
                problems.append(f"currency {v.currency}")
            if v.order_ref and str(v.order_ref) != order.order_id:
                problems.append(f"order ref {v.order_ref} != {order.order_id}")
            if problems:
                detail["problems"] = problems
                updated, _ = self.store.transition(order.order_id, Status.NEEDS_REVIEW, detail)
                log.error("payment %s needs review: %s", order.order_id, "; ".join(problems))
                return updated
        if v.status == Status.PENDING and order.status == Status.PENDING:
            self.store.event(order.order_id, "still_pending", order.status.value, None, detail)
            return self._order(order.order_id)
        updated, _ = self.store.transition(order.order_id, v.status, detail)
        return updated
```

### `bdpay/store.py`

```python
"""SQLite store: orders (idempotent state machine), audit events, token cache.

SQLite is the zero-setup default. For several app servers behind a load
balancer, point BDPAY_DB at a shared volume or swap this class for Postgres +
Redis using the same method names.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Callable, Optional, Tuple

from .core import ALLOWED, BdPayError, Order, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  gateway TEXT NOT NULL,
  amount_poisha INTEGER NOT NULL CHECK (amount_poisha > 0),
  currency TEXT NOT NULL DEFAULT 'BDT',
  status TEXT NOT NULL,
  customer TEXT NOT NULL DEFAULT '{}',
  provider_payment_id TEXT,
  provider_txn_id TEXT,
  refunded_poisha INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_provider_payment ON orders(gateway, provider_payment_id);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status, updated_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL,
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str = "bdpay.sqlite3"):
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)

    # ---------- orders ----------
    def _row(self, r: sqlite3.Row) -> Order:
        return Order(
            order_id=r["order_id"], gateway=r["gateway"], amount_poisha=r["amount_poisha"],
            currency=r["currency"], status=Status(r["status"]), customer=json.loads(r["customer"]),
            provider_payment_id=r["provider_payment_id"], provider_txn_id=r["provider_txn_id"],
            refunded_poisha=r["refunded_poisha"], created_at=r["created_at"], updated_at=r["updated_at"],
        )

    def create_order(self, order: Order) -> Order:
        """Insert, or return the existing order if the same id, gateway and amount
        were already created (safe to retry). A conflicting duplicate raises."""
        now = time.time()
        with self._lock:
            existing = self.get(order.order_id)
            if existing:
                if (existing.gateway, existing.amount_poisha, existing.currency) == \
                        (order.gateway, order.amount_poisha, order.currency):
                    return existing
                raise BdPayError("DUPLICATE", f"order {order.order_id} already exists with different details")
            self.db.execute(
                "INSERT INTO orders (order_id, gateway, amount_poisha, currency, status, customer,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (order.order_id, order.gateway, order.amount_poisha, order.currency,
                 Status.CREATED.value, json.dumps(order.customer), now, now))
            self.event(order.order_id, "created", None, Status.CREATED.value, {"amount_poisha": order.amount_poisha})
            return self.get(order.order_id)  # type: ignore[return-value]

    def get(self, order_id: str) -> Optional[Order]:
        r = self.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return self._row(r) if r else None

    def find_by_provider_id(self, gateway: str, provider_payment_id: str) -> Optional[Order]:
        r = self.db.execute("SELECT * FROM orders WHERE gateway=? AND provider_payment_id=?",
                            (gateway, provider_payment_id)).fetchone()
        return self._row(r) if r else None

    def set_provider_ids(self, order_id: str, payment_id: Optional[str] = None,
                         txn_id: Optional[str] = None) -> None:
        with self._lock:
            if payment_id:
                self.db.execute("UPDATE orders SET provider_payment_id=?, updated_at=? WHERE order_id=?",
                                (payment_id, time.time(), order_id))
            if txn_id:
                self.db.execute("UPDATE orders SET provider_txn_id=?, updated_at=? WHERE order_id=?",
                                (txn_id, time.time(), order_id))

    def transition(self, order_id: str, to: Status, detail: Optional[dict] = None,
                   refunded_poisha: Optional[int] = None) -> Tuple[Order, bool]:
        """Move an order forward. Returns (order, changed). Backward or repeated
        moves are no-ops, which makes duplicate callbacks and IPNs harmless."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = self.get(order_id)
                if cur is None:
                    raise BdPayError("NOT_FOUND", f"order {order_id} not found")
                if to == cur.status and to != Status.PARTIALLY_REFUNDED:
                    self.db.execute("COMMIT")
                    return cur, False
                if to not in ALLOWED[cur.status]:
                    self.event(order_id, "ignored_transition", cur.status.value, to.value, detail)
                    self.db.execute("COMMIT")
                    return cur, False
                if refunded_poisha is not None:
                    self.db.execute("UPDATE orders SET status=?, refunded_poisha=?, updated_at=? WHERE order_id=?",
                                    (to.value, refunded_poisha, time.time(), order_id))
                else:
                    self.db.execute("UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
                                    (to.value, time.time(), order_id))
                self.event(order_id, "transition", cur.status.value, to.value, detail)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return self.get(order_id), True  # type: ignore[return-value]

    def stale(self, older_than_seconds: float, statuses=(Status.PENDING,)) -> list:
        cutoff = time.time() - older_than_seconds
        marks = ",".join("?" for _ in statuses)
        rows = self.db.execute(f"SELECT * FROM orders WHERE status IN ({marks}) AND updated_at < ?",
                               (*[s.value for s in statuses], cutoff)).fetchall()
        return [self._row(r) for r in rows]

    def event(self, order_id: str, kind: str, from_status: Optional[str], to_status: Optional[str],
              detail: Optional[dict] = None) -> None:
        from .http import redact
        self.db.execute("INSERT INTO events (order_id, at, kind, from_status, to_status, detail) VALUES (?,?,?,?,?,?)",
                        (order_id, time.time(), kind, from_status, to_status,
                         json.dumps(redact(detail or {}), default=str)[:20000]))

    def events(self, order_id: str) -> list:
        rows = self.db.execute("SELECT at, kind, from_status, to_status, detail FROM events WHERE order_id=? ORDER BY id",
                               (order_id,)).fetchall()
        return [dict(r) for r in rows]


class TokenCache:
    """Shared access-token cache with single-flight refresh.

    bKash tokens last 3600 s and bKash may lock merchants that grant a new token
    on every request, so tokens are reused and refreshed 5 minutes early."""

    def __init__(self, store: Store, early_refresh_seconds: int = 300):
        self.store = store
        self.early = early_refresh_seconds
        self._locks: dict = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_or_fetch(self, key: str, fetch: Callable[[], Tuple[str, int]]) -> str:
        cached = self._read(key)
        if cached:
            return cached
        with self._lock_for(key):
            cached = self._read(key)       # another thread may have refreshed meanwhile
            if cached:
                return cached
            value, ttl = fetch()
            self.store.db.execute("INSERT OR REPLACE INTO tokens (key, value, expires_at) VALUES (?,?,?)",
                                  (key, value, time.time() + max(60, int(ttl))))
            return value

    def _read(self, key: str) -> Optional[str]:
        r = self.store.db.execute("SELECT value, expires_at FROM tokens WHERE key=?", (key,)).fetchone()
        if r and r["expires_at"] - self.early > time.time():
            return r["value"]
        return None

    def invalidate(self, key: str) -> None:
        self.store.db.execute("DELETE FROM tokens WHERE key=?", (key,))
```

### `bdpay/web.py`

```python
"""FastAPI router. Mount it in any FastAPI app:

    from fastapi import FastAPI
    from bdpay import BdPay
    from bdpay.web import build_router

    pay = BdPay.from_env()
    app = FastAPI()
    app.include_router(build_router(pay, on_paid=lambda order: print("fulfil", order.order_id)))
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .core import BdPayError, Order, Status
from .service import BdPay

log = logging.getLogger("bdpay.web")

HTTP = {"VALIDATION": 400, "AMOUNT_MISMATCH": 409, "DUPLICATE": 409, "NOT_FOUND": 404, "CONFIG": 500,
        "SIGNATURE_INVALID": 403, "NOT_SUPPORTED": 400, "AUTH": 502, "PROVIDER_DECLINED": 402,
        "PROVIDER_UNAVAILABLE": 503, "TIMEOUT": 504}


class CreateOrder(BaseModel):
    order_id: str = Field(..., examples=["INV1001"])
    amount: str = Field(..., examples=["10.00"])
    gateway: str = Field(..., examples=["bkash"])
    customer: dict = Field(default_factory=dict)


class RefundIn(BaseModel):
    amount: Optional[str] = None
    reason: str = "customer request"


def order_json(o: Order) -> dict:
    return {"order_id": o.order_id, "gateway": o.gateway, "amount": o.amount, "currency": o.currency,
            "status": o.status.value, "provider_payment_id": o.provider_payment_id,
            "provider_txn_id": o.provider_txn_id, "refunded": f"{o.refunded_poisha / 100:.2f}"}


def _err(e: BdPayError) -> HTTPException:
    return HTTPException(HTTP.get(e.code, 400), {"code": e.code, "provider": e.provider, "message": e.message})


async def _params(request: Request) -> dict:
    params = dict(request.query_params)
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "json" in ctype:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            except ValueError:
                pass
        else:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
    return params


def build_router(pay: BdPay, on_paid: Optional[Callable[[Order], None]] = None,
                 result_url: Optional[str] = None, admin_token: Optional[str] = None) -> APIRouter:
    """result_url: where customers land after paying, e.g. "https://shop.example/thanks?order={order_id}".
    admin_token: if set, refund/requery/order APIs require header X-BDPAY-ADMIN."""
    r = APIRouter(prefix=pay.callback_prefix, tags=["bdpay"])

    def guard(request: Request):
        if admin_token and request.headers.get("x-bdpay-admin") != admin_token:
            raise HTTPException(401, "missing or wrong X-BDPAY-ADMIN header")

    def notify(o: Order, before: Status):
        if on_paid and o.status == Status.PAID and before != Status.PAID:
            try:
                on_paid(o)
            except Exception:
                log.exception("on_paid hook failed for %s", o.order_id)

    @r.get("/gateways")
    def gateways():
        return [{"id": d.id, "label": d.label, "stability": d.stability,
                 "refunds": d.capabilities.refunds} for d in pay.drivers.values()]

    @r.post("/orders")
    def create(body: CreateOrder, request: Request):
        guard(request)
        try:
            return order_json(pay.create_order(body.order_id, body.amount, body.gateway, body.customer))
        except BdPayError as e:
            raise _err(e)

    @r.post("/orders/{order_id}/pay")
    def start(order_id: str, request: Request):
        guard(request)
        try:
            return {"redirect_url": pay.start_payment(order_id)}
        except BdPayError as e:
            raise _err(e)

    @r.get("/orders/{order_id}")
    def get(order_id: str, request: Request):
        guard(request)
        o = pay.store.get(order_id)
        if not o:
            raise HTTPException(404, "not found")
        return {**order_json(o), "events": pay.store.events(order_id)}

    @r.post("/orders/{order_id}/requery")
    def requery(order_id: str, request: Request):
        guard(request)
        try:
            before = pay.store.get(order_id)
            o = pay.requery(order_id)
            if before:
                notify(o, before.status)
            return order_json(o)
        except BdPayError as e:
            raise _err(e)

    @r.post("/orders/{order_id}/refund")
    def refund(order_id: str, body: RefundIn, request: Request):
        guard(request)
        try:
            res = pay.refund(order_id, body.amount, body.reason)
            return {"ok": res.ok, "refund_ref": res.refund_ref, "status": res.status,
                    "order": order_json(pay.store.get(order_id))}
        except BdPayError as e:
            raise _err(e)

    @r.api_route("/callback/{gateway}/{order_id}/{token}/{outcome}", methods=["GET", "POST"])
    async def callback(gateway: str, order_id: str, token: str, outcome: str, request: Request):
        params = await _params(request)
        before = pay.store.get(order_id)
        try:
            o = pay.handle_callback(gateway, order_id, token, outcome, params)
        except BdPayError as e:
            log.warning("callback rejected: %s", e)
            if e.code == "SIGNATURE_INVALID":
                return HTMLResponse("<h1>Invalid or expired payment link</h1>", status_code=403)
            o = pay.store.get(order_id)
            if not o:
                return HTMLResponse("<h1>Unknown order</h1>", status_code=404)
        if before:
            notify(o, before.status)
        if result_url:
            return RedirectResponse(result_url.format(order_id=o.order_id, status=o.status.value), status_code=303)
        return RedirectResponse(f"{pay.callback_prefix}/result/{o.order_id}", status_code=303)

    @r.api_route("/ipn/{gateway}", methods=["POST", "GET"])
    async def ipn(gateway: str, request: Request):
        params = await _params(request)
        try:
            lookup = pay.driver(gateway).ipn_lookup(params)
            before = None
            if lookup and lookup[0] == "order_id":
                before = pay.store.get(lookup[1])
            elif lookup:
                before = pay.store.find_by_provider_id(gateway, lookup[1])
            o = pay.handle_ipn(gateway, params)
            if o and before:
                notify(o, before.status)
            return {"ok": True, "status": o.status.value if o else None}
        except BdPayError as e:
            log.warning("ipn rejected: %s", e)
            return JSONResponse({"ok": False, "code": e.code}, status_code=HTTP.get(e.code, 400))

    @r.get("/result/{order_id}", response_class=HTMLResponse)
    def result(order_id: str):
        o = pay.store.get(order_id)
        if not o:
            return HTMLResponse("<h1>Unknown order</h1>", status_code=404)
        ok = o.status == Status.PAID
        color = "#0a7d38" if ok else "#b42318"
        return HTMLResponse(f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Payment {o.status.value}</title><body style="font-family:system-ui;max-width:520px;margin:40px auto;padding:0 16px">
<h1 style="color:{color}">{'Payment successful' if ok else 'Payment ' + o.status.value.replace('_', ' ')}</h1>
<p>Order <b>{o.order_id}</b> · ৳{o.amount} · {o.gateway}</p>
<p><a href="/">Back to test console</a></p></body>""")

    return r
```

### `.dockerignore`

```
.env
.env.*
keys/
*.sqlite3*
__pycache__/
.pytest_cache/
.git/
tests/
```

### `.env.example`

```
BDPAY_ENV=sandbox
BDPAY_GATEWAYS=bkash,sslcommerz
BDPAY_PUBLIC_URL=https://your-domain.example
BDPAY_SECRET=
BDPAY_ADMIN_TOKEN=
BDPAY_DB=bdpay.sqlite3
BDPAY_RESULT_URL=
BDPAY_LOG_LEVEL=INFO

# bKash
BKASH_APP_KEY=
BKASH_APP_SECRET=
BKASH_USERNAME=
BKASH_PASSWORD=
BKASH_API_VERSION=v1.2.0-beta
# Nagad
NAGAD_MERCHANT_ID=
NAGAD_MERCHANT_NUMBER=
NAGAD_PRIVATE_KEY_PATH=keys/nagad_merchant_private.pem
NAGAD_PUBLIC_KEY_PATH=keys/nagad_public.pem
NAGAD_CLIENT_IP=
# SSLCOMMERZ
SSLCOMMERZ_STORE_ID=
SSLCOMMERZ_STORE_PASSWORD=
# aamarPay
AAMARPAY_STORE_ID=aamarpaytest
AAMARPAY_SIGNATURE_KEY=dbb74894e82415a2f7ff0ec3a97e4183
# shurjoPay
SHURJOPAY_USERNAME=sp_sandbox
SHURJOPAY_PASSWORD=pyyk97hu&6u6
SHURJOPAY_PREFIX=NOK
# EPS
EPS_USERNAME=
EPS_PASSWORD=
EPS_HASH_KEY=
EPS_MERCHANT_ID=
EPS_STORE_ID=
# PortPos
PORTPOS_APP_KEY=
PORTPOS_SECRET_KEY=
# PayStation
PAYSTATION_MERCHANT_ID=
PAYSTATION_PASSWORD=
# Upay
UPAY_BASE_URL=
UPAY_MERCHANT_ID=
UPAY_MERCHANT_KEY=
UPAY_MERCHANT_CODE=
UPAY_MERCHANT_NAME=
UPAY_MERCHANT_MOBILE=
UPAY_MERCHANT_CATEGORY_CODE=5999
# UddoktaPay (experimental)
BDPAY_ALLOW_EXPERIMENTAL=
UDDOKTAPAY_BASE_URL=https://sandbox.uddoktapay.com
UDDOKTAPAY_API_KEY=
```

### `.gitignore`

```
.env
.env.*
!.env.example
keys/
*.pem
*.sqlite3*
__pycache__/
.pytest_cache/
.venv/
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BDPAY_DB=/data/bdpay.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bdpay ./bdpay
COPY app.py .
RUN useradd --system --uid 10001 bdpay && mkdir -p /data && chown bdpay /data
USER bdpay
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

### `LICENSE`

```
MIT License

Copyright (c) 2026 Mohammad M. Zaman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### `docker-compose.yml`

```yaml
# docker compose up -d --build   ->  http://localhost:8000
services:
  bdpay:
    build: .
    env_file: .env
    environment:
      BDPAY_DB: /data/bdpay.sqlite3
      BDPAY_DOTENV: /nonexistent        # env comes from env_file, not a copied .env
    ports: ["8000:8000"]
    volumes:
      - bdpay-data:/data
      - ./keys:/app/keys:ro
    restart: unless-stopped

  # Re-checks payments left pending (closed tabs, lost callbacks) every 10 minutes.
  reconcile:
    build: .
    env_file: .env
    environment:
      BDPAY_DB: /data/bdpay.sqlite3
      BDPAY_DOTENV: /nonexistent
    volumes:
      - bdpay-data:/data
      - ./keys:/app/keys:ro
    command: ["sh", "-c", "while true; do python -m bdpay reconcile; sleep 600; done"]
    restart: unless-stopped

volumes:
  bdpay-data: {}
```

### `pyproject.toml`

```toml
[project]
name = "bdpay-service"
version = "1.0.0"
description = "One Python integration for Bangladeshi payment gateways (bKash, Nagad, SSLCOMMERZ, aamarPay, shurjoPay, EPS, PortPos, PayStation, Upay)"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
dependencies = ["httpx>=0.27", "fastapi>=0.110", "uvicorn[standard]>=0.29", "python-multipart>=0.0.9"]

[project.optional-dependencies]
nagad = ["cryptography>=42"]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["bdpay", "bdpay.drivers"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### `requirements-dev.txt`

```text
pytest>=8.0
```

### `requirements.txt`

```text
httpx>=0.27,<1
fastapi>=0.110
uvicorn[standard]>=0.29
python-multipart>=0.0.9
cryptography>=42
```

### `tests/conftest.py`

```python
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bdpay import BdPay, Settings  # noqa: E402
from bdpay.store import Store  # noqa: E402


def make_pay(gateways, env_extra, handler):
    env = {"BDPAY_ENV": "sandbox", "BDPAY_GATEWAYS": ",".join(gateways), "BDPAY_SECRET": "s" * 40,
           "BDPAY_PUBLIC_URL": "https://shop.test", **env_extra}
    return BdPay(Settings.from_env(env), store=Store(":memory:"), transport=httpx.MockTransport(handler))


def callback_parts(url):
    """https://shop.test/bdpay/callback/<gw>/<order>/<token>/<outcome>?a=b -> (gw, order, token, outcome)"""
    p = urlparse(url).path.split("/")
    i = p.index("callback")
    return p[i + 1], p[i + 2], p[i + 3], p[i + 4]


def body(request: httpx.Request) -> dict:
    raw = request.content.decode() if request.content else ""
    if not raw:
        return {}
    if "json" in request.headers.get("content-type", ""):
        return json.loads(raw)
    return {k: v[0] for k, v in parse_qs(raw).items()}
```

### `tests/test_aamarpay.py`

```python
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
```

### `tests/test_bkash.py`

```python
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
```

### `tests/test_core.py`

```python
import pytest

from bdpay import BdPayError, Status, fmt_amount, to_poisha
from bdpay.core import Capabilities, Driver, PaymentSession, Verified
from bdpay.http import redact
from bdpay.security import check_callback, sign_callback
from conftest import callback_parts, make_pay


def test_money_round_trip():
    assert to_poisha("100") == 10000
    assert to_poisha("100.5") == 10050
    assert to_poisha(1250.255) == 125026
    assert to_poisha("1,250.00") == 125000
    assert fmt_amount(10050) == "100.50"
    assert fmt_amount(5) == "0.05"
    with pytest.raises(BdPayError):
        to_poisha("abc")
    with pytest.raises(BdPayError):
        to_poisha("NaN")


def test_signed_callback():
    tok = sign_callback("k" * 40, "bkash", "INV1")
    assert check_callback("k" * 40, "bkash", "INV1", tok)
    assert not check_callback("k" * 40, "bkash", "INV2", tok)
    assert not check_callback("k" * 40, "nagad", "INV1", tok)
    assert not check_callback("other" * 8, "bkash", "INV1", tok)
    assert not check_callback("k" * 40, "bkash", "INV1", "garbage")


def test_redaction():
    r = redact({"store_passwd": "x", "id_token": "y", "msg": "call 01712345678", "n": {"app_secret": 1}})
    assert r["store_passwd"] == "***" and r["id_token"] == "***" and r["n"]["app_secret"] == "***"
    assert r["msg"] == "call 017****5678"


class FakeDriver(Driver):
    id = "fake"
    capabilities = Capabilities(refunds="partial")
    answer = None

    def create_payment(self, order, urls):
        return PaymentSession("P-" + order.order_id, "https://provider.test/pay")

    def verify_return(self, order, params):
        return self.answer

    def query(self, order):
        return self.answer


def fake_pay():
    pay = make_pay([], {}, lambda r: None)
    pay.drivers["fake"] = FakeDriver(pay.settings.gateway("fake"), pay.http, pay.tokens)
    return pay, pay.drivers["fake"]


def test_paid_flow_and_duplicate_callback_is_noop():
    pay, d = fake_pay()
    pay.create_order("INV1", "10.00", "fake")
    assert pay.start_payment("INV1") == "https://provider.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV1")).success)
    d.answer = Verified(Status.PAID, 1000, "BDT", "INV1", "P-INV1", "TX1")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.PAID
    d.answer = Verified(Status.FAILED)
    assert pay.handle_callback(gw, oid, tok, "fail", {}).status == Status.PAID   # cannot go backwards
    assert pay.store.get("INV1").provider_txn_id == "TX1"


def test_amount_tamper_goes_to_review():
    pay, d = fake_pay()
    pay.create_order("INV2", "500.00", "fake")
    pay.start_payment("INV2")
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV2")).success)
    d.answer = Verified(Status.PAID, 100, "BDT", "INV2")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.NEEDS_REVIEW


def test_wrong_order_ref_goes_to_review():
    pay, d = fake_pay()
    pay.create_order("INV3", "5", "fake")
    pay.start_payment("INV3")
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV3")).success)
    d.answer = Verified(Status.PAID, 500, "BDT", "SOMEONE_ELSE")
    assert pay.handle_callback(gw, oid, tok, "success", {}).status == Status.NEEDS_REVIEW


def test_forged_callback_rejected():
    pay, d = fake_pay()
    pay.create_order("INV4", "5", "fake")
    with pytest.raises(BdPayError) as e:
        pay.handle_callback("fake", "INV4", "123.abc", "success", {})
    assert e.value.code == "SIGNATURE_INVALID"


def test_order_idempotent_and_conflict():
    pay, d = fake_pay()
    a = pay.create_order("INV5", "5", "fake")
    b = pay.create_order("INV5", "5.00", "fake")
    assert a.order_id == b.order_id
    with pytest.raises(BdPayError) as e:
        pay.create_order("INV5", "6", "fake")
    assert e.value.code == "DUPLICATE"


def test_refund_limits():
    pay, d = fake_pay()
    pay.create_order("INV6", "100", "fake")
    pay.start_payment("INV6")
    d.answer = Verified(Status.PAID, 10000, "BDT", "INV6")
    pay.requery("INV6")
    from bdpay.core import RefundResult
    d.refund = lambda order, amount, reason: RefundResult(True, "R1", "completed")
    with pytest.raises(BdPayError):
        pay.refund("INV6", "100.01")
    pay.refund("INV6", "40")
    assert pay.store.get("INV6").status == Status.PARTIALLY_REFUNDED
    pay.refund("INV6")
    o = pay.store.get("INV6")
    assert o.status == Status.REFUNDED and o.refunded_poisha == 10000


def test_live_requires_strong_secret():
    from bdpay import Settings
    with pytest.raises(BdPayError):
        Settings.from_env({"BDPAY_ENV": "live", "BDPAY_SECRET": "short"})


def test_live_refuses_plain_http():
    from bdpay.http import Http
    with pytest.raises(BdPayError):
        Http("live").request("x", "GET", "http://example.com")
```

### `tests/test_eps.py`

```python
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
```

### `tests/test_nagad.py`

```python
import base64
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from bdpay import Status
from conftest import callback_parts, make_pay


def keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub_der = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return k, priv, base64.b64encode(pub_der).decode()     # Nagad hands out bare base64 keys


def test_full_flow(tmp_path):
    merchant, merchant_priv_pem, merchant_pub_b64 = keypair()
    nagad, _, nagad_pub_b64 = keypair()
    (tmp_path / "m.pem").write_text(merchant_priv_pem)
    (tmp_path / "n.pub").write_text(nagad_pub_b64)
    merchant_pub = merchant.public_key()

    def dec(b64):
        return json.loads(nagad.decrypt(base64.b64decode(b64), padding.PKCS1v15()))

    def check_sig(data, sig):
        merchant_pub.verify(base64.b64decode(sig), json.dumps(data, separators=(",", ":")).encode(),
                            padding.PKCS1v15(), hashes.SHA256())

    def handler(req):
        p = req.url.path
        assert req.headers["x-km-api-version"] == "v-0.2.0" and req.headers["x-km-client-type"] == "PC_WEB"
        if "/check-out/initialize/" in p:
            b = json.loads(req.content)
            s = dec(b["sensitiveData"])
            check_sig(s, b["signature"])
            assert s["merchantId"] == "683002007104225" and s["orderId"] == "INV300" and len(s["datetime"]) == 14
            reply = json.dumps({"paymentReferenceId": "REF1", "challenge": "CH1", "acceptDateTime": s["datetime"]})
            enc = base64.b64encode(merchant_pub.encrypt(reply.encode(), padding.PKCS1v15())).decode()
            sig = base64.b64encode(nagad.sign(reply.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
            return httpx.Response(200, json={"sensitiveData": enc, "signature": sig})
        if "/check-out/complete/REF1" in p:
            b = json.loads(req.content)
            s = dec(b["sensitiveData"])
            check_sig(s, b["signature"])
            assert s == {"merchantId": "683002007104225", "orderId": "INV300", "amount": "99.00",
                         "currencyCode": "050", "challenge": "CH1"}
            return httpx.Response(200, json={"status": "Success", "callBackUrl": "https://nagad.test/pay"})
        if p.endswith("/verify/payment/REF1"):
            return httpx.Response(200, json={"status": "Success", "amount": "99.00", "orderId": "INV300",
                                             "issuerPaymentRefNo": "NG777"})
        return httpx.Response(404)

    pay = make_pay(["nagad"], {"NAGAD_MERCHANT_ID": "683002007104225", "NAGAD_PRIVATE_KEY_PATH": str(tmp_path / "m.pem"),
                               "NAGAD_PUBLIC_KEY_PATH": str(tmp_path / "n.pub")}, handler)
    pay.create_order("INV300", "99", "nagad")
    assert pay.start_payment("INV300") == "https://nagad.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV300")).success)
    o = pay.handle_callback(gw, oid, tok, "success", {"payment_ref_id": "REF1", "status": "Success",
                                                       "status_code": "00_0000_000"})
    assert o.status == Status.PAID and o.provider_txn_id == "NG777"
```

### `tests/test_paystation.py`

```python
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
```

### `tests/test_portpos.py`

```python
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
```

### `tests/test_shurjopay.py`

```python
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
```

### `tests/test_sslcommerz.py`

```python
import hashlib

import httpx
import pytest

from bdpay import BdPayError, Status
from bdpay.drivers.sslcommerz import check_verify_sign
from conftest import body, callback_parts, make_pay

ENV = {"SSLCOMMERZ_STORE_ID": "testbox", "SSLCOMMERZ_STORE_PASSWORD": "qwerty"}


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def signed(params, passwd="qwerty"):
    keys = list(params)
    d = dict(params)
    d["store_passwd"] = md5(passwd)
    s = "&".join(f"{k}={d[k]}" for k in sorted(d))
    return {**params, "verify_key": ",".join(keys), "verify_sign": md5(s)}


class FakeSsl:
    def __init__(self, amount="1500.00", status="VALID"):
        self.amount, self.status = amount, status

    def __call__(self, req):
        p = req.url.path
        if p.endswith("/gwprocess/v4/api.php"):
            b = body(req)
            assert b["total_amount"] == "1500.00" and b["store_id"] == "testbox" and b["ipn_url"].endswith("/ipn/sslcommerz")
            return httpx.Response(200, json={"status": "SUCCESS", "GatewayPageURL": "https://ssl.test/gw", "sessionkey": "S1"})
        if p.endswith("validationserverAPI.php"):
            assert req.url.params["val_id"] == "VAL1"
            return httpx.Response(200, json={"status": self.status, "tran_id": "INV200", "amount": self.amount,
                                             "currency_type": "BDT", "currency_amount": self.amount,
                                             "bank_tran_id": "BT1", "val_id": "VAL1"})
        if p.endswith("merchantTransIDvalidationAPI.php"):
            if "refund_amount" in req.url.params:
                return httpx.Response(200, json={"APIConnect": "DONE", "status": "success", "refund_ref_id": "RR1"})
            return httpx.Response(200, json={"APIConnect": "DONE", "no_of_trans_found": 1, "element": [
                {"status": "FAILED", "tran_id": "INV200", "amount": "1500.00", "currency_type": "BDT", "currency_amount": "1500.00"}]})
        return httpx.Response(404)


def setup(fake):
    pay = make_pay(["sslcommerz"], ENV, fake)
    pay.create_order("INV200", "1500", "sslcommerz")
    assert pay.start_payment("INV200") == "https://ssl.test/gw"
    return pay, callback_parts(pay.callback_urls(pay.store.get("INV200")).success)


def test_verify_sign_helper():
    p = signed({"tran_id": "A", "val_id": "B", "amount": "10.00"})
    assert check_verify_sign(p, "qwerty")
    assert not check_verify_sign({**p, "amount": "99.00"}, "qwerty")


def test_success_is_validated_server_side():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    o = pay.handle_callback(gw, oid, tok, "success", signed({"tran_id": "INV200", "val_id": "VAL1", "status": "VALID"}))
    assert o.status == Status.PAID and o.provider_txn_id == "BT1"


def test_tampered_post_rejected():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    bad = signed({"tran_id": "INV200", "val_id": "VAL1", "amount": "1500.00"})
    bad["amount"] = "1.00"
    with pytest.raises(BdPayError) as e:
        pay.handle_callback(gw, oid, tok, "success", bad)
    assert e.value.code == "SIGNATURE_INVALID"


def test_validation_amount_mismatch():
    pay, (gw, oid, tok, _) = setup(FakeSsl(amount="15.00"))
    o = pay.handle_callback(gw, oid, tok, "success", {"tran_id": "INV200", "val_id": "VAL1"})
    assert o.status == Status.NEEDS_REVIEW


def test_ipn_then_duplicate_validated():
    fake = FakeSsl()
    pay, _ = setup(fake)
    assert pay.handle_ipn("sslcommerz", {"tran_id": "INV200", "val_id": "VAL1"}).status == Status.PAID
    fake.status = "VALIDATED"
    assert pay.handle_ipn("sslcommerz", {"tran_id": "INV200", "val_id": "VAL1"}).status == Status.PAID


def test_fail_without_val_id_queries():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    assert pay.handle_callback(gw, oid, tok, "fail", {"tran_id": "INV200"}).status == Status.FAILED


def test_refund():
    pay, (gw, oid, tok, _) = setup(FakeSsl())
    pay.handle_callback(gw, oid, tok, "success", {"tran_id": "INV200", "val_id": "VAL1"})
    assert pay.refund("INV200", "500").ok
```

### `tests/test_uddoktapay.py`

```python
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
```

### `tests/test_upay.py`

```python
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
```

### `tests/test_web.py`

```python
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
```

---

End of spec. Generated with the bdpay configurator; tests: 35 passed.
