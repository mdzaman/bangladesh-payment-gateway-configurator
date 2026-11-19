# Self-hosting

One container image runs three ways: `serve` (API and dashboard), `worker`, `migrate`.
It needs PostgreSQL 14 or newer. Redis is optional.

## 1. One server, Docker Compose

```bash
git clone https://github.com/mdzaman/bangladesh-payment-gateway-configurator.git && cd bangladesh-payment-gateway-configurator
cp .env.example .env
docker compose run --rm api gen-key          # prints a master key entry
```

Edit `.env`:

```ini
BDPAY_CLOUD_ENV=production
BDPAY_SITE_ADDRESS=pay.example.com                 # Caddy obtains the certificate itself
BDPAY_CLOUD_PUBLIC_URL=https://pay.example.com
BDPAY_HTTP_PORT=80
BDPAY_HTTPS_PORT=443
BDPAY_CLOUD_MASTER_KEYS=k20261118:....             # from gen-key. Back it up somewhere else.
POSTGRES_PASSWORD=a-long-random-password
BDPAY_CLOUD_MAIL_BACKEND=smtp
BDPAY_CLOUD_SMTP_HOST=smtp.example.com
BDPAY_CLOUD_SMTP_PORT=587
BDPAY_CLOUD_SMTP_TLS=starttls
BDPAY_CLOUD_SMTP_USERNAME=...
BDPAY_CLOUD_SMTP_PASSWORD=...
BDPAY_CLOUD_MAIL_FROM=Pay <no-reply@pay.example.com>
BDPAY_CLOUD_EGRESS_IPS=203.0.113.10                # this server's public IPv4
```

Then:

```bash
docker compose up -d --build
docker compose exec api bdpay-cloud doctor
uv run python scripts/smoke_test.py https://pay.example.com   # from a checkout; needs sign-up open and the simulator on
```

Point the domain's DNS at the server and open ports 80 and 443. The compose file runs
Caddy (TLS), the API, one worker, the migration step, PostgreSQL and Redis. Containers
run as a non-root user with a read-only filesystem and no capabilities.

The first account: sign up in the browser, or, with sign-up closed
(`BDPAY_CLOUD_ALLOW_SIGNUP=false`):

```bash
docker compose exec api bdpay-cloud create-user --email you@example.com --name "Your Name" --platform-admin
```

