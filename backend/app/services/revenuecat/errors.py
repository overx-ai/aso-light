"""RevenueCat REST API errors."""

from __future__ import annotations


class RevenueCatAPIError(Exception):
    """Raised when the RevenueCat REST API returns a non-2xx response."""

    def __init__(self, status_code: int, body: dict | str):
        self.status_code = status_code
        self.body = body
        if isinstance(body, dict):
            self.message = (
                body.get("message")
                or body.get("error")
                or body.get("title")
                or str(body)[:200]
            )
        else:
            self.message = str(body)[:200]
        super().__init__(f"RevenueCat API {status_code}: {self.message}")


class RevenueCatRateLimitError(RevenueCatAPIError):
    """Raised when RevenueCat returns 429 after all retries are exhausted."""

    def __init__(self, body: dict | str, retry_after: float):
        super().__init__(429, body)
        self.retry_after = retry_after
