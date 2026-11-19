# Changelog

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
