# Security model

What the service protects, how, and what is left to whoever runs it. To report a
vulnerability, see [SECURITY.md](../SECURITY.md).

## What is at stake

* **Gateway credentials** of every tenant. With them someone could start payments,
  read payment status and issue refunds on that merchant's account.
* **The integrity of "paid"**. A merchant ships goods when an order says paid.
* **Tenant separation**. One business must never see or change another's data.
* **Customer data**: names, phone numbers, emails that merchants send with orders.

Out of scope by design: card numbers and wallet PINs. The service never sees them;
every payment is completed on the provider's own page.

## Integrity of "paid"

Inherited from the reference engine and unchanged:

* The amount is the one stored when the order was created. A provider answer with a
  different amount, currency or order reference moves the order to `needs_review`.
* A browser return or an IPN is never believed. Each triggers a server-to-server check
  with the provider, using the merchant's credentials.
* Callback URLs carry `{exp}.{hmac}`: HMAC-SHA256 over `gateway|order_id|exp`, compared
  in constant time, valid 24 hours. The secret is per workspace environment, so a link
  from one tenant, or from sandbox, does nothing anywhere else.
* Orders only move forward, under a row lock.

Added by the service:

* One provider session per order. A second concurrent "start" either gets the same URL
  or is told to retry; it cannot create a second payment that would later be refused as
  "not this order's".
* Refunds are serialised per order, and a refund whose outcome is unknown is never
  re-sent by the same idempotency key.

## Tenant isolation

Every tenant table carries `(workspace_id, mode)` and every query is built from a
`Scope` that is resolved from the caller's credentials before any handler runs. Lookups
by id always include the scope, and a miss answers `404`, the same as "does not exist".
`cloud/tests/test_isolation.py` exercises each object type from a second tenant.

Sandbox and live are separate scopes of the same workspace: separate credentials, keys,
webhooks, orders and callback secrets.

Database row-level security is not used. If you want it as a second line of defence,
the `(workspace_id, mode)` columns on every tenant table are what a policy would key on.

## Secrets at rest

| Secret | Stored as |
|---|---|
| Gateway credentials, Nagad keys | AES-256-GCM ciphertext |
| Callback-signing secrets, webhook signing secrets | AES-256-GCM ciphertext |
| Cached provider access tokens | AES-256-GCM ciphertext |
| Two-factor (authenticator app) secrets | AES-256-GCM ciphertext |
| API keys | SHA-256 only (the key has about 238 bits of entropy) |
| Session cookies, sign-in challenges, email-link tokens, invitations | SHA-256 only |
| Two-factor recovery codes | SHA-256 only (each code has 80 bits of entropy) |
| Passwords | Argon2id |

* Keys come from `BDPAY_CLOUD_MASTER_KEYS`, a ring `id:key,id:key`. The first encrypts;
  all decrypt. Rotate with `bdpay-cloud gen-key` and `bdpay-cloud rotate-keys`
  ([operations.md](operations.md)).
* Each ciphertext is bound to its row through the AEAD associated data
  (`gateway:<workspace>:<mode>:<gateway>` and so on). A ciphertext copied into another
  tenant's row fails to decrypt.
* Secrets are write-only. No API returns them; the dashboard sees "set" and a short hint.
  API keys and webhook secrets are shown once, at creation.
* In production the service refuses to start without a key, and refuses the published
  development key.

The master keys are as sensitive as everything they protect. Keep them in a secret
manager, not in the image, the repository, or the database. A database dump without the
keys does not reveal gateway credentials; the keys plus a dump do.

## Server-side request forgery

Tenants supply URLs the service will call: webhook endpoints, and base-URL overrides for
gateways whose host comes from an onboarding pack (Upay, UddoktaPay, Nagad).

* When saved, a URL must be `http(s)` (https only in live mode), carry no credentials,
  and not resolve to a private, loopback, link-local or otherwise reserved address.
* When called, the connection goes through a guarded transport that resolves the host,
  refuses if *any* resolved address is not publicly routable (including IPv4-mapped,
  6to4 and NAT64 forms), and then connects to the address it just checked. Resolving and
  connecting in one step is what defeats DNS rebinding. Redirects are never followed.
* `BDPAY_CLOUD_BASE_URL_ALLOWLIST` restricts gateway base-URL overrides to known hosts.
  Set it on a shared deployment: a workspace that controls the "gateway" host also
  controls where its checkout page sends customers.

`BDPAY_CLOUD_ALLOW_PRIVATE_TARGETS=true` switches the guard off for local development
and is refused in production.

## Dashboard sessions

* Opaque random token in an `HttpOnly`, `SameSite=Lax` cookie; `Secure` and the
  `__Host-` prefix when served over https. Only its SHA-256 is stored.
* 12 hours idle, 14 days maximum, revocable ("sign out everywhere else"). A password
  change or reset ends other sessions.
* Writes need the per-session CSRF token in `X-CSRF-Token`; in production the `Origin`
  header must also match. No CORS headers are sent, so other sites cannot call the API.
