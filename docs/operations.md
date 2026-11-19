# Operations

## Is it healthy?

```bash
bdpay-cloud doctor
```

Checks configuration, the key ring, the database and its schema version, Redis, whether
a worker has reported recently, and prints Dhaka time (Nagad and Upay timestamps use it).

| Endpoint | Use |
|---|---|
| `GET /healthz` | Liveness: the process answers. |
| `GET /readyz` | Readiness: database reachable and migrated. `503` otherwise. |
| `GET /metrics` | Prometheus. Keep it private or set `BDPAY_CLOUD_METRICS_TOKEN`. |

Metrics worth alerting on:

| Metric | Why |
|---|---|
| `bdpay_worker_backlog{queue="webhooks"\|"reconcile"\|"email"}` | Growing: workers are down or too few. |
| `bdpay_http_requests_total{status=~"5.."}` | The service itself is failing. |
| `bdpay_provider_requests_total{provider,status}` | A provider is erroring or slow (`bdpay_provider_request_seconds`). |
| `bdpay_reconcile_checks_total{result="error"}` | Reconciliation cannot reach a provider. |
| `bdpay_rate_limited_total{bucket}` | Abuse, or a limit that is too tight. |

Logs are JSON, one object per line, with a `request_id` that is also returned to callers
in `X-Request-Id` and in error bodies. Secrets and phone numbers are masked before
anything is written.

## The worker must run

`bdpay-cloud worker` delivers webhooks, re-checks pending payments, sends email and
cleans up. Without it, payments still complete when the customer returns, but lost
redirects are never recovered and no webhook leaves. The dashboard warns when no worker
has reported for three minutes.

## Day-to-day

**An order is in `needs_review`.** The provider confirmed a payment whose amount,
currency or order number did not match the order. The service will never mark it paid by
itself. Compare the order's audit trail with the provider's panel, then fulfil or refund
by hand. For the first week of a new gateway, watch for these.

**A refund answered `TIMEOUT`.** The outcome is unknown. The order's audit trail has
`refund_unconfirmed`. Look in the provider's panel before refunding again.

**A gateway shows "Credentials rejected".** The provider refused the stored credentials
on the last real call (the message is next to it). The merchant re-enters them under
Gateways and presses Test connection.

**A webhook endpoint keeps failing.** Deliveries retry for about two days. After the
merchant fixes their server, "Send again" on a delivery re-queues it.

**A customer paid but the order is still pending.** It is re-checked automatically (2
minutes after starting, then at growing intervals, for 24 hours). "Ask the provider
again" on the order does it at once.

**Someone cannot get past the two-factor step.** A phone whose clock is wrong makes
every code wrong: have them set the time to automatic. A lost phone: one of their
recovery codes signs in, and then they switch two-factor off and set it up again under
Your account. With the recovery codes gone as well, nothing they can do themselves will
get them in, on purpose: a password reset does not bypass the second factor. Make sure
who is asking, then:

```bash
bdpay-cloud disable-two-factor --email person@example.com
```

Their sessions end, their password is unchanged, and the audit log records it as
`user.two_factor_reset`.

**"Too many failed attempts".** Eight wrong passwords or codes lock an account for 15
minutes. It unlocks by itself. A password reset clears the lock only for an account
without two-factor.

## Rotating the master key

```bash
bdpay-cloud gen-key                       # prints  k20270101:<base64>
# 1. Put the new entry FIRST in BDPAY_CLOUD_MASTER_KEYS, keep the old one after it.
# 2. Roll out that setting to every API and worker process.
bdpay-cloud rotate-keys                   # re-encrypts everything with the new key
# 3. When it reports success, remove the old entry and roll out again.
```

`rotate-keys` re-encrypts gateway credentials, callback and webhook secrets, and the
two-factor secrets of dashboard users. It reports any value that no key in the ring can
read and leaves it untouched, so one damaged row cannot block a rotation. Do not remove
old keys while it reports unreadable values.

If the keys are lost, stored gateway credentials and webhook secrets cannot be
recovered: every workspace has to enter its credentials again. Two-factor secrets are
lost with them: codes from the app can no longer be checked, a recovery code still
signs in, and `disable-two-factor` clears the way for anyone who has none left.

## Suspending a workspace

A platform admin can suspend at `/admin`. New payments are refused in both modes;
callbacks and IPNs for payments already at a provider are still processed, because a
customer who has paid must be recorded as paid.

## Command reference

```
bdpay-cloud serve [--host --port --workers --forwarded-allow-ips --migrate]
bdpay-cloud worker [--once]
bdpay-cloud migrate [--check]
bdpay-cloud doctor
bdpay-cloud gen-key [--id]
bdpay-cloud rotate-keys
bdpay-cloud create-user --email --name [--workspace] [--platform-admin] [--password-stdin]
bdpay-cloud disable-two-factor --email
bdpay-cloud seed-demo --workspace <slug>       development only
bdpay-cloud make-migration -m <message>       development only
bdpay-cloud openapi
```
