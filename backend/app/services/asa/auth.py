"""Apple Search Ads OAuth2 client-credentials auth.

ASA Advanced uses an ES256-signed client_secret JWT (PyJWT) to obtain a
1-hour access token from `appleid.apple.com`. Tokens are cached
in-process per credential id.
"""
from __future__ import annotations

import asyncio
import time
from typing import Final

import httpx
import jwt as pyjwt

from app.services.asa.errors import ASAAPIError

ASA_AUDIENCE: Final[str] = "https://appleid.apple.com"
ASA_TOKEN_URL: Final[str] = "https://appleid.apple.com/auth/oauth2/token"
ASA_OAUTH_SCOPE: Final[str] = "searchadsorg"
# 2 hours — outlives Apple's 1h access token so a 401-triggered refresh
# always has a valid client_secret without needing to re-decrypt the
# private key. Apple allows up to 180 days; we keep this conservative.
CLIENT_SECRET_TTL_SECONDS: Final[int] = 2 * 60 * 60


def build_client_secret(
    *,
    client_id: str,
    team_id: str,
    key_id: str,
    private_key_pem: str,
    now: int | None = None,
) -> str:
    """Sign an ES256 JWT used as the OAuth2 client_secret.

    Apple requires sub=client_id, iss=team_id, aud=appleid.apple.com,
    a short-ish exp (≤180 days), and the kid in the header.
    """
    iat = int(now if now is not None else time.time())
    payload = {
        "sub": client_id,
        "aud": ASA_AUDIENCE,
        "iss": team_id,
        "iat": iat,
        "exp": iat + CLIENT_SECRET_TTL_SECONDS,
    }
    return pyjwt.encode(
        payload,
        private_key_pem,
        algorithm="ES256",
        headers={"kid": key_id},
    )


async def fetch_access_token(
    *,
    client_id: str,
    client_secret: str,
    http: httpx.AsyncClient | None = None,
) -> tuple[str, float]:
    """Exchange the client_secret JWT for an access token.

    Returns (access_token, expires_at_unix_seconds). Caller is
    responsible for caching; see `AccessTokenCache` below.
    """
    own_http = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(
            ASA_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": ASA_OAUTH_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise ASAAPIError(
                "token request failed",
                status=resp.status_code,
                body=resp.text,
            )
        data = resp.json()
        return data["access_token"], time.time() + int(data.get("expires_in", 3600))
    finally:
        if own_http:
            await client.aclose()


class AccessTokenCache:
    """Per-process access-token cache keyed by credential id.

    Tokens have a 1h TTL from Apple; we expire 5 seconds early as a
    safety margin against clock skew + in-flight requests racing the
    expiry.
    """

    _SAFETY_MARGIN_SECONDS: int = 5

    def __init__(self) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, credential_id: int) -> str | None:
        async with self._lock:
            entry = self._tokens.get(credential_id)
            if entry is None:
                return None
            token, expires_at = entry
            if expires_at <= time.time() + self._SAFETY_MARGIN_SECONDS:
                self._tokens.pop(credential_id, None)
                return None
            return token

    async def set(
        self, credential_id: int, token: str, expires_at: float,
    ) -> None:
        async with self._lock:
            self._tokens[credential_id] = (token, expires_at)

    async def invalidate(self, credential_id: int) -> None:
        async with self._lock:
            self._tokens.pop(credential_id, None)


_TOKEN_CACHE = AccessTokenCache()


def get_token_cache() -> AccessTokenCache:
    """Return the per-process AccessTokenCache singleton."""
    return _TOKEN_CACHE
