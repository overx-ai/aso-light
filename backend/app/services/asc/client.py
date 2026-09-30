"""Base client for App Store Connect API v1."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx
import jwt
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.services.asc.errors import (
    ASCAPIError,
    ASCNetworkError,
    ASCRateLimitError,
    CredentialDecryptError,
)

if TYPE_CHECKING:
    from app.models.credential import ASCCredential

logger = logging.getLogger(__name__)

_TOKEN_LIFETIME_SECONDS = 20 * 60  # 20 minutes per Apple docs
_MAX_RETRIES = 6
_BACKOFF_BASE = 1.0  # seconds
_MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests (~7 req/s)
_TRANSIENT_5XX = frozenset({500, 502, 503, 504})
# A POST that answered 5xx or timed out may still have created its resource;
# callers read back instead of retrying it blind.
_IDEMPOTENT = frozenset({"GET", "PUT", "PATCH", "DELETE"})
# Failures raised before the request left the machine, safe to retry for any method.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _error_body(response: httpx.Response) -> dict:
    if not response.content:
        return {"errors": []}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        return body
    return {"errors": [{"detail": response.text[:500]}]}


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise ASCRateLimitError(_error_body(response), retry_after=0)
    if response.status_code >= 400:
        raise ASCAPIError(response.status_code, _error_body(response))


def _backoff_delay(attempt: int) -> float:
    return _BACKOFF_BASE * (2 ** attempt)


def _retry_after(response: httpx.Response, attempt: int) -> float:
    # Retry-After may also be an HTTP date; the exponential delay stands in for it.
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return _backoff_delay(attempt)


async def _backoff(method: str, reason: str, attempt: int) -> None:
    delay = _backoff_delay(attempt)
    logger.warning(
        "ASC API %s failed (%s), retrying in %.1fs (attempt %d/%d)",
        method,
        reason,
        delay,
        attempt + 1,
        _MAX_RETRIES,
    )
    await asyncio.sleep(delay)


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

    async def _send(
        self,
        method: str,
        send: Callable[[], Awaitable[httpx.Response]],
        *,
        refresh_on_401: bool = False,
    ) -> httpx.Response:
        """Send with retries and return the last answer, whatever its status.

        Retried: 429 (global backoff, any method); 500/502/503/504 and
        timeouts or dropped connections on an idempotent method; a connect
        failure on any method. The first 401 refreshes the token when asked to.
        A network failure that is not retried, or outlasts the retries,
        raises :class:`ASCNetworkError`.
        """
        refreshed = False
        for attempt in range(_MAX_RETRIES):
            last = attempt == _MAX_RETRIES - 1
            await self._throttle()
            try:
                response = await send()
            except httpx.TransportError as exc:
                if last or not (isinstance(exc, _NOT_SENT) or method in _IDEMPOTENT):
                    raise ASCNetworkError(exc) from exc
                await _backoff(method, type(exc).__name__, attempt)
                continue

            if last:
                return response
            status = response.status_code
            if status == 401 and refresh_on_401 and not refreshed:
                refreshed = True
                logger.warning("ASC API returned 401, refreshing token")
                await self.close()
                continue
            if status == 429:
                retry_after = _retry_after(response, attempt)
                # Global, so every concurrent request waits too.
                self._backoff_until = time.time() + retry_after
                logger.warning(
                    "ASC API rate limited, backing off %.1fs (attempt %d/%d)",
                    retry_after,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue
            if status in _TRANSIENT_5XX and method in _IDEMPOTENT:
                await _backoff(method, str(status), attempt)
                continue
            return response
        raise AssertionError("unreachable: the last attempt always returns or raises")

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        """Make an authenticated request to ASC API; see :meth:`_send`.

        Raises ASCAPIError (ASCRateLimitError for 429) on a final 4xx/5xx.
        """
        async def send() -> httpx.Response:
            client = await self._get_client()
            return await client.request(method, path, **kwargs)

        response = await self._send(method, send, refresh_on_401=True)
        _raise_for_status(response)
        if response.status_code == 204:
            return {}
        return response.json()

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
        async def send() -> httpx.Response:
            async with httpx.AsyncClient(timeout=120.0) as upload_client:
                return await upload_client.put(
                    url,
                    content=data,
                    headers={"Content-Type": content_type},
                )

        _raise_for_status(await self._send("PUT", send))

    async def _get_binary(self, url: str) -> bytes:
        """GET raw bytes from an absolute URL (Apple download endpoint).

        Analytics report segments are served from pre-signed URLs, the read
        counterpart of :meth:`_put_binary`'s upload URLs. They must NOT carry
        the ASC Bearer token — Apple rejects a signed URL that also presents
        auth headers — so this uses a separate client with no auth.
        """
        async def send() -> httpx.Response:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as dl:
                return await dl.get(url)

        response = await self._send("GET", send)
        _raise_for_status(response)
        return response.content

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
            async def send(url: str = next_url) -> httpx.Response:
                client = await self._get_client()
                return await client.get(url)

            raw_response = await self._send("GET", send, refresh_on_401=True)
            _raise_for_status(raw_response)
            response = raw_response.json()
            all_items.extend(response.get("data", []))

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
        using Fernet symmetric encryption and verifies the result is a parseable
        PEM private key. Raises :class:`CredentialDecryptError` if decryption
        fails (wrong/rotated FERNET_KEY) or the decrypted bytes are not a valid
        PEM private key (legacy/corrupt rows). The error message is safe to show
        to end users; it never includes ciphertext or key material.
        """
        from app.core.security import decrypt_value

        try:
            private_key = decrypt_value(credential.private_key_encrypted)
        except InvalidToken as exc:
            raise CredentialDecryptError(
                "Stored credential cannot be decrypted (FERNET_KEY mismatch or "
                "corrupt data). Re-upload your .p8 key."
            ) from exc

        try:
            load_pem_private_key(private_key.encode("utf-8"), password=None)
        except (ValueError, TypeError) as exc:
            raise CredentialDecryptError(
                "Stored credential is not a valid PEM private key. "
                "Re-upload your .p8 key."
            ) from exc

        return cls(
            issuer_id=credential.issuer_id,
            key_id=credential.key_id,
            private_key=private_key,
        )
