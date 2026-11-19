# bdpay-service

The bdpay reference engine: one Python integration for Bangladeshi payment gateways. Drivers for: bKash, Nagad, SSLCOMMERZ, aamarPay, shurjoPay, EPS, PortPos, PayStation, Upay, UddoktaPay.
Not affiliated with any payment provider. Merchant-side integration software only: money always moves through the licensed provider.

This folder is kept byte-for-byte as the bdpay build spec gives it. The hosted, multi-tenant service in `../cloud` is built around it, and the self-host generator in `../web` ships these files to people who want to run it themselves.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # offline tests against mock gateways
python -m bdpay doctor       # settings, keys, reachability, Dhaka time
uvicorn app:app --reload     # http://localhost:8000 test console
```

Docker: `docker compose up -d --build` (service + reconcile worker).

## Gateways

| Gateway | Status | Refund by API | Verification | Sandbox test data |
|---|---|---|---|---|
| bKash | Stable | Partial | Execute, then query on timeout | Wallet 01770618575, OTP 123456, PIN 12121 (bKash demo page) |
| Nagad | Stable | Panel | RSA signature, then verify API | Sandbox wallet numbers come with the pack |
| SSLCOMMERZ | Stable | Partial | verify_sign, Validation API, amount match | Use the test cards and mobile banking options shown on the sandbox payment page |
| aamarPay | Stable | Panel | Search Transaction API | Store aamarpaytest, key dbb74894e82415a2f7ff0ec3a97e4183 (aamarPay docs) |
| shurjoPay | Stable | Panel | Verification API (sp_code 1000) | User sp_sandbox, password pyyk97hu&6u6, prefix NOK (shurjoPay developer page) |
| EPS | Beta: check pack | Panel | HMAC-SHA512 + status API | From the portal |
| PortPos | Beta: check pack | Partial | Invoice retrieve + IPN | From the panel |
| PayStation | Beta: check pack | Panel | Transaction status API | From PayStation |
| Upay | Beta: check pack | Full only | Single payment status API | From the onboarding pack |
| UddoktaPay | Experimental | None | verify-payment API | API key 982d381360a69d419689740d9f2e26ce36fb7a50 at sandbox.uddoktapay.com |

Beta drivers follow public and community references. Check every field against the provider's onboarding pack before going live; base URLs can be overridden with `<GATEWAY>_BASE_URL`.

## URLs to register with providers

- **bKash**: callbacks `https://your-domain.example/bdpay/callback/bkash/...` (signed per order, automatic)
- **Nagad**: callbacks `https://your-domain.example/bdpay/callback/nagad/...` (signed per order, automatic)
- **SSLCOMMERZ**: callbacks `https://your-domain.example/bdpay/callback/sslcommerz/...` (signed per order, automatic); IPN `https://your-domain.example/bdpay/ipn/sslcommerz`
- **aamarPay**: callbacks `https://your-domain.example/bdpay/callback/aamarpay/...` (signed per order, automatic)
- **shurjoPay**: callbacks `https://your-domain.example/bdpay/callback/shurjopay/...` (signed per order, automatic)
- **EPS**: callbacks `https://your-domain.example/bdpay/callback/eps/...` (signed per order, automatic)
- **PortPos**: callbacks `https://your-domain.example/bdpay/callback/portpos/...` (signed per order, automatic); IPN `https://your-domain.example/bdpay/ipn/portpos`
- **PayStation**: callbacks `https://your-domain.example/bdpay/callback/paystation/...` (signed per order, automatic); IPN `https://your-domain.example/bdpay/ipn/paystation`
- **Upay**: callbacks `https://your-domain.example/bdpay/callback/upay/...` (signed per order, automatic)
- **UddoktaPay**: callbacks `https://your-domain.example/bdpay/callback/uddoktapay/...` (signed per order, automatic); IPN `https://your-domain.example/bdpay/ipn/uddoktapay`

## HTTP API (send header `X-BDPAY-ADMIN: <BDPAY_ADMIN_TOKEN>`)

| Method | Path | Purpose |
|---|---|---|
| POST | /bdpay/orders | Create order `{order_id, amount, gateway, customer}` (amount is saved server-side) |
| POST | /bdpay/orders/{id}/pay | Get `redirect_url` to send the customer to |
| GET | /bdpay/orders/{id} | Status and audit trail |
| POST | /bdpay/orders/{id}/requery | Ask the provider again |
| POST | /bdpay/orders/{id}/refund | `{amount?, reason}` |
| GET | /bdpay/gateways | Enabled gateways |
| GET/POST | /bdpay/callback/... and /bdpay/ipn/{gateway} | Used by providers |

Order IDs: letters, digits, `-` or `_`, up to 40 characters (Nagad: letters and digits only, up to 20). Use a new order ID for each new payment attempt.

## Use inside a Python app

```python
from bdpay import BdPay, Status
pay = BdPay.from_env()
pay.create_order("INV1001", "1250.00", "bkash", {"name": "Rahim", "phone": "01700000000"})
url = pay.start_payment("INV1001")        # redirect the customer
# FastAPI: app.include_router(bdpay.web.build_router(pay, on_paid=your_fulfilment))
```

Fulfil orders in `on_paid` in `app.py`: it fires once, only after server-side verification.

## Statuses

created → pending → paid | failed | cancelled | expired | needs_review; paid → partially_refunded → refunded.
`needs_review` means the provider reported a payment whose amount, currency or order number did not match: check it by hand.

## Operations

- `python -m bdpay reconcile` every 10 minutes (the compose file does this).
- Logs mask secrets and phone numbers; every state change is in the `events` table.
- SQLite is the default store. For several replicas, implement the same `Store` and `TokenCache` methods on Postgres/Redis.

## Go-live

1. Live credentials stored only in `.env` or your secret manager; `BDPAY_ENV=live`; new `BDPAY_SECRET`.
2. `BDPAY_PUBLIC_URL` is your https domain and reachable from the internet.
3. bKash: live app key/secret and username/password from bKash; run one real payment and a refund of the minimum amount.
4. Nagad: live keys in `keys/`; server public IPv4 and callback domain whitelisted by Nagad.
5. SSLCOMMERZ: IPN URL set in My Store → IPN Settings; allow inbound traffic from SSLCOMMERZ on ports 80/443.
6. Upay: production IPv4 whitelisted; base URL from the live pack.
7. Beta drivers: compare every field with the provider pack and run the full sandbox test list.
8. `python -m bdpay doctor` shows all ✓.
9. Reconcile job running; watch for `needs_review` orders for the first week.
10. Test console off in live (default) and `BDPAY_ADMIN_TOKEN` set.

## Security

Never commit `.env` or `keys/` (already in .gitignore; CI runs gitleaks). Report vulnerabilities privately to the maintainer.

## Licence

MIT. See LICENSE.
