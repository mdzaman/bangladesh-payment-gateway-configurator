---
name: New gateway
about: Propose or contribute a driver for another payment gateway
labels: gateway
---

**Gateway and public documentation link**

**Sandbox access** (public test credentials, self-registration, or onboarding only)

**Checklist** (from part 9 of the build spec)

- [ ] Driver subclass with `id`, `label`, `stability`, `capabilities`, `required_env`
- [ ] Base URLs per environment and a `<ID>_BASE_URL` override
- [ ] `verify_return` rejects ids that do not belong to the order, then calls the provider
- [ ] `query` maps provider statuses and returns amount in poisha, currency, order reference, transaction id
- [ ] Tokens through `self.tokens.get_or_fetch`
- [ ] A test file with a fake provider covering success and mismatch
- [ ] Catalog entry in `cloud/bdpay_cloud/catalog/gateways.json`
