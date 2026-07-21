"""Server-side field-editability lock tests (REST + MCP).

Covers the guard wired in front of every metadata write path: when the
per-app ``editable_fields`` list does not contain a field, writing it must be
rejected (REST -> 409, MCP -> ToolError) regardless of how the client crafts
the request. app_info fields (always editable when an app_info exists) and the
single live/promo-only field (``promotional_text``) must still succeed.

These also document the cap path (C3) and exercise the bulk force-skip + I2
sanitization indirectly via ``tests/services/metadata/test_bulk.py``.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError

import app.mcp.tools.metadata as metadata_tools
from app.api.v1.metadata import (
    create_locale as rest_create_locale,
    update_locale as rest_update_locale,
)
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.metadata import (
    AppMetadataLocalization,
    AppMetadataState,
    MetadataTranslationCache,
)
from app.models.user import User
from app.schemas.metadata import LocaleUpsertIn
from app.services.metadata.translate import (
    TranslationQuotaExceededError,
    translate_with_cache,
)
from tests._async_harness import run_async


# ----------------------------------------------------------------------
# Schema + seeding
# ----------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed(
    *,
    editable_fields: list[str],
    editable_version_state: str = "READY_FOR_DISTRIBUTION",
) -> tuple[int, int]:
    """Seed a user-owned app with a state row + one version + app_info locale.

    Returns ``(user_id, app_id)``.
    """
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        owner = User(
            email=f"lock-owner-{suffix}@example.com",
            password_hash="x",
            name="Lock Owner",
        )
        session.add(owner)
        await session.flush()

        cred = ASCCredential(
            user_id=owner.id,
            name="Owner ASC",
            issuer_id=f"issuer-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted="enc",
        )
        session.add(cred)
        await session.flush()

        app = App(
            credential_id=cred.id,
            asc_app_id=f"asc-{suffix}",
            bundle_id=f"ai.overx.lock.{suffix}",
            name="Lock Test App",
            platform="ios",
        )
        session.add(app)
        await session.flush()

        session.add(
            AppMetadataState(
                app_id=app.id,
                editable_version_id="version-1",
                editable_version_state=editable_version_state,
                app_info_id="info-1",
                editable_fields_json=editable_fields,
            )
        )
        session.add(
            AppMetadataLocalization(
                app_id=app.id,
                kind="version",
                asc_localization_id="ver-en-loc",
                asc_parent_id="version-1",
                locale="en-US",
                description="Old description",
                keywords="old,words",
                promotional_text="Old promo",
            )
        )
        session.add(
            AppMetadataLocalization(
                app_id=app.id,
                kind="app_info",
                asc_localization_id="info-en-loc",
                asc_parent_id="info-1",
                locale="en-US",
                name="Old name",
            )
        )
        await session.commit()
        return owner.id, app.id


# ----------------------------------------------------------------------
# ASC stubs (the success path must not hit the network)
# ----------------------------------------------------------------------


class _FakeAscContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


async def _fake_asc_client_for_app(app: App, session) -> _FakeAscContext:
    return _FakeAscContext()


class _FakeAscMetadataService:
    """No-op ASC service: update/create succeed, snapshot re-sync is a no-op."""

    def __init__(self, client) -> None:
        pass

    async def update_app_info_localization(self, loc_id, attrs):
        return {"id": loc_id, "attributes": attrs}

    async def update_version_localization(self, loc_id, attrs, version_state=None):
        return {"id": loc_id, "attributes": attrs}

    async def create_app_info_localization(self, parent_id, locale, attrs):
        return {"id": "new-info-loc", "attributes": {**attrs, "locale": locale}}

    async def create_version_localization(self, parent_id, locale, attrs):
        return {"id": "new-ver-loc", "attributes": {**attrs, "locale": locale}}


class _FakeSnapshotService:
    def __init__(self, asc, session) -> None:
        pass

    async def sync_app(self, app) -> None:
        return None


def _patch_rest_asc(monkeypatch) -> None:
    import app.api.v1.metadata as rest_mod

    monkeypatch.setattr(rest_mod, "_get_asc_client_for_app", _fake_asc_client_for_app)
    monkeypatch.setattr(rest_mod, "ASCMetadataService", _FakeAscMetadataService)
    monkeypatch.setattr(rest_mod, "MetadataSnapshotService", _FakeSnapshotService)


# ----------------------------------------------------------------------
# REST: update_locale
# ----------------------------------------------------------------------


def test_rest_update_locked_version_field_returns_409(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as ei:
                await rest_update_locale(
                    app_id=app_id,
                    kind="version",
                    locale="en-US",
                    body=LocaleUpsertIn(keywords="new,keywords"),
                    current_user={"user_id": user_id},
                    session=session,
                )
            assert ei.value.status_code == 409
            assert "keywords" in str(ei.value.detail)

    run_async(go())


def test_rest_update_locked_description_field_returns_409(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as ei:
                await rest_update_locale(
                    app_id=app_id,
                    kind="version",
                    locale="en-US",
                    body=LocaleUpsertIn(description="New description"),
                    current_user={"user_id": user_id},
                    session=session,
                )
            assert ei.value.status_code == 409

    run_async(go())


def test_rest_update_promotional_text_succeeds_when_editable(monkeypatch) -> None:
    _patch_rest_asc(monkeypatch)

    async def go() -> None:
        await _ensure_schema()
        user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            out = await rest_update_locale(
                app_id=app_id,
                kind="version",
                locale="en-US",
                body=LocaleUpsertIn(promotional_text="Fresh promo"),
                current_user={"user_id": user_id},
                session=session,
            )
            assert out.promotional_text == "Fresh promo"

    run_async(go())


def test_rest_update_app_info_name_succeeds_when_present(monkeypatch) -> None:
    _patch_rest_asc(monkeypatch)

    async def go() -> None:
        await _ensure_schema()
        # app_info fields are always in editable_fields when an app_info exists.
        user_id, app_id = await _seed(
            editable_fields=["name", "subtitle", "privacy_policy_url", "promotional_text"],
        )
        async with async_session_factory() as session:
            out = await rest_update_locale(
                app_id=app_id,
                kind="app_info",
                locale="en-US",
                body=LocaleUpsertIn(name="New name"),
                current_user={"user_id": user_id},
                session=session,
            )
            assert out.name == "New name"

    run_async(go())


# ----------------------------------------------------------------------
# REST: create_locale (version kind has no separate guard — covered by C1)
# ----------------------------------------------------------------------


def test_rest_create_locked_version_field_returns_409(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as ei:
                await rest_create_locale(
                    app_id=app_id,
                    kind="version",
                    locale="fr-FR",
                    body=LocaleUpsertIn(keywords="mots,nouveaux"),
                    current_user={"user_id": user_id},
                    session=session,
                )
            assert ei.value.status_code == 409

    run_async(go())


# ----------------------------------------------------------------------
# MCP: update_locale / create_locale
# ----------------------------------------------------------------------


def _patch_mcp(monkeypatch, app: App, session) -> None:
    @asynccontextmanager
    async def _scope():
        yield session

    async def _resolve(app_id: int, sess) -> App:
        return app

    monkeypatch.setattr(metadata_tools, "session_scope", _scope)
    monkeypatch.setattr(metadata_tools, "resolve_app", _resolve)
    monkeypatch.setattr(
        metadata_tools, "_get_asc_client_for_app", _fake_asc_client_for_app
    )
    monkeypatch.setattr(metadata_tools, "ASCMetadataService", _FakeAscMetadataService)
    monkeypatch.setattr(
        metadata_tools, "MetadataSnapshotService", _FakeSnapshotService
    )


def test_mcp_update_locked_version_field_raises_tool_error(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        _user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            _patch_mcp(monkeypatch, app, session)
            with pytest.raises(ToolError, match="keywords"):
                await metadata_tools.update_locale(
                    app_id=app_id,
                    kind="version",
                    locale="en-US",
                    fields={"keywords": "new,keywords"},
                )

    run_async(go())


def test_mcp_update_promotional_text_succeeds(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        _user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            _patch_mcp(monkeypatch, app, session)
            out = await metadata_tools.update_locale(
                app_id=app_id,
                kind="version",
                locale="en-US",
                fields={"promotional_text": "Fresh promo"},
            )
            assert out.promotional_text == "Fresh promo"

    run_async(go())


def test_mcp_create_locked_version_field_raises_tool_error(monkeypatch) -> None:
    async def go() -> None:
        await _ensure_schema()
        _user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            _patch_mcp(monkeypatch, app, session)
            with pytest.raises(ToolError):
                await metadata_tools.create_locale(
                    app_id=app_id,
                    kind="version",
                    locale="de-DE",
                    fields={"description": "Neue Beschreibung"},
                )

    run_async(go())


# ----------------------------------------------------------------------
# C3: translation cap raises at the cap
# ----------------------------------------------------------------------


class _StubTranslator:
    model_name = "stub:model"

    async def translate(self, text, source_locale, target_locale, field_kind, brand_allowlist=None):  # noqa: D401
        return f"t:{text}"


def test_translation_cap_raises_at_cap() -> None:
    async def go() -> None:
        await _ensure_schema()
        _user_id, app_id = await _seed(editable_fields=["promotional_text"])
        now = datetime.now(timezone.utc)
        # Pre-fill the cache so the app is already AT the cap (5 here).
        async with async_session_factory() as session:
            for i in range(5):
                session.add(
                    MetadataTranslationCache(
                        app_id=app_id,
                        source_locale="en-US",
                        target_locale="de-DE",
                        source_hash=f"hash-{i}",
                        field_kind="promotional_text",
                        translated_text=f"row-{i}",
                        model="stub:model",
                        created_at=now - timedelta(days=1),
                    )
                )
            await session.commit()

        async with async_session_factory() as session:
            with pytest.raises(TranslationQuotaExceededError):
                await translate_with_cache(
                    translator=_StubTranslator(),
                    session=session,
                    app_id=app_id,
                    text="Brand new source text",
                    source_locale="en-US",
                    target_locale="de-DE",
                    field_kind="promotional_text",
                    monthly_cap=5,
                )

    run_async(go())


def test_translation_under_cap_persists_durably() -> None:
    """I3/C3 happy path: a successful translation is committed as produced."""

    async def go() -> None:
        await _ensure_schema()
        _user_id, app_id = await _seed(editable_fields=["promotional_text"])
        async with async_session_factory() as session:
            translated, cached = await translate_with_cache(
                translator=_StubTranslator(),
                session=session,
                app_id=app_id,
                text="Brand new source text",
                source_locale="en-US",
                target_locale="de-DE",
                field_kind="promotional_text",
                monthly_cap=5,
            )
            assert cached is False
            assert translated == "t:Brand new source text"

        # A fresh session sees the committed (durable) cache row.
        async with async_session_factory() as session:
            from sqlalchemy import func, select

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(MetadataTranslationCache)
                    .where(MetadataTranslationCache.app_id == app_id)
                )
            ).scalar_one()
            assert count == 1

    run_async(go())