* Login: rate limited per address and per account, account lock after eight failures,
  identical answer for "no such account" and "wrong password", with equalised timing.
* Roles: owner, admin, developer, analyst. Changing things in live mode needs admin;
  live mode itself can only be activated by an owner with a confirmed email.

## Two-factor sign-in

Each person can add a second factor to their account: a time-based code from an
authenticator app (TOTP, RFC 6238, with the parameters every app supports: SHA-1, six
digits, 30 seconds). Owners can require it of anyone who changes things in live mode
(Settings, "Sign-in security").

* **No session without the code.** With the factor on, a correct password is answered
  with a single-purpose challenge, valid for 10 minutes, and no cookie. A session is
  created only when a valid code comes back with that challenge. Only the SHA-256 of
  the challenge is stored.
* **Each code works once.** The last accepted time step is kept per account and nothing
  at or before it is accepted again. One step either side of the server's clock is
  accepted, so a phone up to 30 seconds out still works. Attempts are serialised with a
  row lock: several requests carrying the same code get one session between them.
* **Wrong codes count as failed sign-ins.** They share the counter and the lock-out
  with wrong passwords (eight failures, then 15 minutes), and a correct password does
  not reset it. The code step is also rate limited per challenge and per address. That
  leaves someone who already has the password about 770 guesses a day at 3 in a
  million each, roughly a 1 in 400 chance per day, with the account locked throughout.
* **Recovery codes.** Ten are issued when the factor is switched on. Each works once.
  They are shown once and stored as SHA-256 bound to the account. Using one is written
  to the audit log and the account holder is emailed.
* **Changing it takes both factors.** Starting set-up needs the password. Switching the
  factor off, or issuing new recovery codes, needs the password and a current code (or
  a recovery code), so a hijacked session cannot remove it. These attempts are rate
  limited per account. Switching it on or off signs out every other device and emails
  the account holder.
* **A password reset does not get past it.** The emailed link sets a password and
  creates no session; the next sign-in still asks for the code. For an account with the
  factor on, a reset also leaves the failed-attempt counter alone, so controlling the
  mailbox does not buy more guesses.
* **The secret** is encrypted at rest like gateway credentials, bound to the account
  (`totp:<user>`), and shown only during set-up. The QR code is drawn in the browser:
  the secret is never sent to a QR service or any other third party.
* **Locked out** (phone and recovery codes both gone): the operator runs
  `bdpay-cloud disable-two-factor --email <address>` after checking who is asking. That
  is written to the audit log and the account holder is emailed.

## Abuse limits

Per API key, per client address for sign-up and login, per hosted page, and per order
for provider-facing endpoints: an IPN or a replayed callback makes the service call the
provider with the merchant's credentials, so each order accepts only a bounded number.
Limits are shared across replicas through Redis when configured, per process otherwise.
Request bodies are capped at 256 KB, with or without a `Content-Length`.

## Pages and headers

Every response carries `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, a Content-Security-Policy, and HSTS over https. Hosted
checkout pages contain no scripts and no inline styles (`default-src 'none'`). The
result page of an order is reachable only through a signed path, so order status cannot
be read by guessing order ids.

## Logs and audit

Logs and the order audit trail pass through the reference redaction: values of keys
that look like secrets, and Bangladeshi mobile numbers, are masked. The access log
records route templates, never raw paths, because callback paths contain a signature.
The workspace audit log records who changed what, with field names but never values.

## Not included

Know these before you run it for other businesses:

* **Two-factor sign-in is each person's choice**, or each workspace's for changes in
  live mode. There is no setting that forces it on every account of a deployment.
* **Passkeys and security keys** (WebAuthn), codes by SMS or email, and "remember this
  device" are not implemented. The second factor is an authenticator app or a recovery
  code, and it is asked for at every sign-in.
* **Database row-level security** is not used (see Tenant isolation).
* **Single sign-on** (SAML, OIDC) and **subscription billing** are not part of the service.

## What the operator must do

* Serve it only over HTTPS, behind a reverse proxy that you trust to set the client address.
* Keep the master keys in a secret manager and back them up separately from the database.
* Keep `/metrics` off the public internet (the bundled proxy configurations block it) or set a token.
* Give outbound traffic a fixed IPv4 if merchants use Nagad or Upay, and publish it in `BDPAY_CLOUD_EGRESS_IPS`.
* Patch the base image and dependencies; `uv.lock` and `package-lock.json` pin exact versions.
* Decide who may sign up and go live (`ALLOW_SIGNUP`, `LIVE_MODE=approval`).
* Have every platform admin turn on two-factor sign-in: an admin can suspend any
  workspace and approve live mode, and configuration cannot force it for them.
* Optional, for a deployment used by one company: put `/api/` behind your
  identity-aware proxy or office network. Everything the dashboard does goes through
  `/api/`, which no customer and no provider ever calls, so payments are not affected.
