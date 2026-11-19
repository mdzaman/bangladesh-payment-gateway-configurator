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
| Two-factor secrets of dashboard users | AES-256-GCM ciphertext |
| API keys | SHA-256 only (the key has about 238 bits of entropy) |
| Session cookies, sign-in challenges, email-link tokens, invitations | SHA-256 only |
| Passwords, two-factor recovery codes | Argon2id |

* Keys come from `BDPAY_CLOUD_MASTER_KEYS`, a ring `id:key,id:key`. The first encrypts;
  all decrypt. Rotate with `bdpay-cloud gen-key` and `bdpay-cloud rotate-keys`
  ([operations.md](operations.md)).
* Each ciphertext is bound to its row through the AEAD associated data
  (`gateway:<workspace>:<mode>:<gateway>`, `totp:<user>` and so on). A ciphertext copied
  into another tenant's row, or another person's, fails to decrypt.
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
  change or reset ends other sessions, and so does switching two-factor on or off.
* Writes need the per-session CSRF token in `X-CSRF-Token`; in production the `Origin`
  header must also match. No CORS headers are sent, so other sites cannot call the API.
* Login: rate limited per address and per account, account lock after eight failures,
  identical answer for "no such account" and "wrong password", with equalised timing.
  A second step for accounts that switched it on: see Two-factor sign-in below.
* The failures that lock an account are wrong passwords and wrong two-factor codes,
  wherever they are typed: at sign-in, or on a page that asks a signed-in person for
  them again (changing the password, changing two-factor). They are counted in the
  database, in one statement, so guesses sent in parallel are each counted. A locked
  account is refused on those pages as well as at sign-in.
* Roles: owner, admin, developer, analyst. Changing things in live mode needs admin;
  live mode itself can only be activated by an owner with a confirmed email.

## Two-factor sign-in

Each person can add a second step to their own sign-in: a six-digit code from an
authenticator app (TOTP, RFC 6238, with the settings every app implements: HMAC-SHA1,
30-second steps). It is switched on under **Your account**.

* **Two steps, one session.** For such an account a correct password creates no session
  and sets no cookie. It is answered with a challenge: random, stored only as its
  SHA-256, good for five minutes, for one use, and for nothing except finishing that
  sign-in. The session is created when the challenge comes back with a valid code. A
  challenge is tied to the password it was issued for, so changing or resetting the
  password ends every sign-in that was half-way.
* **A code works once.** The newest time-step a code was accepted for is stored per
  account, and nothing at or before it is accepted again: a code that someone watched
  being typed is already spent. One step either side of the server's clock is allowed,
  for a phone that is a little out. Everything that checks a code for an account first
  locks that account's row, so two requests cannot both spend the same code.
* **Wrong codes are failed sign-ins.** They feed the same counter as wrong passwords:
  eight of either lock the account for 15 minutes, and a correct password does not
  clear the count while the second step is still owed. The second step also draws on
  the first step's per-account rate limit, and has a per-address limit of its own.
  That leaves someone who already has the password at most 32 guesses an hour at three
  in a million each: about a 1 in 430 chance per day, with the account locked
  throughout. It slows a stolen password down a great deal; it is not a reason to
  keep one.
* **Recovery codes.** Ten are made when two-factor is switched on, and shown once. Each
  signs in once, in place of a code from the app. They are short enough to type (ten
  characters, 50 bits), so they are stored the way passwords are: Argon2id only. Using
  one is written to the audit log.
* **Changing it takes both factors.** Starting a set-up asks for the password again.
  Switching two-factor off, or replacing the recovery codes, asks for the password and
  a current code (or a recovery code). A session left open on a shared computer is
  therefore not enough to add, remove or weaken it. Switching it on or off signs out
  every other device and is written to the audit log.
* **An unfinished set-up expires** after 15 minutes. Confirming one takes a code and no
  password, so it must not stay open. Housekeeping deletes the unused secret a day later.
* **A password reset does not get around it.** The emailed link sets a password and
  signs nobody in; the next sign-in still asks for the code. With two-factor on, a
  reset also leaves the failed-attempt count and any lock as they are: holding the
  mailbox must not buy more guesses.
* **The secret** is encrypted at rest like gateway credentials, bound to the account,
  re-encrypted by `rotate-keys`, and shown only during set-up. The QR code is drawn in
  the browser by code in this repository: the secret is never sent to a QR service.
* **A lost phone.** A recovery code signs in; then switch two-factor off and set it up
  again. With the recovery codes gone too, only the operator can help:
  `bdpay-cloud disable-two-factor --email <address>` ([operations.md](operations.md)).
  That ends the person's sessions and is written to the audit log.

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

* **Two-factor sign-in is each person's own choice.** No setting makes it compulsory
  for a workspace or for a whole deployment, so an account whose owner has not switched
  it on is protected by Argon2id, rate limits and lock-out only. Everything the
  dashboard does goes through `/api/`, which no customer and no provider ever calls, so
  a deployment for a single company can also put `/api/` behind its identity-aware
  proxy or office network without affecting payments.
* **Passkeys and security keys** (WebAuthn), codes by SMS or email, and "trust this
  device" are not implemented. The second factor is an authenticator app.
* **Database row-level security** is not used (see Tenant isolation).
* **Single sign-on** (SAML, OIDC) and **subscription billing** are not part of the service.

## What the operator must do

* Serve it only over HTTPS, behind a reverse proxy that you trust to set the client address.
* Keep the master keys in a secret manager and back them up separately from the database.
* Switch two-factor sign-in on for every platform admin account, and decide how someone who
  has lost both phone and recovery codes proves who they are before you run `disable-two-factor`.
* Keep `/metrics` off the public internet (the bundled proxy configurations block it) or set a token.
* Give outbound traffic a fixed IPv4 if merchants use Nagad or Upay, and publish it in `BDPAY_CLOUD_EGRESS_IPS`.
* Patch the base image and dependencies; `uv.lock` and `package-lock.json` pin exact versions.
* Decide who may sign up and go live (`ALLOW_SIGNUP`, `LIVE_MODE=approval`).
