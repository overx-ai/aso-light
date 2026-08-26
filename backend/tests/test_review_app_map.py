"""Reproduction + regression tests for bug-001: reviews cross-app IDOR.

ASC scopes ``/v1/customerReviews/*`` and ``/v1/customerReviewResponses/*`` to
the whole Apple team, not per app. ``list_reviews`` is app-scoped
(``GET /v1/apps/{asc_app_id}/customerReviews``); every other entry point
(``get_review``, ``draft``, ``translate``, ``create_response``,
``update_response``, ``delete_response``) takes a bare ``review_id`` /
``response_id`` and — before the fix — never verifies it belongs to the
caller's verified app.

Two independent ASO-Light tenants (app A / app B, each its own user +
credential) share one fake ASC client that — deliberately mirroring real
Apple behavior — answers a bare review/response id lookup regardless of
which app's client made the call. ``list_reviews`` is only ever called for
app A, so app B's review/response ids are never observed under app A. Every
``*_cross_app_*`` test attempts one of the 6 vulnerable entry points against
app B's ids while authenticated for app A and asserts a 404-equivalent.

Before the fix (``app/services/reviews/ownership.py`` wired into both
``app/api/v1/reviews.py`` and ``app/mcp/tools/reviews.py``), every one of
these calls succeeds instead of raising — that success *is* the
vulnerability. Confirmed by running this file against the pre-fix tree:
every ``pytest.raises`` block below failed with "DID NOT RAISE".
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy import select

import app.api.v1.reviews as rest_reviews
import app.mcp.context as mcp_context
import app.mcp.tools.reviews as mcp_reviews
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.schemas.review import DraftIn, ReplyIn, TranslateReviewIn
from tests._async_harness import run_async


# ----------------------------------------------------------------------
# Fake ASC client — answers bare review/response ids regardless of which
# app's client called. That's the crux of the vulnerability under test:
# real ASC behaves identically (customerReviews/customerReviewResponses
# are team-scoped, not app-scoped), so a fake that scoped by app wouldn't
# reproduce anything.
# ----------------------------------------------------------------------


def _review_page_entry(review_id: str, response_id: str | None, territory: str) -> dict:
    entry: dict[str, Any] = {
        "id": review_id,
        "attributes": {
            "rating": 4,
            "title": "Nice",
            "body": f"Body for {review_id}",
            "territory": territory,
            "reviewerNickname": "nick",
            "createdDate": "2026-01-01T00:00:00Z",
        },
    }
    if response_id:
        entry["relationships"] = {
            "response": {"data": {"type": "customerReviewResponses", "id": response_id}},
        }
    return entry


def _response_included(response_id: str) -> dict:
    return {
        "type": "customerReviewResponses",
        "id": response_id,
        "attributes": {
            "responseBody": f"Existing reply for {response_id}",
            "lastModifiedDate": "2026-01-02T00:00:00Z",
            "state": "PUBLISHED",
        },
    }


class _FakeReviewsASCClient:
    """Stub ASCClient: bare-id lookups ignore which app's client called."""

    def __init__(self) -> None:
        self._pages: dict[str, dict] = {}
        self._reviews: dict[str, dict] = {}
        self.created: list[tuple[str, str]] = []
        self.updated: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def seed_review(
        self, review_id: str, *, response_id: str | None, territory: str = "USA",
    ) -> None:
        self._reviews[review_id] = {
            "data": _review_page_entry(review_id, response_id, territory),
            "included": [_response_included(response_id)] if response_id else [],
        }

    def seed_app_page(self, asc_app_id: str, review_ids: list[str]) -> None:
        self._pages[asc_app_id] = {
            "data": [self._reviews[rid]["data"] for rid in review_ids],
            "included": [
                inc for rid in review_ids for inc in self._reviews[rid]["included"]
            ],
            "links": {},
        }

    async def _get(self, path: str, params: dict | None = None) -> dict:
        if path.startswith("/v1/apps/") and path.endswith("/customerReviews"):
            asc_app_id = path.split("/")[3]
            return self._pages.get(asc_app_id, {"data": [], "included": [], "links": {}})
        if path.startswith("/v1/customerReviews/"):
            review_id = path.rsplit("/", 1)[-1]
            return self._reviews.get(review_id, {"data": {}})
        raise AssertionError(f"unexpected GET {path}")

    async def _post(self, path: str, json: dict | None = None) -> dict:
        assert path == "/v1/customerReviewResponses"
        review_id = json["data"]["relationships"]["review"]["data"]["id"]
        body = json["data"]["attributes"]["responseBody"]
        self.created.append((review_id, body))
        return {
            "data": {
                "id": f"resp-new-{review_id}",
                "attributes": {"responseBody": body, "state": "PUBLISHED"},
            },
        }

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        response_id = path.rsplit("/", 1)[-1]
        body = json["data"]["attributes"]["responseBody"]
        self.updated.append((response_id, body))
        return {
            "data": {
                "id": response_id,
                "attributes": {"responseBody": body, "state": "PUBLISHED"},
            },
        }

    async def _delete(self, path: str) -> None:
        self.deleted.append(path.rsplit("/", 1)[-1])

    async def __aenter__(self) -> "_FakeReviewsASCClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


