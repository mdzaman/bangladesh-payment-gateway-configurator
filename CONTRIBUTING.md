# Contributing

Thank you for helping. A few things make a contribution easy to accept.

## Before you start

* For anything larger than a fix, open an issue first so the approach can be agreed.
* Security problems are reported privately, as [SECURITY.md](SECURITY.md) describes, not in
  a public issue.

## Ground rules

1. **The payment rules are not negotiable.** Amount truth on the server, re-verification
   of every redirect and IPN, signed callbacks, forward-only states, tenant scoping on
   every query. A change that weakens one will not be merged, whatever it enables.
2. **`core/` is the reference engine.** It is kept byte-for-byte as its build spec gives
   it. Adapt around it in `cloud/`.
3. **Every behaviour change comes with a test** that fails without it.
4. **No secrets, credentials or real customer data** in code, fixtures, logs or screenshots.
5. **A schema change comes with a migration** that is safe while the previous release is
   still serving.

## Workflow

```bash
make setup
make test && make lint
```

Keep pull requests focused: one change, described in terms of what a user or operator
will notice. [docs/development.md](docs/development.md) explains the layout, the tests
and how to add a gateway.

## Licence

By contributing you agree that your contribution is licensed under the MIT licence of
this repository.