A platform admin sees every workspace at `/admin` and can approve live mode or suspend.
Turn on two-factor sign-in for that account straight away (Account, "Two-factor
sign-in").

## 2. Kubernetes

Manifests are in `deploy/k8s` (Kustomize): API deployment with a migration init
container, readiness and liveness probes, an autoscaler and a disruption budget; worker
deployment; ingress; network policies.

```bash
# No image is published for this project: build your own and push it to your registry.
docker build -t registry.example.com/bdpay-cloud:1.0.0 .
docker push registry.example.com/bdpay-cloud:1.0.0

kubectl create namespace bdpay
kubectl -n bdpay create secret generic bdpay-cloud \
  --from-literal=BDPAY_CLOUD_DATABASE_URL='postgresql+psycopg://bdpay:...@your-db:5432/bdpay_cloud' \
  --from-literal=BDPAY_CLOUD_MASTER_KEYS="$(docker run --rm registry.example.com/bdpay-cloud:1.0.0 gen-key 2>/dev/null)" \
  --from-literal=BDPAY_CLOUD_REDIS_URL='redis://your-redis:6379/0' \
  --from-literal=BDPAY_CLOUD_SMTP_USERNAME=... --from-literal=BDPAY_CLOUD_SMTP_PASSWORD=... \
  --from-literal=BDPAY_CLOUD_METRICS_TOKEN="$(openssl rand -hex 24)"
cp -r deploy/k8s/overlays/example deploy/k8s/overlays/production    # set image, domain, egress IP
kubectl apply -k deploy/k8s/overlays/production
```

These manifests are checked in CI by rendering them (`kubectl kustomize`). They have not
been applied to a live cluster as part of this repository's tests, so review them against
your cluster's ingress class, storage and policies. They expect a managed PostgreSQL and
Redis; neither is deployed by them.

## 3. AWS reference

| Need | Use |
|---|---|
| API and worker | ECS Fargate services (or EKS with the manifests above), one task definition, different commands |
| Migrations | a one-off task `migrate` before each deploy, or `serve --migrate` |
| Database | RDS PostgreSQL, Multi-AZ, automated backups and point-in-time recovery |
| Rate-limit counters | ElastiCache (Redis or Valkey) |
| Secrets | Secrets Manager, injected as environment variables (the master keys above all) |
| TLS and routing | an Application Load Balancer; block `/metrics` on the public listener |
| **Fixed outbound IP** | private subnets behind a NAT gateway with an Elastic IP. Nagad and Upay whitelist the caller's IPv4, so every merchant on your deployment gives them this address. Put it in `BDPAY_CLOUD_EGRESS_IPS`. |

## Settings

Every setting is an environment variable, listed with comments in
[`.env.example`](../.env.example). The ones that decide how a deployment behaves:

| Variable | Meaning |
|---|---|
| `BDPAY_CLOUD_ENV` | `production` refuses unsafe settings at start (no key, http URL, console mail, private targets). |
| `BDPAY_CLOUD_PUBLIC_URL` | The https origin providers and customers reach. Callback and IPN URLs are built from it. |
| `BDPAY_CLOUD_MASTER_KEYS` | Key ring for stored secrets. |
| `BDPAY_CLOUD_ALLOW_SIGNUP` | `false`: accounts only by invitation or `create-user`. |
| `BDPAY_CLOUD_LIVE_MODE` | `self_serve`, `approval` (a platform admin approves each workspace) or `disabled`. |
| `BDPAY_CLOUD_SIMULATOR` | The sandbox test gateway. |
| `BDPAY_CLOUD_ALLOW_EXPERIMENTAL` | The UddoktaPay driver. Read [compliance.md](compliance.md) first. |
| `BDPAY_CLOUD_BASE_URL_ALLOWLIST` | Hosts allowed for gateway base-URL overrides. Set it on a shared deployment. |
| `BDPAY_CLOUD_EGRESS_IPS` | Shown to merchants for provider whitelisting. |
| `BDPAY_CLOUD_REDIS_URL` | Makes rate limits exact across replicas. |
| `BDPAY_CLOUD_DB_PGBOUNCER` | `true` behind PgBouncer in transaction mode. |

## Scaling

* **API**: stateless. Add replicas. Each handles `THREADPOOL_SIZE` requests at once and
  opens up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections.
* **Workers**: add replicas when the `bdpay_worker_backlog` metric grows. Work is
  claimed with `SKIP LOCKED`, so copies never collide.
* **PostgreSQL**: the one stateful part. Start with a managed instance; add PgBouncer
  when connections, not CPU, become the limit. `orders` and `order_events` are the
  tables that grow; both carry `(workspace_id, mode)` and a time-ordered key if you later
  partition them.
* Run one process per container (`--workers 1`) and scale with containers; metrics are
  per process.

On a laptop, one API container handled about 85 simulated payment starts per second
(172 requests per second, median 170 ms, 32 concurrent clients). Treat that as a sanity
check, not a capacity figure: measure on your own hardware with
`python scripts/load_check.py`.

## Backups and upgrades

* Back up PostgreSQL (point-in-time recovery if you can) **and** the master keys, in
  different places. Either one alone is not enough to recover gateway credentials.
* Upgrade by deploying the new image. `migrate` runs first (the compose `migrate`
  service, the Kubernetes init container, or `serve --migrate`); it takes a lock, so
  rolling several replicas is safe. Migrations in this repository are written to be
  compatible with the previous release still serving during the roll-out.
* Roll back by deploying the previous image. Do not downgrade the schema unless the
  release notes say a migration requires it.
