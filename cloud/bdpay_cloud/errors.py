"""Base class for errors the service layer raises on purpose."""

from __future__ import annotations


class ServiceError(Exception):
    """An expected failure with a message that is safe to show the caller."""

    message: str = "Request could not be completed"
    status: int = 400
    code: str = "VALIDATION"
    errors: list[dict[str, str]] = []
