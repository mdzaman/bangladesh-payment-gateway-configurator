# syntax=docker/dockerfile:1.7
# One image, three commands: `serve` (API + dashboard), `worker`, `migrate`.

# ---- dashboard ---------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
# The build bundles the reference engine (for the generator) and the gateway catalog.
COPY core /src/core
COPY cloud/bdpay_cloud/catalog /src/cloud/bdpay_cloud/catalog
COPY web ./
RUN npm run build

# ---- python dependencies, exactly as locked in uv.lock ---------------------------
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY core/bdpay core/bdpay
COPY cloud/bdpay_cloud cloud/bdpay_cloud
RUN uv sync --frozen --no-dev --no-editable

# ---- runtime -----------------------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" \
    BDPAY_CLOUD_ENV=production BDPAY_CLOUD_WEB_DIST=/app/web-dist
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin bdpay
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=web /src/web/dist /app/web-dist
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["bdpay-cloud"]
# The API sits behind a reverse proxy on a private network, so its forwarded headers are trusted.
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
