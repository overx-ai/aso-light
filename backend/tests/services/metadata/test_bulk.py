"""Tests for localized bulk metadata fan-out planning."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

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
    app_info_locales: dict[str, str] | None = None,
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
        for locale, value in (app_info_locales or {}).items():
            session.add(
                AppMetadataLocalization(
                    app_id=app.id,
                    kind="app_info",
                    asc_localization_id=f"{locale}-info-loc",
                    asc_parent_id="info-1",
                    locale=locale,
                    subtitle=value,
                )
            )
        await session.commit()
        return app.id


class _FakeAsc:
    """Records ASC PATCH / POST calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str | None]]] = []
        self.creates: list[tuple[str, str, str, dict[str, str | None]]] = []

    async def create_version_localization(
        self,
        version_id: str,
        locale: str,
        attributes: dict[str, str | None],
    ) -> dict:
        self.creates.append(("version", version_id, locale, attributes))
        return {"id": f"new-{locale}-loc", "attributes": dict(attributes)}

    async def create_app_info_localization(
        self,
        app_info_id: str,
        locale: str,
        attributes: dict[str, str | None],
    ) -> dict:
        self.creates.append(("app_info", app_info_id, locale, attributes))
        return {"id": f"new-{locale}-info-loc", "attributes": dict(attributes)}

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


# ------------------------------------------------------------------
# create_missing (spec 012 / R1)
# ------------------------------------------------------------------


def test_preview_create_missing_plans_a_create_for_absent_locale() -> None:
    """R1: a locale absent from the snapshot becomes ``action="create"``.

    ...and, with ``create_missing`` left at its default, keeps returning the
    existing hard skip. Same call, same app, one flag apart — the pair is what
    proves the flag is opt-in.
    """

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor"},
        )
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=_FakeAsc(), session=session)  # type: ignore[arg-type]

            created = await service.preview(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE"],
                create_missing=True,
            )
            skipped = await service.preview(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE"],
            )

        assert len(created) == 1
        assert created[0].action == "create"
        assert created[0].would_skip is False
        assert created[0].current_value is None
        assert created[0].new_value == "Atme besser"
        assert created[0].reason is None

        assert len(skipped) == 1
        assert skipped[0].action == "skip"
        assert skipped[0].would_skip is True
        assert skipped[0].reason == "no existing version localization to update"

    run_async(go())


def test_create_missing_does_not_bypass_char_limits_even_with_force() -> None:
    """R1: an overflowing create is still a hard skip, force or not.

    Two layers guard this and both are asserted, because ``create_missing``
    must never become the way around :func:`validate_field`:

    * the public ``preview``/``apply`` entry points reject an over-limit value
      outright (``ValueError`` from ``_validate_inputs``) — no ASC call at all;
    * the plan builder, reached directly, still marks the item
      ``would_skip=True`` and ``_is_hard_skip`` still returns True, which is the
      exact predicate ``apply`` consults before honouring ``force``.
    """

    too_long = "x" * 171  # promotional_text limit is 170

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]

            with pytest.raises(ValueError, match="over the 170-char limit"):
                await service.preview(
                    app,
                    field="promotional_text",
                    value=too_long,
                    target_locales=["de-DE"],
                    create_missing=True,
                )
            with pytest.raises(ValueError, match="over the 170-char limit"):
                await service.apply(
                    app,
                    field="promotional_text",
                    value=too_long,
                    target_locales=["de-DE"],
                    force=True,
                    create_missing=True,
                )

        assert asc.creates == []
        assert asc.calls == []

    run_async(go())

    # The plan builder itself — the layer ``force`` is checked against.
    items = _service()._build_items(  # noqa: SLF001
        field="promotional_text",
        value=too_long,
        kind="version",
        target_locales=["de-DE"],
        existing={},
        state=AppMetadataState(
            app_id=1, editable_fields_json=["promotional_text"],
        ),
        create_missing=True,
    )
    assert items[0].char_overflow_by == 1
    assert items[0].would_skip is True
    assert items[0].action == "skip"
    assert BulkMetadataService._is_hard_skip(items[0]) is True  # noqa: SLF001


