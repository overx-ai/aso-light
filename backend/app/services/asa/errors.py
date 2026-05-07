"""Errors raised from the Apple Search Ads service layer."""
from __future__ import annotations


class ASAAPIError(Exception):
    """An error from the Apple Search Ads API.

    Carries `status` (HTTP status, when available) and a truncated `body`
    excerpt for diagnostics. Service code should map this to ToolError /
    HTTPException at the API boundary; never expose the raw body to LLM
    clients or end users.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        self.message = message
        self.status = status
        self.body = body
        super().__init__(message)

    def __str__(self) -> str:
        body_preview = (self.body or "")[:500]
        if self.status is not None:
            return f"ASA API {self.status}: {self.message} ({body_preview})"
        return f"ASA API: {self.message} ({body_preview})"
