"""Async HTTP client for the Apple Search Ads Advanced API.

Mirrors the shape of `app.services.asc.client.ASCClient`: 150ms minimum
inter-request interval to stay under Apple's soft cap, exponential
jittered backoff on 429, automatic single retry on 401 (after token
refresh), max 6 retries before raising. All errors map to ASAAPIError;
the API boundary is responsible for translating those to ToolError /
HTTPException.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Final

import httpx

from app.core.security import decrypt_value
from app.models.asa import ASACredential
from app.services.asa.auth import (
    build_client_secret,
    fetch_access_token,
    get_token_cache,
)
from app.services.asa.errors import ASAAPIError

ASA_API_BASE: Final[str] = "https://api.searchads.apple.com/api/v5"
_MIN_REQUEST_INTERVAL: Final[float] = 0.15
_MAX_RETRIES: Final[int] = 6
_BACKOFF_CAP: Final[float] = 60.0

logger = logging.getLogger(__name__)


class ASAClient:
    """Async client for the ASA v5 API.

    Use `ASAClient.from_credential(cred)` for production; the test-only
    `__test_with_token__` factory skips the auth round-trip when you
    already have a transport and access token (e.g. with httpx.MockTransport).
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        access_token: str,
        credential_id: int | None = None,
        client_id: str | None = None,
        client_secret_jwt: str | None = None,
    ) -> None:
        self._http = http
        self._access_token = access_token
        self._credential_id = credential_id
        self._client_id = client_id
        self._client_secret_jwt = client_secret_jwt
        self._last_request_at = 0.0
        self._gate = asyncio.Lock()  # serializes the rate-limit gate

    @classmethod
    async def from_credential(cls, cred: ASACredential) -> "ASAClient":
        """Build a client from a stored ASACredential. Decrypts the .p8 in
        memory only for the lifetime of this call."""
        client_id = decrypt_value(cred.client_id_ciphertext)
        team_id = decrypt_value(cred.team_id_ciphertext)
        private_key_pem = decrypt_value(cred.private_key_ciphertext)
        client_secret = build_client_secret(
            client_id=client_id, team_id=team_id, key_id=cred.key_id,
            private_key_pem=private_key_pem,
        )
        cache = get_token_cache()
        token = await cache.get(cred.id)
        http = httpx.AsyncClient(base_url=ASA_API_BASE, timeout=60.0)
        if token is None:
            try:
                token, expires_at = await fetch_access_token(
                    client_id=client_id, client_secret=client_secret, http=http,
                )
            except Exception:
                await http.aclose()
                raise
            await cache.set(cred.id, token, expires_at)
        return cls(
            http=http,
            access_token=token,
            credential_id=cred.id,
            client_id=client_id,
            client_secret_jwt=client_secret,
        )

    @classmethod
    def __test_with_token__(
        cls, *, http: httpx.AsyncClient, access_token: str,
    ) -> "ASAClient":
        """Test-only factory — accepts a preconfigured transport + token."""
        return cls(http=http, access_token=access_token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _refresh_token(self) -> None:
        if (
            self._credential_id is None
            or self._client_id is None
            or self._client_secret_jwt is None
        ):
            return
        cache = get_token_cache()
        await cache.invalidate(self._credential_id)
        token, expires_at = await fetch_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret_jwt,
            http=self._http,
        )
        await cache.set(self._credential_id, token, expires_at)
        self._access_token = token

    async def _await_rate_gate(self) -> None:
        async with self._gate:
            elapsed = time.time() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_at = time.time()

    async def request(
        self,
        method: str,
        path: str,
        *,
        org_id: int | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Issue one request with retry semantics; return parsed JSON."""
        backoff = 1.0
        last_resp: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES):
            await self._await_rate_gate()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            if org_id is not None:
                headers["X-AP-Context"] = f"orgId={org_id}"
            try:
                resp = await self._http.request(
                    method, path, json=json, params=params, headers=headers,
                )
            except httpx.HTTPError as exc:
                logger.warning("ASA HTTP error: %s", exc)
                await asyncio.sleep(backoff + random.random() * 0.25)
                backoff = min(backoff * 2, _BACKOFF_CAP)
                continue
            if resp.status_code == 401 and attempt == 0:
                await self._refresh_token()
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                await asyncio.sleep(retry_after + random.random() * 0.25)
                backoff = min(backoff * 2, _BACKOFF_CAP)
                last_resp = resp
                continue
            if resp.status_code >= 400:
                raise ASAAPIError(
                    f"{method} {path} failed",
                    status=resp.status_code,
                    body=resp.text,
                )
            try:
                return resp.json()
            except Exception:
                return resp.text
        raise ASAAPIError(
            "exhausted retries",
            status=last_resp.status_code if last_resp is not None else None,
            body=last_resp.text if last_resp is not None else None,
        )

    async def get_all_pages(
        self,
        method: str,
        path: str,
        *,
        org_id: int | None = None,
        page_size: int = 1000,
    ) -> list[dict]:
        """Walk a paginated `find`-style endpoint until exhausted."""
        out: list[dict] = []
        offset = 0
        while True:
            payload = await self.request(
                method, path,
                org_id=org_id,
                json={"selector": {"pagination": {
                    "offset": offset, "limit": page_size,
                }}},
            )
            data = payload.get("data") or []
            out.extend(data)
            total = payload.get("pagination", {}).get("totalResults", len(out))
            offset += len(data)
            if not data or offset >= total:
                break
        return out
