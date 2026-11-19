"""Structured logging. Every record goes through the reference ``redact`` so
secrets and Bangladeshi mobile numbers are masked before anything is written
(rule 7 of the build spec)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

from bdpay.http import redact

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        rid = request_id.get()
        if rid:
            out["request_id"] = rid
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        if extra:
            out.update(redact(extra))
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id.get()
        base = f"{record.levelname:<7} {record.name}: {redact(record.getMessage())}"
        if rid:
            base += f" [{rid}]"
        if record.exc_info:
            base += "\n" + str(redact(self.formatException(record.exc_info)))
        return base


def configure(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's own access log would duplicate ours and print raw paths,
    # which for provider callbacks contain a signed token.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
