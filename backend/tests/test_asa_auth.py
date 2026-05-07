import time

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1, generate_private_key,
)

from app.services.asa.auth import (
    ASA_AUDIENCE,
    CLIENT_SECRET_TTL_SECONDS,
    build_client_secret,
)


def _make_pem() -> str:
    key = generate_private_key(SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_build_client_secret_has_correct_header_and_claims():
    pem = _make_pem()
    iat = 1_700_000_000
    token = build_client_secret(
        client_id="SEARCHADS.x",
        team_id="TEAM",
        key_id="KID",
        private_key_pem=pem,
        now=iat,
    )
    headers = pyjwt.get_unverified_header(token)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert headers["alg"] == "ES256"
    assert headers["kid"] == "KID"
    assert payload["sub"] == "SEARCHADS.x"
    assert payload["aud"] == ASA_AUDIENCE
    assert payload["iss"] == "TEAM"
    assert payload["iat"] == iat
    assert payload["exp"] == iat + CLIENT_SECRET_TTL_SECONDS


import asyncio

import httpx

from app.services.asa.auth import (
    AccessTokenCache,
    fetch_access_token,
    get_token_cache,
)


def test_fetch_access_token_posts_correct_form():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"access_token": "abc", "expires_in": 3600, "token_type": "Bearer"},
        )

    async def go() -> tuple[str, float]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_access_token(
                client_id="cid", client_secret="jwt", http=client,
            )

    token, expires_at = asyncio.run(go())
    assert token == "abc"
    assert "client_id=cid" in captured["body"]
    assert "scope=searchadsorg" in captured["body"]
    assert "grant_type=client_credentials" in captured["body"]
    assert expires_at > time.time()


def test_fetch_access_token_raises_on_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad creds")

    async def go() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            try:
                await fetch_access_token(
                    client_id="cid", client_secret="jwt", http=client,
                )
            except Exception as exc:  # noqa: BLE001
                return exc
            return None

    err = asyncio.run(go())
    from app.services.asa.errors import ASAAPIError
    assert isinstance(err, ASAAPIError)
    assert err.status == 401


def test_access_token_cache_get_set_invalidate():
    async def go() -> list:
        cache = AccessTokenCache()
        await cache.set(1, "tok1", expires_at=time.time() + 1000)
        a = await cache.get(1)
        await cache.set(1, "tok2", expires_at=time.time() - 1)  # expired
        b = await cache.get(1)
        await cache.set(2, "tok3", expires_at=time.time() + 1000)
        await cache.invalidate(2)
        c = await cache.get(2)
        return [a, b, c]

    a, b, c = asyncio.run(go())
    assert a == "tok1"
    assert b is None  # expired
    assert c is None  # invalidated


def test_get_token_cache_returns_singleton():
    assert get_token_cache() is get_token_cache()