def test_apply_create_missing_creates_and_interleaves_with_updates() -> None:
    """R1: creates and updates run in one pass, each committed per locale."""

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value=None,
                target_locales=["es-ES", "de-DE"],
                values_by_locale={
                    "es-ES": "Respira con calma",
                    "de-DE": "Atme ruhiger",
                },
                create_missing=True,
            )

        assert [r.status for r in results] == ["applied", "applied"]
        # Existing locale patched, missing locale POSTed under the version parent.
        assert asc.calls == [
            ("es-ES-loc", {"promotionalText": "Respira con calma"}),
        ]
        assert asc.creates == [
            ("version", "version-1", "de-DE", {"promotionalText": "Atme ruhiger"}),
        ]

        # The create is mirrored into the snapshot and durably committed.
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(AppMetadataLocalization).where(
                        AppMetadataLocalization.app_id == app_id,
                        AppMetadataLocalization.kind == "version",
                        AppMetadataLocalization.locale == "de-DE",
                    )
                )
            ).scalar_one()
            assert row.promotional_text == "Atme ruhiger"
            assert row.asc_localization_id == "new-de-DE-loc"
            assert row.asc_parent_id == "version-1"

    run_async(go())


def test_apply_create_missing_targets_the_app_info_parent() -> None:
    """R1: ``kind`` picks the ASC parent — app_info creates hit the AppInfo."""

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["subtitle"],
            app_info_locales={"es-ES": "Respira"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="subtitle",
                value="Atme ruhig",
                target_locales=["de-DE"],
                create_missing=True,
            )

        assert [r.status for r in results] == ["applied"]
        assert asc.creates == [
            ("app_info", "info-1", "de-DE", {"subtitle": "Atme ruhig"}),
        ]

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(AppMetadataLocalization).where(
                        AppMetadataLocalization.app_id == app_id,
                        AppMetadataLocalization.kind == "app_info",
                        AppMetadataLocalization.locale == "de-DE",
                    )
                )
            ).scalar_one()
            assert row.subtitle == "Atme ruhig"
            assert row.asc_parent_id == "info-1"

    run_async(go())


def test_apply_without_create_missing_never_creates() -> None:
    """R1: the default is unchanged — a missing locale is skipped, not created."""

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={"es-ES": "Respira mejor"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE"],
                force=True,
            )

        assert [r.status for r in results] == ["skipped"]
        assert results[0].error == "no existing version localization to update"
        assert asc.creates == []
        assert asc.calls == []

    run_async(go())


def test_apply_create_missing_skips_when_no_editable_parent() -> None:
    """R1: no ASC parent to hang the create on → skip, never a blind POST."""

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
        )
        async with async_session_factory() as session:
            state = (
                await session.execute(
                    select(AppMetadataState).where(
                        AppMetadataState.app_id == app_id,
                    )
                )
            ).scalar_one()
            state.editable_version_id = None
            await session.commit()

        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE"],
                create_missing=True,
            )

        assert [r.status for r in results] == ["skipped"]
        assert "no editable version parent available" in (results[0].error or "")
        assert asc.creates == []

    run_async(go())


# ------------------------------------------------------------------
# Review pass: both kinds honour create_missing / the missing-row skip
# ------------------------------------------------------------------


def test_missing_app_info_row_is_a_hard_skip_not_a_phantom_update() -> None:
    """R1: an app_info locale with no row must NOT preview as ``update``.

    The missing-row skip used to be gated on ``kind == "version"``, so an
    absent ``app_info`` locale came back ``would_skip=False, action="update"``
    from ``preview`` and was then dropped by ``apply``'s defensive branch. A
    preview that disagrees with apply is worse than no preview: this is the
    36-locale expansion's headline call.
    """

    async def go() -> None:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["subtitle"],
            app_info_locales={"es-ES": "Respira"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]

            items = await service.preview(
                app,
                field="subtitle",
                value="Atme ruhig",
                target_locales=["de-DE"],
            )
            # force must not turn it into a write either — it is a HARD skip.
            results = await service.apply(
                app,
                field="subtitle",
                value="Atme ruhig",
                target_locales=["de-DE"],
                force=True,
            )

        assert items[0].would_skip is True
        assert items[0].action == "skip"
        assert items[0].reason == "no existing app_info localization to update"
        assert BulkMetadataService._is_hard_skip(items[0]) is True  # noqa: SLF001

        # apply agrees with preview, and nothing was sent to ASC.
        assert [r.status for r in results] == ["skipped"]
        assert results[0].error == "no existing app_info localization to update"
        assert asc.creates == []
        assert asc.calls == []

    run_async(go())


