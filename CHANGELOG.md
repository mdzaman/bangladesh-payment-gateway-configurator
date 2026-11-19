# Changelog

## Unreleased

* Two-factor sign-in for the dashboard: time-based codes from an authenticator app (RFC
  6238) and ten single-use recovery codes. For an account that has it on, a correct
  password alone no longer creates a session. Workspace owners can require it of
  anyone who changes things in live mode. The set-up QR code is drawn in the browser.
* New command `bdpay-cloud disable-two-factor --email <address>` for someone who has
  lost both their phone and their recovery codes.
* Upgrading: run `bdpay-cloud migrate` (revision `0002`; it only adds nullable columns
  and two tables). `rotate-keys` re-encrypts the new two-factor secrets as well.
* Fixed: signing out left the dashboard on screen, showing "Sign in to continue" errors,
  until the page was reloaded.
* Fixed: on a phone, the list of signed-in devices on the account page was wider than
  its card.

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
