# Merchant API

A running service serves the full reference at `/docs` (OpenAPI at `/openapi.json`).
This page is the guide.

## Basics

* **Base URL**: your deployment, for example `https://pay.example.com/v1`.
* **Authentication**: `Authorization: Bearer <API key>`. Create keys in the dashboard
  under Developers. A key belongs to one workspace environment: `bdp_test_...` keys see
  only sandbox data, `bdp_live_...` only live data. Keys can be full-access or limited to
  scopes (`orders:read`, `orders:write`, `refunds:write`, `checkout:write`, `events:read`).
  Call the API from your server only. Never ship a key in a browser or a mobile app.
* **Amounts**: Bangladeshi taka as a decimal string (`"1250.00"`). Responses also carry
  integer poisha (`amount_poisha`).
* **Order ids**: yours. Letters, digits, `-` or `_`, up to 40 characters (Nagad: letters
  and digits only, up to 20). One payment attempt per order id.
* **Errors**: `{"detail": {"code", "provider", "message", "request_id"}}`. `code` is
  stable: `VALIDATION` (400), `UNAUTHENTICATED` (401), `PROVIDER_DECLINED` (402),
  `FORBIDDEN`, `SIGNATURE_INVALID`, `LIVE_NOT_ACTIVE` (403), `NOT_FOUND` (404),
  `DUPLICATE` (409), `RATE_LIMITED` (429), `AUTH` (502, the provider rejected the stored
  credentials), `PROVIDER_UNAVAILABLE` (503), `TIMEOUT` (504).
* **Rate limit**: 600 requests a minute per key by default; `429` with `Retry-After`.

## Orders

```
POST /v1/orders                     create
POST /v1/orders/{order_id}/pay      start the payment, get redirect_url
GET  /v1/orders/{order_id}          status and audit trail
GET  /v1/orders                     list (status, gateway, q, created_from, created_to, limit, starting_after)
POST /v1/orders/{order_id}/requery  ask the provider again
POST /v1/orders/{order_id}/refund   {"amount"?: "200.00", "reason": "..."}   amount omitted = full refund
GET  /v1/gateways                   gateways enabled for this key's environment
```

Create:

```json
POST /v1/orders
{
  "order_id": "INV1001",
  "amount": "1250.00",
  "gateway": "bkash",
  "customer": {"name": "Rahim", "phone": "01700000000", "email": "rahim@example.com"},
  "metadata": {"cart": "42"},
  "return_url": "https://shop.example/thanks?order={order_id}&status={status}"
}
```

* The amount stored here is the only amount that will ever be accepted as paid.
* Creating the same `order_id` again with the same gateway and amount returns the
  existing order. Different details answer `409 DUPLICATE`.
* `customer` accepts `name`, `email`, `phone`, `address`, `city`, `postcode`, `product`,
  `category`, `ip`. `metadata` is yours: up to 20 string values, returned on every read
  and in webhooks.
* `return_url` is where the customer lands afterwards; `{order_id}` and `{status}` are
  filled in. Without it, the environment's default page (Settings) or a built-in result
  page is used. **Never treat arriving there as proof of payment.** Read the order or
  wait for the webhook.

`POST .../pay` returns `{"redirect_url": "..."}`. Calling it again returns the same URL;
it never opens a second payment for the same order. For a new attempt, create a new order.

### Statuses

```
created → pending → paid | failed | cancelled | expired | needs_review
failed | cancelled | expired → paid | needs_review     (a late verified payment rescues the order)
paid → partially_refunded → refunded
```

Orders only move forward. `needs_review` means the provider reported a payment whose
amount, currency or order number did not match the order: do not ship, check it in the
provider's panel.

Pending orders are re-checked with the provider automatically (after 2 minutes, then at
growing intervals) and expire after 24 hours.

## Idempotency

Send `Idempotency-Key: <unique string>` on any POST you might retry. The same key with
the same request replays the stored response (`Idempotent-Replayed: true`); with a
different request it answers `422`; while the first is still running, `409`.

This matters most for refunds. If a refund times out at the provider (`504`), nobody
knows whether money moved: the same key will keep answering `504` rather than send the
refund twice, and the order's audit trail records `refund_unconfirmed`. Check the
provider's panel before retrying with a new key.

## Hosted checkout

```
POST /v1/checkout/sessions                {"amount", "reference"?, "description"?, "customer"?, "metadata"?,
                                           "gateways"?, "success_url"?, "cancel_url"?, "locale"?, "expires_in_minutes"?}
GET  /v1/checkout/sessions/{id}           the session and its attempts
POST /v1/checkout/sessions/{id}/expire    close an open session
POST /v1/payment-links                    a reusable page that creates a session per visitor
GET  /v1/payment-links
```

Send the customer to the session's `url`. They choose a method; each try is its own
order (id `CO` + 16 characters) linked to the session, so a failed bKash attempt can be
followed by a card. `success_url` and `cancel_url` may contain `{session_id}`,
`{order_id}`, `{reference}` and `{status}`. Before a second attempt starts, earlier
pending attempts are re-checked with their provider so a customer is not asked to pay
twice.

## Webhooks

Add an endpoint in the dashboard (Developers → Webhooks). Each event is an HTTP POST:

```json
{
  "id": "evt_...", "object": "event", "type": "order.paid", "mode": "live",
  "created_at": "2026-11-18T10:21:00Z",
  "data": {"order": { ...the order, exactly as GET /v1/orders/{id} returns it... }, "previous_status": "pending"}
}
```

Types: `order.paid`, `order.failed`, `order.cancelled`, `order.expired`,
`order.needs_review`, `order.partially_refunded`, `order.refunded`,
`checkout.session.completed`, `checkout.session.extra_payment` (a second attempt of an
already-paid session was also paid: refund one), `ping`.

Headers: `BDPay-Signature: t=<unix seconds>,v1=<hex>`, `BDPay-Event-Id`,
`BDPay-Event-Type`, `BDPay-Delivery-Id`, `BDPay-Delivery-Attempt`.

Verify every delivery, against the **raw** body:

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    timestamp, given = int(parts["t"]), parts["v1"]
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)
```

* Answer `2xx` within 10 seconds. Anything else is retried after 10 s, 1 min, 5 min,
  30 min, 2 h, 6 h, 12 h and 24 h (nine attempts), then marked failed. You can resend
  from the dashboard.
* Delivery is at least once and not ordered: use the event `id` as an idempotency key,
  and trust the order's `status` in the payload over the order in which events arrive.
* Events exist whether or not an endpoint is configured: `GET /v1/events`.

## Coming from self-hosted bdpay

The order endpoints are also served at the reference paths, with the key in the
reference header:

```
POST /bdpay/orders   POST /bdpay/orders/{id}/pay   GET /bdpay/orders/{id}
POST /bdpay/orders/{id}/requery   POST /bdpay/orders/{id}/refund   GET /bdpay/gateways
X-BDPAY-ADMIN: <API key>
```

Responses start with the same eight fields as the reference and add more. Point your
existing integration at the new base URL and replace the `on_paid` hook with a webhook.
