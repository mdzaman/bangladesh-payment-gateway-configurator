"""Health, readiness and metrics."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__, metrics, migrate
from ..db import ping
from ..engine.runtime import Runtime
from .deps import get_rt
from .errors import ApiError

router = APIRouter(tags=["Operations"])


@router.get("/healthz", summary="Liveness: the process is up")
def healthz():
    return {"ok": True, "version": __version__}


@router.get("/readyz", summary="Readiness: database reachable and migrated")
def readyz(request: Request, rt: Runtime = Depends(get_rt)):
    checks = {"database": ping(rt.engine)}
    checks["migrations"] = checks["database"] and migrate.is_current(rt.engine)
    limiter = request.app.state.limiter
    if hasattr(limiter, "ping"):
        checks["redis"] = limiter.ping()
    # Redis is optional at runtime (limits fall back to in-process), so it does not gate readiness.
    ready = checks["database"] and checks["migrations"]
    return JSONResponse({"ok": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics", include_in_schema=False)
def prometheus(request: Request, rt: Runtime = Depends(get_rt)):
    token = rt.settings.metrics_token
    if token:
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given, f"Bearer {token}"):
            raise ApiError(401, "UNAUTHENTICATED", "metrics need a bearer token")
    return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
