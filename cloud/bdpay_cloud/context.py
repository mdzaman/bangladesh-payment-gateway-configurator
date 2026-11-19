"""Small value types shared across the service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    """The tenant boundary: one workspace in one environment. Every query that
    touches tenant data takes a Scope; nothing is looked up by a bare id."""

    workspace_id: str
    mode: str  # "sandbox" | "live"

    def __post_init__(self) -> None:
        if self.mode not in ("sandbox", "live"):
            raise ValueError(f"invalid mode {self.mode!r}")

    @property
    def params(self) -> dict:
        return {"ws": self.workspace_id, "mode": self.mode}


@dataclass(frozen=True)
class Actor:
    """Who is doing something, for the audit log."""

    type: str  # "user" | "api_key" | "system"
    id: str | None = None
    label: str = ""
    ip: str | None = None

    @classmethod
    def system(cls, label: str = "system") -> Actor:
        return cls("system", None, label)
