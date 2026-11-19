# Common tasks. `make help` lists them.
.DEFAULT_GOAL := help
PY := .venv/bin/python
DEV := docker compose -f docker-compose.dev.yml
# Development always uses the services from docker-compose.dev.yml and port 8000,
# whatever a local .env written for a deployment says.
DEVENV := BDPAY_CLOUD_ENV=development BDPAY_CLOUD_PUBLIC_URL=http://localhost:8000 \
  BDPAY_CLOUD_DATABASE_URL=postgresql+psycopg://bdpay:bdpay@127.0.0.1:54329/bdpay_cloud \
  BDPAY_CLOUD_REDIS_URL=redis://127.0.0.1:63790/0 BDPAY_CLOUD_WEB_DIST=web/dist BDPAY_CLOUD_LOG_FORMAT=text

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

setup: ## install Python and Node dependencies
	uv sync
	cd web && npm ci

services: ## start PostgreSQL and Redis for development
	$(DEV) up -d postgres redis

migrate: ## bring the development database up to date
	$(DEVENV) .venv/bin/bdpay-cloud migrate

dev: services migrate web-build ## run the API with the dashboard on http://localhost:8000
	$(DEVENV) .venv/bin/bdpay-cloud serve --host 127.0.0.1 --port 8000

worker: ## run the background worker
	$(DEVENV) .venv/bin/bdpay-cloud worker

web-dev: ## dashboard with hot reload on http://localhost:5173 (needs `make dev` too)
	cd web && npm run dev

web-build: ## build the dashboard into web/dist
	cd web && npm run build

test: test-core test-cloud test-web ## run every test-suite

test-core: ## the 35 reference tests of the engine
	cd core && ../$(PY) -m pytest -q

test-cloud: services ## service tests (needs PostgreSQL)
	$(PY) -m pytest

test-web: ## dashboard and generator tests
	cd web && npm test

lint: ## ruff, type-check and lint
	.venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd web && npm run typecheck && npm run lint

format: ## format the Python code
	.venv/bin/ruff format . && .venv/bin/ruff check . --fix

doctor: ## check configuration, database, keys
	$(DEVENV) .venv/bin/bdpay-cloud doctor

openapi: ## write the merchant API description to docs/openapi.json
	$(DEVENV) .venv/bin/bdpay-cloud openapi > docs/openapi.json

image: ## build the container image
	docker build -t bdpay-cloud:local .

up: ## run the whole stack in containers on http://localhost:8080
	docker compose up -d --build

down: ## stop the container stack
	docker compose down

.PHONY: help setup services migrate dev worker web-dev web-build test test-core test-cloud test-web lint format doctor openapi image up down
