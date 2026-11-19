**What this changes and why**

**How it was tested**

- [ ] `make test` passes (engine, service, dashboard)
- [ ] `make lint` passes
- [ ] No rule from parts 3 to 5 of the build spec is weakened (amount truth, re-verification, signed callbacks, forward-only states)
- [ ] No secret, key or customer data in the diff, in fixtures or in logs
- [ ] A schema change comes with a migration, and it is safe to run while the previous version is still serving
