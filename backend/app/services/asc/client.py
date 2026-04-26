"""Base client for App Store Connect API v1."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx
import jwt

from app.services.asc.errors import ASCAPIError, ASCRateLimitError

if TYPE_CHECKING:
    from app.models.credential import ASCCredential

logger = logging.getLogger(__name__)

_TOKEN_LIFETIME_SECONDS = 20 * 60  # 20 minutes per Apple docs
_MAX_RETRIES = 6
_BACKOFF_BASE = 1.0  # seconds
_MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests (~7 req/s)


class ASCClient:
    """Base client for App Store Connect API v1.

    Handles JWT generation, authenticated requests, pagination,
    rate-limit retries, and token refresh on 401.
    """

    BASE_URL = "https://api.appstoreconnect.apple.com/v1"

    def __init__(self, issuer_id: str, key_id: str, private_key: str):
        """
        Args:
            issuer_id: Apple Issuer ID.
            key_id: Apple Key ID.
            private_key: Decrypted .p8 private key content (PEM format).
        """
        self.issuer_id = issuer_id
        self.key_id = key_id
        self.private_key = private_key
        self._client: httpx.AsyncClient | None = None
        self._token_issued_at: float = 0.0
        self._rate_lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._backoff_until: float = 0.0

    # ------------------------------------------------------------------
    # JWT token generation
    # ------------------------------------------------------------------

    def _generate_token(self) -> str:
        """Generate a JWT token for ASC API authentication.

        Apple requires:
        - Algorithm: ES256
        - Header: {"alg": "ES256", "kid": key_id, "typ": "JWT"}
        - Payload: {"iss": issuer_id, "iat": now, "exp": now + 20min,
                     "aud": "appstoreconnect-v1"}
        """
        now = int(time.time())
        payload = {
            "iss": self.issuer_id,
            "iat": now,
            "exp": now + _TOKEN_LIFETIME_SECONDS,
            "aud": "appstoreconnect-v1",
        }
        headers = {
            "alg": "ES256",
            "kid": self.key_id,
            "typ": "JWT",
        }
        token: str = jwt.encode(
            payload,
            self.private_key,
            algorithm="ES256",
            headers=headers,
        )
        self._token_issued_at = now
        return token

    def _is_token_expired(self) -> bool:
        """Check whether the current token is near expiry (with 60s margin)."""
        if self._token_issued_at == 0.0:
            return True
        return time.time() >= self._token_issued_at + _TOKEN_LIFETIME_SECONDS - 60

    # ------------------------------------------------------------------
    # HTTP client management
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx async client with auth headers.

        Recreates the client if the token has expired or the client is closed.
        """
        if (
            self._client is None
            or self._client.is_closed
            or self._is_token_expired()
        ):
            await self.close()
            token = self._generate_token()
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    # ------------------------------------------------------------------
    # Core request method with retry logic
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        """Enforce minimum interval between requests and respect backoff."""
        async with self._rate_lock:
            now = time.time()

            # If a 429 set a global backoff, wait for it
            if now < self._backoff_until:
                wait = self._backoff_until - now
                logger.debug("Rate limiter: waiting %.1fs (backoff)", wait)
                await asyncio.sleep(wait)

            # Enforce minimum interval between requests
            elapsed = time.time() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

            self._last_request_at = time.time()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        """Make an authenticated request to ASC API with error handling.

        Handles:
        - Rate limiting: global throttle + exponential backoff on 429.
        - 401 Unauthorized: regenerate token and retry once.
        - 4xx/5xx: raise ASCAPIError with parsed error details.
        """
        client = await self._get_client()

        for attempt in range(_MAX_RETRIES):
            await self._throttle()
            response = await client.request(method, path, **kwargs)

            if response.status_code == 401 and attempt == 0:
                logger.warning("ASC API returned 401, refreshing token")
                await self.close()
                client = await self._get_client()
                continue

            if response.status_code == 429:
                retry_after = float(
                    response.headers.get("Retry-After", _BACKOFF_BASE * (2 ** attempt))
                )
                # Set global backoff so all concurrent requests wait
                self._backoff_until = time.time() + retry_after
                logger.warning(
                    "ASC API rate limited, backing off %.1fs (attempt %d/%d)",
                    retry_after,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 400:
                body = response.json() if response.content else {"errors": []}
                raise ASCAPIError(response.status_code, body)

            # 204 No Content (e.g. DELETE responses)
            if response.status_code == 204:
                return {}

            return response.json()

        # Exhausted all retries
        body = response.json() if response.content else {"errors": []}  # type: ignore[possibly-undefined]
        raise ASCRateLimitError(body, retry_after=0)  # type: ignore[possibly-undefined]

    # ------------------------------------------------------------------
    # Convenience HTTP methods
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET request."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        """POST request."""
        return await self._request("POST", path, json=json)

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        """PATCH request."""
        return await self._request("PATCH", path, json=json)

    async def _delete(self, path: str) -> None:
        """DELETE request."""
        await self._request("DELETE", path)

    async def _put_binary(
        self,
        url: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """PUT raw binary to an absolute URL (Apple upload endpoint).

        Apple's asset upload flow returns pre-signed S3 URLs.
        These must NOT include the ASC Bearer token — uses a
        separate httpx client without auth headers.
        """
        await self._throttle()
        async with httpx.AsyncClient(timeout=120.0) as upload_client:
            response = await upload_client.put(
                url,
                content=data,
                headers={"Content-Type": content_type},
            )
        if response.status_code >= 400:
            raise ASCAPIError(
                response.status_code,
                {"errors": [{"detail": response.text[:500]}]},
            )

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def _get_all_pages(
        self,
        path: str,
        params: dict | None = None,
    ) -> list[dict]:
        """Fetch all pages of a paginated ASC API response.

        ASC API uses cursor-based pagination with a ``next`` link:

        .. code-block:: json

            {
                "data": [...],
                "links": {
                    "self": "...",
                    "next": "...?cursor=..."
                }
            }

        Returns:
            Combined list of all ``data`` items across every page.
        """
        all_items: list[dict] = []
        current_params = dict(params) if params else {}

        response = await self._get(path, params=current_params)
        all_items.extend(response.get("data", []))

        while True:
            next_url = response.get("links", {}).get("next")
            if not next_url:
                break

            # The "next" link is an absolute URL; request it directly.
            for attempt in range(_MAX_RETRIES):
                await self._throttle()
                client = await self._get_client()
                raw_response = await client.get(next_url)

                if raw_response.status_code == 429:
                    retry_after = float(
                        raw_response.headers.get("Retry-After", _BACKOFF_BASE * (2 ** attempt))
                    )
                    self._backoff_until = time.time() + retry_after
                    logger.warning(
                        "ASC API rate limited during pagination, backing off %.1fs",
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if raw_response.status_code >= 400:
                    body = raw_response.json() if raw_response.content else {"errors": []}
                    raise ASCAPIError(raw_response.status_code, body)

                response = raw_response.json()
                all_items.extend(response.get("data", []))
                break
            else:
                # Exhausted retries on this page
                body = raw_response.json() if raw_response.content else {"errors": []}  # type: ignore[possibly-undefined]
                raise ASCRateLimitError(body, retry_after=0)  # type: ignore[possibly-undefined]

        return all_items

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> ASCClient:
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        await self.close()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_credential(cls, credential: ASCCredential) -> ASCClient:
        """Create a client from a database credential record.

        Decrypts the private key stored in ``credential.private_key_encrypted``
        using Fernet symmetric encryption.
        """
        from app.core.security import decrypt_value

        private_key = decrypt_value(credential.private_key_encrypted)
        return cls(
            issuer_id=credential.issuer_id,
            key_id=credential.key_id,
            private_key=private_key,
        )
