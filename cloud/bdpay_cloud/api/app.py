"""Application assembly: middleware, routers, static files."""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import anyio.to_thread
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from .. import __version__, logs, metrics
from ..engine.runtime import Runtime
from ..ids import rand_b32
from ..security.ratelimit import make_limiter
from ..settings import ConfigError, Settings, get_settings
from . import account, environment, errors, hosted, ops, provider, v1

log = logging.getLogger("bdpay_cloud.app")
access = logging.getLogger("bdpay_cloud.access")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_BODY_BYTES = 256 * 1024
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
# First path segments owned by the service, never by the single-page app.
RESERVED = (
    "api",
    "v1",
    "bdpay",
    "p",
    "checkout",
    "pay",
    "simulator",
    "static",
    "assets",
    "healthz",
    "readyz",
    "metrics",
    "docs",
    "openapi.json",
)
_NO_STORE = ("/api/", "/v1/", "/bdpay/", "/p/", "/checkout/", "/pay/", "/simulator/")
_QUIET = ("/healthz", "/readyz", "/metrics", "/static/", "/assets/")

CSP_APP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'; object-src 'none'"
)
CSP_DOCS = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'"
)

DESCRIPTION = """
One API for Bangladeshi payment gateways: **create order → redirect → verify → paid**.

Each workspace connects its **own** merchant accounts. Money always moves from the customer, through
the licensed provider, into the merchant's own account. This service never holds funds.

* Authenticate with `Authorization: Bearer <API key>`. Keys starting `bdp_test_` act on sandbox data,
  `bdp_live_` on live data.
* Amounts are BDT as decimal strings (`"1250.00"`); integer poisha is returned alongside.
* The amount stored when an order is created is the only amount ever accepted as paid. A provider answer
  with a different amount, currency or order number puts the order in `needs_review`, never `paid`.
* Send `Idempotency-Key` on POST requests you may need to retry.
* Order statuses: `created → pending → paid | failed | cancelled | expired | needs_review`, then
  `paid → partially_refunded → refunded`. Orders only move forward.
"""


class BodyTooLarge(HTTPException):
    """An HTTPException on purpose: FastAPI turns any other error raised while
    it reads a body into a generic 400."""

    def __init__(self) -> None:
        super().__init__(413, "Request body is too large")


class PlatformMiddleware:
    """Request id, access log, metrics, security headers and a body-size cap,
    as plain ASGI so it adds no per-request task."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.hsts = settings.cookie_secure

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        given = headers.get("x-request-id", "")
        rid = given if _REQUEST_ID.match(given) else "req_" + rand_b32(16)
        token = logs.request_id.set(rid)
        path, method = scope["path"], scope["method"]
        started = time.perf_counter()
        state = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                out = MutableHeaders(scope=message)
                out["X-Request-Id"] = rid
                out.setdefault("X-Content-Type-Options", "nosniff")
                out.setdefault("X-Frame-Options", "DENY")
                out.setdefault("Referrer-Policy", "no-referrer")
                out.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                out.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
                out.setdefault("Content-Security-Policy", CSP_DOCS if path.startswith("/docs") else CSP_APP)
                if self.hsts:
                    out.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
                if path.startswith(_NO_STORE):
                    out.setdefault("Cache-Control", "no-store")
                elif path.startswith("/assets/"):
                    out.setdefault("Cache-Control", "public, max-age=31536000, immutable")
            await send(message)

        received = 0

        async def limited_receive():
            # A chunked upload has no Content-Length, so count what actually arrives.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    raise BodyTooLarge()
            return message

        try:
            length = headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                response = JSONResponse(errors.envelope("TOO_LARGE", "Request body is too large"), status_code=413)
                return await response(scope, receive, send_wrapper)
            await self.app(scope, limited_receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            # The route template, never the raw path: callback paths contain a signed token.
            route = getattr(scope.get("route"), "path", None) or ("static" if path.startswith(_QUIET) else "unmatched")
            metrics.HTTP_REQUESTS.labels(method, route, str(state["status"])).inc()
            metrics.HTTP_SECONDS.labels(method, route).observe(elapsed)
            if not path.startswith(_QUIET):
                client = scope.get("client") or ("-", 0)
                access.info(
                    "%s %s %s",
                    method,
                    route,
                    state["status"],
                    extra={
                        "method": method,
                        "route": route,
                        "status": state["status"],
                        "ms": int(elapsed * 1000),
                        "ip": client[0],
                    },
                )
            logs.request_id.reset(token)


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else get_settings())
    logs.configure(settings.log_level, settings.log_format)
    problems = settings.problems()
    if problems:
        raise ConfigError("refusing to start:\n  - " + "\n  - ".join(problems))
    for warning in settings.warnings():
        log.warning(warning)
    rt = runtime or Runtime(settings)
    metrics.install_provider_tap()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Handlers are synchronous (the gateway drivers are), so this is the request concurrency.
        anyio.to_thread.current_default_thread_limiter().total_tokens = settings.threadpool_size
        yield
        if runtime is None:
            rt.close()

    app = FastAPI(
        title=f"{settings.brand_name} API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        swagger_ui_parameters={"defaultModelsExpandDepth": 0, "persistAuthorization": False},
    )
    app.state.rt = rt
    app.state.limiter = make_limiter(settings.redis_url)
    errors.install(app)

    app.add_middleware(PlatformMiddleware, settings=settings)

    # Only documents the scheme in OpenAPI; the real check is deps.api_key.
    bearer = HTTPBearer(auto_error=False, description="API key from the dashboard (Developers → API keys)")
    for router in v1.build_routers("/v1", full=True) + v1.build_routers("/bdpay", full=False):
        app.include_router(router, dependencies=[Depends(bearer)])
    app.include_router(ops.router)
    app.include_router(account.router)
    app.include_router(environment.router)
    app.include_router(provider.router)
    app.include_router(hosted.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _mount_dashboard(app, settings)
    return app


def _mount_dashboard(app: FastAPI, settings: Settings) -> None:
    """Serve the built dashboard (web/dist) from the same origin, with
    client-side routing: unknown non-API paths get index.html."""
    dist = Path(settings.web_dist).resolve() if settings.web_dist else None
    if dist is None or not (dist / "index.html").is_file():

        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return JSONResponse(
                {
                    "service": settings.brand_name,
                    "version": __version__,
                    "docs": "/docs" if settings.docs_enabled else None,
                    "note": "The dashboard is not bundled with this process (set BDPAY_CLOUD_WEB_DIST to web/dist).",
                }
            )

        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index_file = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str):
        if path.split("/", 1)[0] in RESERVED:
            raise HTTPException(404, "Not found")
        if path:
            candidate = (dist / path).resolve()
            if dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        # Read each time: it is tiny, and a rebuilt dashboard is picked up without a restart.
        return HTMLResponse(index_file.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
