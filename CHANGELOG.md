# Changelog

## Unreleased

* **Two-factor sign-in** for the dashboard: a code from an authenticator app (TOTP,
  RFC 6238) as a second step after the password, with ten single-use recovery codes.
  Each person switches it on under Your account. [docs/security.md](docs/security.md)
  describes how it holds up. Upgrading: `bdpay-cloud migrate` applies revision `0002`
  (two new tables and four nullable columns on `users`; safe while the previous release
  is still serving).
* `bdpay-cloud disable-two-factor --email <address>` for someone who has lost both the
  authenticator and the recovery codes. `rotate-keys` re-encrypts two-factor secrets too.
* Sign-in is stricter in three places, with or without two-factor. Failed attempts are
  counted in the database in one statement, so guesses sent in parallel are each
  counted. A wrong current password on the change-password page counts towards the
  lock-out, and that page is rate limited. After the password check, sign-in looks at
  the account again under a row lock: an account locked in the meantime, or a password
  changed in the meantime, is refused.
* The merchant API under `/v1` is unchanged.

## 1.0.0

First release.

* Reference engine (`core/`): bKash, Nagad, SSLCOMMERZ, aamarPay, shurjoPay, EPS,
  PortPos, PayStation, Upay and UddoktaPay drivers, as specified in the bdpay build spec.
* Multi-tenant service (`cloud/`): workspaces with sandbox and live environments, the
  hosted configurator with encrypted write-only credentials, merchant API compatible
  with self-hosted bdpay, hosted checkout and payment links in English and Bangla,
  signed webhooks through a transactional outbox, reconciliation, a sandbox simulator,
  team roles and audit log, health checks and Prometheus metrics.
* Dashboard and self-host generator (`web/`).
* Container image, Docker Compose stack with Caddy, Kubernetes manifests.
