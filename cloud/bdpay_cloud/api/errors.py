"""One error shape for every JSON endpoint, the one the reference REST API uses::

    {"detail": {"code": "...", "provider": "...", "message": "..."}}

``code`` is stable and safe to branch on. Provider errors keep the reference
codes; errors raised by the platform itself use ``provider: "platform"``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bdpay.core import BdPayError
from bdpay.http import redact

from .. import logs
from ..errors import ServiceError

log = logging.getLogger("bdpay_cloud.api")

# Reference mapping, except CONFIG: for a hosted tenant "gateway not enabled"
# or "missing settings" is the caller's configuration, not a server fault.
BDPAY_HTTP = {
    "VALIDATION": 400,
    "AMOUNT_MISMATCH": 409,
    "DUPLICATE": 409,
    "NOT_FOUND": 404,
    "CONFIG": 400,
    "SIGNATURE_INVALID": 403,
    "NOT_SUPPORTED": 400,
    "AUTH": 502,
    "PROVIDER_DECLINED": 402,
    "PROVIDER_UNAVAILABLE": 503,
    "TIMEOUT": 504,
}

_STATUS_CODES = {
    400: "VALIDATION",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "DUPLICATE",
    413: "TOO_LARGE",
    422: "VALIDATION",
    429: "RATE_LIMITED",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        provider: str = "platform",
        errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.provider, self.errors, self.headers = provider, errors or [], headers or {}


def envelope(
    code: str, message: str, provider: str = "platform", errors: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": code, "provider": provider, "message": message}
    if errors:
        detail["errors"] = errors
    rid = logs.request_id.get()
    if rid:
        detail["request_id"] = rid
    return {"detail": detail}


def bdpay_error_response(e: BdPayError) -> JSONResponse:
    return JSONResponse(envelope(e.code, str(redact(e.message)), e.provider), status_code=BDPAY_HTTP.get(e.code, 400))


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(envelope(e.code, e.message, e.provider, e.errors), status_code=e.status, headers=e.headers)

    @app.exception_handler(ServiceError)
    async def _service(_: Request, e: ServiceError):
        return JSONResponse(envelope(e.code, e.message, "platform", e.errors), status_code=e.status)

    @app.exception_handler(BdPayError)
    async def _bdpay(_: Request, e: BdPayError):
        return bdpay_error_response(e)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        errors = []
        for err in e.errors():
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
            errors.append({"field": ".".join(loc), "message": err.get("msg", "invalid")})
        first = errors[0] if errors else {"field": "", "message": "invalid request"}
        message = f"{first['field']}: {first['message']}" if first["field"] else first["message"]
        return JSONResponse(envelope("VALIDATION", message, "platform", errors), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = _STATUS_CODES.get(e.status_code, "ERROR")
        return JSONResponse(envelope(code, str(e.detail)), status_code=e.status_code, headers=e.headers)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, e: Exception):
        log.exception("unhandled error on %s %s", request.method, getattr(request.scope.get("route"), "path", "?"))
        return JSONResponse(
            envelope("INTERNAL", "Something went wrong on our side. Quote the request id if you contact support."),
            status_code=500,
        )
