# Development

## Set up

Python 3.11 or newer, Node 22.12 or newer, Docker, and [uv](https://docs.astral.sh/uv/).

```bash
make setup        # uv sync, npm ci
make dev          # PostgreSQL and Redis in Docker, migrations, dashboard build, API on :8000
make worker       # second terminal
make web-dev      # optional third terminal: dashboard with hot reload on :5173
```

Development mode needs no configuration: a published development key encrypts secrets
(it is refused in production) and email is printed to the log, which is where you find
confirmation and reset links. Sign up at <http://localhost:8000>, then:

```bash
.venv/bin/bdpay-cloud seed-demo --workspace <your-workspace-address>
```

fills the sandbox with simulator payments so the charts have something to show.

To receive webhooks on your own machine in development, set
`BDPAY_CLOUD_ALLOW_PRIVATE_TARGETS=true` (never on a shared deployment) or use a tunnel.

## Layout

```
core/bdpay/                the reference engine. Do not edit: see "The engine" below.
cloud/bdpay_cloud/
  settings.py              every setting, with production guards
  crypto.py                key ring and AEAD for stored secrets
  models.py, migrations/   schema
  engine/                  PgStore, PgTokenCache, driver factory, simulator, TenantPay
  services/                business logic (auth, gateways, checkout, webhooks, ...)
  api/                     HTTP: v1 (merchant), account + environment (dashboard), provider, hosted
  workers/                 reconcile, housekeeping, the worker loop
  catalog/gateways.json    gateway forms: the single source for service and dashboard
web/src/
  lib/generator.ts         the self-host generator
  pages/app/Gateways.tsx   the hosted configurator
```

## Tests

```bash
make test-core     # 35 reference tests, no services needed
make test-cloud    # needs PostgreSQL (make services)
make test-web      # generator and zip; builds real projects and runs their tests
make lint
```

The service tests use a real PostgreSQL because the code relies on row locks, advisory
locks, `SKIP LOCKED` and JSONB. They create a fresh workspace per test instead of
cleaning tables, so they do not interfere with each other. Set
`BDPAY_CLOUD_TEST_DATABASE_URL` to use another server; the database name must contain
`test` because its schema is dropped and rebuilt.

Provider calls are faked with `httpx.MockTransport` (`cloud/tests/helpers.py`), never
sent to a real gateway.

## The engine

`core/` is the bdpay reference implementation exactly as its build spec gives it
([docs/reference/BDPAY_BUILD_SPEC.md](reference/BDPAY_BUILD_SPEC.md)), and the self-host
generator ships those files to users. `cloud/tests/test_core_is_reference.py` compares
the two byte for byte, so change `core/` only together with the spec. The service adapts
around it instead: see `engine/runtime.py` for how.

## Changing the schema

1. Edit `models.py`.
2. `.venv/bin/bdpay-cloud make-migration -m "describe the change"` writes the next file
   in `cloud/bdpay_cloud/migrations/versions/` by comparing the models with your
   development database (run `make migrate` first so it is at the current head).
3. Read the generated file. Make it safe to run while the previous release is still
   serving (add columns as nullable or with a default; drop in a later release).
4. `make test-cloud`: `test_migrations.py` fails if models and migrations disagree.

## Adding a gateway

Follow part 9 of the build spec for the driver itself (`core/bdpay/drivers/<id>.py`,
registry entry, a test file with a fake provider). Then for the hosted service:

1. Add the gateway to `cloud/bdpay_cloud/catalog/gateways.json`: its fields (mark
   secrets as `secret`, key material as `textarea`, URLs with `"url": true`), where to
   get credentials, sandbox data, and the labels customers see on checkout.
2. If it has a call that proves credentials without creating a payment, teach
   `services/gateways.py:_probe` to use it. Otherwise the connection test reports
   "reachable" and says so.
3. `cloud/tests/test_gateways_api.py` checks that every catalog entry has a driver and
   that required fields match.

The dashboard forms, the generator and validation all read the catalog; no other code
changes are needed.

## Secret scan

CI scans every commit with [gitleaks](https://github.com/gitleaks/gitleaks). The same
check, locally, after committing:

```bash
docker run --rm -v "$PWD:/repo:ro" ghcr.io/gitleaks/gitleaks:v8.30.1 \
  git /repo --config /repo/.gitleaks.toml --redact --no-banner --verbose
```

`.gitleaks.toml` lists the only exceptions: published sandbox credentials and test fixtures.

## Style

Python: `ruff format` and `ruff check` (configuration in `pyproject.toml`). TypeScript:
strict mode, `oxlint`. Comments say why, not what. User-facing text is plain and
specific: say what happened and what to do next.
