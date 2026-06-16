"""Tests for localized bulk metadata fan-out planning."""
from __future__ import annotations

import uuid

import pytest

from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.user import User
from app.services.metadata.bulk import BulkMetadataService
from tests._async_harness import run_async


def _service() -> BulkMetadataService:
    return BulkMetadataService(asc=object(), session=object())  # type: ignore[arg-type]


def _app() -> App:
    return App(
        id=1,
        credential_id=1,
        asc_app_id="123",
        bundle_id="ai.overx.test",
        name="Test",
        platform="ios",
    )


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_app_with_state(
    *,
    editable_fields: list[str],
    editable_version_state: str = "PREPARE_FOR_SUBMISSION",
    version_locales: dict[str, str] | None = None,
) -> int:
    """Create an App + AppMetadataState (+ version localizations) in the DB.

    Returns the new app id. ``editable_fields`` is written verbatim to
    ``AppMetadataState.editable_fields_json`` — the single source of truth the
    bulk guard reads.
    """
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        owner = User(
            email=f"bulk-owner-{suffix}@example.com",
            password_hash="x",
            name="Bulk Owner",
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
            bundle_id=f"ai.overx.bulk.{suffix}",
            name="Bulk Test App",
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
        for locale, value in (version_locales or {}).items():
            session.add(
                AppMetadataLocalization(
                    app_id=app.id,
                    kind="version",
                    asc_localization_id=f"{locale}-loc",
                    asc_parent_id="version-1",
                    locale=locale,
                    promotional_text=value,
                )
            )
        await session.commit()
        return app.id


class _FakeAsc:
    """Records ASC PATCH calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str | None]]] = []

    async def update_version_localization(
        self,
        localization_id: str,
        attrs: dict[str, str | None],
        *,
        version_state: str | None = None,
    ) -> None:
        self.calls.append((localization_id, attrs))

    async def update_app_info_localization(
        self,
        localization_id: str,
        attrs: dict[str, str | None],
    ) -> None:
        self.calls.append((localization_id, attrs))


def test_values_by_locale_preview_uses_each_locale_value() -> None:
    service = _service()
    existing = {
        "es-ES": AppMetadataLocalization(
            app_id=1,
            kind="version",
            asc_localization_id="es-loc",
            asc_parent_id="version",
            locale="es-ES",
            promotional_text="Respira mejor",
        ),
        "ru": AppMetadataLocalization(
            app_id=1,
            kind="version",
            asc_localization_id="ru-loc",
            asc_parent_id="version",
            locale="ru",
            promotional_text="Дышите лучше",
        ),
    }
    state = AppMetadataState(
        app_id=1,
        editable_fields_json=["promotional_text"],
    )

    items = service._build_items(  # noqa: SLF001
        field="promotional_text",
        value=None,
        values_by_locale={
            "es-ES": "Respira con calma",
            "ru": "Дышите спокойнее",
        },
        kind="version",
        target_locales=["es-ES", "ru"],
        existing=existing,
        state=state,
    )

    assert [item.new_value for item in items] == [
        "Respira con calma",
        "Дышите спокойнее",
    ]
    assert all(not item.would_skip for item in items)


def test_values_by_locale_requires_every_target_locale() -> None:
    service = _service()

    with pytest.raises(ValueError, match="Missing proposed value for target locale ru"):
        service._validate_inputs(  # noqa: SLF001
            field="promotional_text",
            value=None,
            target_locales=["es-ES", "ru"],
            values_by_locale={"es-ES": "Respira con calma"},
        )


def test_values_by_locale_overflow_is_per_locale() -> None:
    service = _service()
    existing = {
        "es-ES": AppMetadataLocalization(
            app_id=1,
            kind="app_info",
            asc_localization_id="es-loc",
            asc_parent_id="info",
            locale="es-ES",
            subtitle="Respira",
        ),
        "ru": AppMetadataLocalization(
            app_id=1,
            kind="app_info",
            asc_localization_id="ru-loc",
            asc_parent_id="info",
            locale="ru",
            subtitle="Дыши",
        ),
    }

    items = service._build_items(  # noqa: SLF001
        field="subtitle",
        value=None,
        values_by_locale={
            "es-ES": "Respira calmado",
            "ru": "Д" * 31,
        },
        kind="app_info",
        target_locales=["es-ES", "ru"],
        existing=existing,
        state=AppMetadataState(app_id=1, editable_fields_json=["subtitle"]),
    )

    assert items[0].char_overflow_by == 0
    assert items[0].would_skip is False
    assert items[1].char_overflow_by == 1
    assert items[1].would_skip is True


def test_values_by_locale_apply_patches_each_locale_value() -> None:
    """DB-backed apply: each locale is patched with its own proposed value.

    Rewritten from the old ``object()``-session form: ``apply`` now re-asserts
    field editability against the DB-stored ``editable_fields`` and commits the
    snapshot mirror per applied locale, so it needs a real session + a seeded
    ``AppMetadataState`` row.
    """

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor", "ru": "Дышите лучше"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = (
                await session.get(App, app_id)
            )
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value=None,
                target_locales=["es-ES", "ru"],
                values_by_locale={
                    "es-ES": "Respira con calma",
                    "ru": "Дышите спокойнее",
                },
            )

        assert [result.status for result in results] == ["applied", "applied"]
        assert asc.calls == [
            ("es-ES-loc", {"promotionalText": "Respira con calma"}),
            ("ru-loc", {"promotionalText": "Дышите спокойнее"}),
        ]

    run_async(go())


def test_apply_force_cannot_override_locked_version_field() -> None:
    """I1: ``force=True`` must NOT override a locked version field.

    The version field ``keywords`` is absent from ``editable_fields`` (live /
    promo-only state). Even with ``force=True`` the guard re-asserts editability
    and the locale is hard-skipped — no ASC PATCH is issued.
    """

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            editable_version_state="READY_FOR_DISTRIBUTION",
            version_locales={"es-ES": "palabras viejas"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="keywords",
                value="nuevas,palabras",
                target_locales=["es-ES"],
                force=True,
            )

        assert len(results) == 1
        assert results[0].status == "skipped"
        assert asc.calls == []  # force did not buy a PATCH

    run_async(go())


def test_apply_generic_exception_is_sanitized() -> None:
    """I2: an unexpected exception yields a generic message, not str(exc).

    The raw exception text (which could leak internals) must never reach the
    per-locale ``error`` field — only ``"Unexpected error"`` does.
    """

    class BoomAsc(_FakeAsc):
        async def update_version_localization(
            self,
            localization_id: str,
            attrs: dict[str, str | None],
            *,
            version_state: str | None = None,
        ) -> None:
            raise RuntimeError("secret internal detail leak")

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor"},
        )
        asc = BoomAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="Respira con calma",
                target_locales=["es-ES"],
            )

        assert len(results) == 1
        assert results[0].status == "failed"
        assert results[0].error == "Unexpected error"
        assert "secret internal detail leak" not in (results[0].error or "")

    run_async(go())
