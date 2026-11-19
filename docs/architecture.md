# Architecture

## The shape of it

```
                    ┌──────────────────────────────────────────────┐
 customer browser ─▶│ reverse proxy (Caddy, an ingress, an ALB)     │
 merchant server  ─▶│   TLS, request size cap                       │
 payment provider ─▶└───────────────┬──────────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │ API  (bdpay-cloud serve)   stateless, N copies│
                    │  /v1, /bdpay   merchant API (API key)         │
                    │  /api          dashboard API (session + CSRF) │
                    │  /p            provider callbacks and IPNs    │
                    │  /checkout, /pay, /simulator   hosted pages   │
                    │  /             the dashboard (static files)   │
                    └───────┬───────────────────────┬──────────────┘
                            ▼                       ▼
                    ┌───────────────┐       ┌───────────────┐
                    │ PostgreSQL    │       │ Redis         │  optional: shared
                    │ all state     │       │ rate limits   │  rate-limit counters
                    └───────▲───────┘       └───────────────┘
                            │
                    ┌───────┴──────────────────────────────────────┐
                    │ worker  (bdpay-cloud worker)       N copies   │
                    │  webhook delivery · reconciliation · email    │──▶ providers,
                    │  housekeeping                                  │    merchant webhooks
                    └──────────────────────────────────────────────┘
```

One container image runs as the API, the worker, or the migration step. PostgreSQL holds
every piece of state, so API and worker processes can be added, removed or restarted at
any time.

## Two layers

**The engine (`core/bdpay`).** The bdpay reference implementation: ten gateway drivers,
the order state machine, signed callbacks, the HTTP policy. It is single-tenant and reads
its settings from environment variables. It is kept byte-for-byte as specified, and its
35 tests run in CI.

**The service (`cloud/bdpay_cloud`).** Everything a shared deployment needs, built around
the engine without editing it:

| The engine expects | The service supplies |
|---|---|
| `BDPAY_*` environment variables | each workspace's stored gateway settings, secrets decrypted in memory for one request |
| a SQLite `Store` | `PgStore`: the same methods on PostgreSQL, scoped to one workspace environment |
| a `TokenCache` on that store | `PgTokenCache`: encrypted, single-flight across replicas |
| one `BDPAY_SECRET` | a callback-signing secret per workspace environment |
| the `/bdpay` URL prefix | `/p/<environment key>` |

`TenantPay` is a subclass of the engine's `BdPay` object. `create_order`,
`handle_callback`, `handle_ipn`, `requery`, `reconcile` and the rule `_apply` (amount,
currency and order number must match) are inherited as they are. Three methods are
tightened: `start_payment` opens at most one provider session per order and returns the
same URL when asked again; `refund` is serialised per order and records an outcome it
could not confirm; `driver` explains a missing gateway in dashboard terms.

`cloud/tests/test_core_conformance.py` loads the engine's own test files and runs all 35
against `TenantPay` on PostgreSQL. If the service ever stops behaving like the reference,
that file fails.

## Tenancy

A **workspace** is one business. It has two **environments**, `sandbox` and `live`, with
separate credentials, API keys, webhooks and orders, like the engine's `BDPAY_ENV`
switch. Every tenant-owned table carries `(workspace_id, mode)`, and every query goes
through a `Scope(workspace_id, mode)` that is resolved before a handler runs:

* dashboard requests: session cookie → user → membership of the workspace in the URL;
* merchant API requests: API key → the one environment it belongs to;
* provider callbacks: the environment key in the URL, then the HMAC signature made with
  that environment's secret.

Nothing is looked up by a bare id. `cloud/tests/test_isolation.py` checks this object by
object.

## Where consistency comes from

* **State changes** take a row lock (`SELECT ... FOR UPDATE`) and only move forward, so a
  browser return and an IPN arriving on two replicas at once produce one change.
* **Webhook events** are inserted in the same transaction as the change they report (a
  transactional outbox), together with one delivery row per subscribed endpoint.
* **Starting a payment** is claimed with a conditional `UPDATE`; a claim abandoned by a
  crash frees itself after two minutes.
* **Background work** is claimed with `FOR UPDATE SKIP LOCKED` and a lease, so any number
  of workers can run and a worker that dies mid-task loses nothing.
* **Provider tokens and refunds** use transaction-scoped advisory locks, which also work
  through PgBouncer.
* **Migrations** run in one transaction under an advisory lock, so starting several
  replicas of a new version at once is safe.

Provider HTTP calls always happen between database transactions, never inside one (the
two advisory-lock cases above hold a connection, not row locks).

## Request concurrency

The gateway drivers are synchronous, so handlers are too and run in a thread pool
(`BDPAY_CLOUD_THREADPOOL_SIZE`, default 64). A request waiting on a provider occupies a
thread, not the process. Scale by adding API replicas; size
`replicas × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` under your PostgreSQL connection limit, or
put PgBouncer in front and set `BDPAY_CLOUD_DB_PGBOUNCER=true`.

## Data model

| Table | Holds |
|---|---|
| `users`, `sessions`, `email_tokens` | accounts, dashboard logins, single-use email links |
| `login_challenges`, `recovery_codes` | two-factor sign-ins waiting for their code, single-use recovery codes |
| `workspaces`, `memberships`, `invitations` | tenants and their teams |
| `workspace_envs` | the two environments: URL key, encrypted callback secret |
| `gateway_configs` | per-environment gateway settings; secrets encrypted, hints only in clear |
| `api_keys` | SHA-256 of each key, scopes, last use |
| `orders`, `order_events` | the engine's orders and their append-only audit trail |
| `checkout_sessions`, `payment_links` | hosted checkout |
| `events`, `webhook_endpoints`, `webhook_deliveries` | the outbox and its deliveries |
| `provider_tokens` | cached provider access tokens, encrypted |
| `idempotency_keys`, `audit_log`, `outbound_emails`, `worker_heartbeats`, `simulator_payments` | operations |

Money is integer poisha everywhere. The schema lives in `cloud/bdpay_cloud/models.py`;
`cloud/tests/test_migrations.py` fails if models and migrations drift apart.
