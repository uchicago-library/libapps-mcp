"""Error type carrying a stable error code for tool responses."""

from __future__ import annotations

from typing import Any


class LibAppsError(Exception):
    def __init__(self, message: str, code: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": self.message, "code": self.code}
        if self.status_code is not None:
            out["statusCode"] = self.status_code
        return out