def test_force_cannot_override_a_non_editable_field_skip_in_the_plan() -> None:
    """``_is_hard_skip`` must fail CLOSED for every reason but ``unchanged``.

    It used to enumerate the hard reasons, so a "field not editable" skip was
    classified as soft and ``force`` walked past it in the plan — only the
    separate ``assert_fields_editable`` query stopped the write.
    """
    items = _service()._build_items(  # noqa: SLF001
        field="promotional_text",
        value="new",
        kind="version",
        target_locales=["de-DE"],
        existing={},
        state=AppMetadataState(
            app_id=1,
            editable_version_state="READY_FOR_DISTRIBUTION",
            editable_fields_json=["whats_new"],
        ),
        create_missing=True,
    )
    assert items[0].would_skip is True
    assert items[0].action == "skip"
    assert "not editable" in (items[0].reason or "")
    assert BulkMetadataService._is_hard_skip(items[0]) is True  # noqa: SLF001

    # ...and ``unchanged`` stays the one soft skip ``force`` may re-apply.
    unchanged = _service()._build_items(  # noqa: SLF001
        field="promotional_text",
        value="same",
        kind="version",
        target_locales=["de-DE"],
        existing={
            "de-DE": AppMetadataLocalization(
                app_id=1,
                kind="version",
                asc_localization_id="de-loc",
                asc_parent_id="version-1",
                locale="de-DE",
                promotional_text="same",
            ),
        },
        state=AppMetadataState(app_id=1, editable_fields_json=["promotional_text"]),
    )
    assert unchanged[0].reason == "unchanged"
    assert BulkMetadataService._is_hard_skip(unchanged[0]) is False  # noqa: SLF001


def test_editability_is_asserted_once_per_fanout_not_once_per_locale() -> None:
    """The guard's arguments never vary per locale — one query, not N.

    The whole point of this service is a 31-locale fan-out; re-issuing an
    identical query per locale made the DB cost scale with the fan-out.
    """

    async def go() -> tuple[int, list[str]]:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
            version_locales={f"l{i}-XX": "old" for i in range(6)},
        )
        asc = _FakeAsc()
        calls: list[str] = []
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]

            import app.services.metadata.bulk as bulk_module

            real = bulk_module.assert_fields_editable
            guard_calls = 0

            async def counting_guard(*args, **kwargs):
                nonlocal guard_calls
                guard_calls += 1
                return await real(*args, **kwargs)

            bulk_module.assert_fields_editable = counting_guard  # type: ignore[assignment]
            try:
                results = await service.apply(
                    app,
                    field="promotional_text",
                    value="new",
                    target_locales=[f"l{i}-XX" for i in range(6)],
                )
            finally:
                bulk_module.assert_fields_editable = real  # type: ignore[assignment]

            calls = [r.status for r in results]
        return guard_calls, calls

    guard_calls, statuses = run_async(go())
    assert statuses == ["applied"] * 6
    assert guard_calls == 1


def test_locked_field_still_reports_every_locale_after_the_hoist() -> None:
    """Asserting once must not collapse the per-locale result matrix."""

    async def go() -> list:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["whats_new"],
            version_locales={"de-DE": "old", "es-ES": "old"},
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="new",
                target_locales=["de-DE", "es-ES"],
                force=True,
            )
        assert asc.calls == []
        return results

    results = run_async(go())
    assert [r.locale for r in results] == ["de-DE", "es-ES"]
    assert [r.status for r in results] == ["skipped", "skipped"]
    assert all("not editable" in (r.error or "") for r in results)


def test_create_with_no_id_in_the_asc_response_fails_and_writes_nothing() -> None:
    """An id-less create must not persist an empty ``asc_localization_id``.

    That row's next bulk update would PATCH
    ``/appStoreVersionLocalizations/`` — the collection, with no id at all.
    """

    class _IdLessAsc(_FakeAsc):
        async def create_version_localization(self, version_id, locale, attributes):
            self.creates.append(("version", version_id, locale, attributes))
            return {"attributes": dict(attributes)}  # no "id"

    async def go() -> list:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
        )
        asc = _IdLessAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE"],
                create_missing=True,
            )

        # Nothing mirrored — not even a row with an empty id.
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(AppMetadataLocalization).where(
                        AppMetadataLocalization.app_id == app_id,
                    )
                )
            ).scalars().all()
        assert rows == []
        return results

    results = run_async(go())
    assert [r.status for r in results] == ["failed"]
    assert "no localization id" in (results[0].error or "")


def test_a_locale_repeated_in_the_request_is_created_once() -> None:
    """A duplicated target locale must not POST the same localization twice."""

    async def go() -> list:
        await _ensure_schema()
        app_id = await _seed_app_with_state(
            editable_fields=["promotional_text"],
        )
        asc = _FakeAsc()
        async with async_session_factory() as session:
            app = await session.get(App, app_id)
            assert app is not None
            service = BulkMetadataService(asc=asc, session=session)  # type: ignore[arg-type]
            results = await service.apply(
                app,
                field="promotional_text",
                value="Atme besser",
                target_locales=["de-DE", "de-DE"],
                create_missing=True,
            )
        assert len(asc.creates) == 1
        # The second pass PATCHes the row the first pass created.
        assert asc.calls == [("new-de-DE-loc", {"promotionalText": "Atme besser"})]
        return results

    results = run_async(go())
    assert [r.status for r in results] == ["applied", "applied"]