# ----------------------------------------------------------------------
# Seeding: two independent tenants (app A / app B)
# ----------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_tenant(suffix: str) -> tuple[int, int]:
    """Create one User + ASCCredential + App. Returns (user_id, app_id)."""
    async with async_session_factory() as session:
        user = User(
            email=f"reviews-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name=f"Reviews Tenant {suffix}",
        )
        session.add(user)
        await session.flush()

        cred = ASCCredential(
            user_id=user.id,
            name="ASC",
            issuer_id=f"issuer-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(cred)
        await session.flush()

        app = App(
            credential_id=cred.id,
            asc_app_id=f"adam-{suffix}",
            bundle_id=f"ai.overx.reviews-{suffix}",
            name=f"Reviews App {suffix}",
            platform="ios",
        )
        session.add(app)
        await session.flush()
        await session.commit()

        return user.id, app.id


async def _seed_world() -> dict[str, Any]:
    """App A has rev-A1/resp-A1; app B has rev-B1/resp-B1.

    ``list_reviews`` (called in every test below) only ever runs for app A —
    app B's ids are only ever reachable via the vulnerable bare-id entry
    points.
    """
    suffix = uuid.uuid4().hex[:8]
    user_a_id, app_a_id = await _seed_tenant(f"a-{suffix}")
    user_b_id, app_b_id = await _seed_tenant(f"b-{suffix}")

    async with async_session_factory() as session:
        app_a = (await session.execute(select(App).where(App.id == app_a_id))).scalar_one()
        app_b = (await session.execute(select(App).where(App.id == app_b_id))).scalar_one()
        asc_app_id_a, asc_app_id_b = app_a.asc_app_id, app_b.asc_app_id

    client = _FakeReviewsASCClient()
    client.seed_review("rev-A1", response_id="resp-A1", territory="USA")
    client.seed_review("rev-B1", response_id="resp-B1", territory="GBR")
    client.seed_app_page(asc_app_id_a, ["rev-A1"])
    client.seed_app_page(asc_app_id_b, ["rev-B1"])

    return {
        "user_a_id": user_a_id,
        "app_a_id": app_a_id,
        "user_b_id": user_b_id,
        "app_b_id": app_b_id,
        "client": client,
    }


@asynccontextmanager
async def _request_session():
    """Mirrors ``app.db.session.get_session``'s commit/rollback boundary.

    REST route functions are called directly here (this repo has no
    ``TestClient`` convention — see ``tests/test_reviews.py``'s module
    docstring), which bypasses the ``Depends(get_session)`` commit-on-success
    wrapper FastAPI provides in production. Reproducing that boundary per
    call means writes made by one "request" (e.g. ``list_reviews``
    populating the ownership map) are durably visible to the next, exactly
    as they would be across two real HTTP requests.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _fake_access_token(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(claims={"user_id": str(user_id), "pat_id": "1"})


def _monkeypatch_rest_client(monkeypatch: pytest.MonkeyPatch, client: _FakeReviewsASCClient) -> None:
    async def _fake_get_client(app, session):
        return client

    monkeypatch.setattr(rest_reviews, "_get_asc_client_for_app", _fake_get_client)


def _monkeypatch_rest_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest_reviews.settings, "ANTHROPIC_API_KEY", "fake-anthropic-key")

    async def _fake_draft_reply(**kwargs):
        return "drafted reply"

    monkeypatch.setattr(rest_reviews, "draft_reply", _fake_draft_reply)
    monkeypatch.setattr(rest_reviews, "build_translator", lambda settings: object())


def _monkeypatch_mcp_client(monkeypatch: pytest.MonkeyPatch, client: _FakeReviewsASCClient) -> None:
    # mcp_reviews does ``from app.mcp.context import resolve_asc_client`` —
    # that binds the name into mcp_reviews's own module globals at import
    # time, so patching app.mcp.context.resolve_asc_client would not affect
    # calls made from inside mcp_reviews. Patch the name where it's looked
    # up (mirrors the _get_asc_client_for_app patching convention in
    # tests/test_subscription_swap_fixes.py).
    async def _fake_resolve_client(app, session):
        return client

    monkeypatch.setattr(mcp_reviews, "resolve_asc_client", _fake_resolve_client)


def _monkeypatch_mcp_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_reviews.settings, "ANTHROPIC_API_KEY", "fake-anthropic-key")

    async def _fake_draft_reply(**kwargs):
        return "drafted reply"

    monkeypatch.setattr(mcp_reviews, "draft_reply", _fake_draft_reply)
    monkeypatch.setattr(mcp_reviews, "build_translator", lambda settings: object())


def _monkeypatch_mcp_auth(monkeypatch: pytest.MonkeyPatch, user_id: int) -> None:
    monkeypatch.setattr(mcp_context, "get_access_token", lambda: _fake_access_token(user_id))


async def _rest_list_reviews_for_app_a(world: dict[str, Any]) -> None:
    async with _request_session() as session:
        # Route params with FastAPI ``Query(...)`` defaults resolve to
        # ``Query`` sentinel objects (not their default value) when the
        # route function is called directly instead of through FastAPI's
        # dependency injection — pass explicit values for all of them.
        await rest_reviews.list_reviews(
            app_id=world["app_a_id"],
            territory=None,
            rating=None,
            has_response=None,
            cursor=None,
            limit=50,
            current_user={"user_id": world["user_a_id"]},
            session=session,
        )


# ----------------------------------------------------------------------
# REST: 6 vulnerable entry points, app B's ids via app A's auth
# ----------------------------------------------------------------------


def test_rest_get_review_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.get_review(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


def test_rest_draft_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        _monkeypatch_rest_ai(monkeypatch)
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.draft_review_reply(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                body=DraftIn(),
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


def test_rest_translate_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        _monkeypatch_rest_ai(monkeypatch)
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.translate_review(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                body=TranslateReviewIn(target_locale="en-GB"),
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


def test_rest_create_reply_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.create_reply(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                body=ReplyIn(body="Thanks!"),
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


def test_rest_update_reply_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.update_reply(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                response_id="resp-B1",
                body=ReplyIn(body="Edited"),
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


def test_rest_delete_reply_cross_app_404(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        await _rest_list_reviews_for_app_a(world)

        async with _request_session() as session:
            await rest_reviews.delete_reply(
                app_id=world["app_a_id"],
                review_id="rev-B1",
                response_id="resp-B1",
                current_user={"user_id": world["user_a_id"]},
                session=session,
            )

    with pytest.raises(HTTPException) as exc_info:
        run_async(go())
    assert exc_info.value.status_code == 404


# ----------------------------------------------------------------------
# MCP: same 6 entry points
# ----------------------------------------------------------------------


def test_mcp_get_review_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.get_review(app_id=world["app_a_id"], review_id="rev-B1")

    with pytest.raises(ToolError):
        run_async(go())


def test_mcp_draft_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        _monkeypatch_mcp_ai(monkeypatch)
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.draft_review_reply(app_id=world["app_a_id"], review_id="rev-B1")

    with pytest.raises(ToolError):
        run_async(go())


def test_mcp_translate_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        _monkeypatch_mcp_ai(monkeypatch)
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.translate_review(
            app_id=world["app_a_id"], review_id="rev-B1", target_locale="en-GB",
        )

    with pytest.raises(ToolError):
        run_async(go())


def test_mcp_create_reply_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.create_reply(
            app_id=world["app_a_id"], review_id="rev-B1", body="Thanks!",
        )

    with pytest.raises(ToolError):
        run_async(go())


def test_mcp_update_reply_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.update_reply(
            app_id=world["app_a_id"], response_id="resp-B1", body="Edited",
        )

    with pytest.raises(ToolError):
        run_async(go())


def test_mcp_delete_reply_cross_app_toolerror(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        await mcp_reviews.delete_reply(app_id=world["app_a_id"], response_id="resp-B1")

    with pytest.raises(ToolError):
        run_async(go())


# ----------------------------------------------------------------------
# Positive regression: same-app access continues to work unchanged
# ----------------------------------------------------------------------


def test_rest_same_app_access_still_works(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_rest_client(monkeypatch, world["client"])
        _monkeypatch_rest_ai(monkeypatch)
        await _rest_list_reviews_for_app_a(world)

        current_user = {"user_id": world["user_a_id"]}

        async with _request_session() as session:
            got = await rest_reviews.get_review(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                current_user=current_user,
                session=session,
            )
        assert got.id == "rev-A1"

        async with _request_session() as session:
            drafted = await rest_reviews.draft_review_reply(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                body=DraftIn(),
                current_user=current_user,
                session=session,
            )
        assert drafted.suggestion == "drafted reply"

        async with _request_session() as session:
            translated = await rest_reviews.translate_review(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                body=TranslateReviewIn(target_locale="en-US"),
                current_user=current_user,
                session=session,
            )
        assert translated.translation

        async with _request_session() as session:
            created = await rest_reviews.create_reply(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                body=ReplyIn(body="Thanks!"),
                current_user=current_user,
                session=session,
            )
        assert created.id == "resp-new-rev-A1"

        async with _request_session() as session:
            updated = await rest_reviews.update_reply(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                response_id="resp-A1",
                body=ReplyIn(body="Edited"),
                current_user=current_user,
                session=session,
            )
        assert updated.id == "resp-A1"

        async with _request_session() as session:
            await rest_reviews.delete_reply(
                app_id=world["app_a_id"],
                review_id="rev-A1",
                response_id="resp-A1",
                current_user=current_user,
                session=session,
            )
        assert "resp-A1" in world["client"].deleted

    run_async(go())


def test_mcp_same_app_access_still_works(monkeypatch: pytest.MonkeyPatch):
    async def go():
        await _ensure_schema()
        world = await _seed_world()
        _monkeypatch_mcp_client(monkeypatch, world["client"])
        _monkeypatch_mcp_auth(monkeypatch, world["user_a_id"])
        _monkeypatch_mcp_ai(monkeypatch)
        await mcp_reviews.list_reviews(app_id=world["app_a_id"])

        got = await mcp_reviews.get_review(app_id=world["app_a_id"], review_id="rev-A1")
        assert got.id == "rev-A1"

        drafted = await mcp_reviews.draft_review_reply(
            app_id=world["app_a_id"], review_id="rev-A1",
        )
        assert drafted.suggestion == "drafted reply"

        translated = await mcp_reviews.translate_review(
            app_id=world["app_a_id"], review_id="rev-A1", target_locale="en-US",
        )
        assert translated.translation

        created = await mcp_reviews.create_reply(
            app_id=world["app_a_id"], review_id="rev-A1", body="Thanks!",
        )
        assert created.id == "resp-new-rev-A1"

        updated = await mcp_reviews.update_reply(
            app_id=world["app_a_id"], response_id="resp-A1", body="Edited",
        )
        assert updated.id == "resp-A1"

        result = await mcp_reviews.delete_reply(
            app_id=world["app_a_id"], response_id="resp-A1",
        )
        assert result == {"detail": "Response deleted"}
        assert "resp-A1" in world["client"].deleted

    run_async(go())
