"""Tests for localized bulk metadata fan-out planning."""
from __future__ import annotations

import pytest

from app.models.app import App
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.services.metadata.bulk import BulkMetadataService


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


@pytest.mark.asyncio
async def test_values_by_locale_apply_patches_each_locale_value() -> None:
    class FakeAsc:
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

    class FakeBulkService(BulkMetadataService):
        async def _load_existing(  # type: ignore[override]
            self,
            app: App,
            kind: str,
            target_locales: list[str],
        ) -> dict[str, AppMetadataLocalization]:
            return {
                "es-ES": AppMetadataLocalization(
                    app_id=app.id,
                    kind=kind,
                    asc_localization_id="es-loc",
                    asc_parent_id="version",
                    locale="es-ES",
                    promotional_text="Respira mejor",
                ),
                "ru": AppMetadataLocalization(
                    app_id=app.id,
                    kind=kind,
                    asc_localization_id="ru-loc",
                    asc_parent_id="version",
                    locale="ru",
                    promotional_text="Дышите лучше",
                ),
            }

        async def _load_state(self, app: App) -> AppMetadataState | None:
            return AppMetadataState(
                app_id=app.id,
                editable_version_state="PREPARE_FOR_SUBMISSION",
                editable_fields_json=["promotional_text"],
            )

    asc = FakeAsc()
    service = FakeBulkService(asc=asc, session=object())  # type: ignore[arg-type]

    results = await service.apply(
        _app(),
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
        ("es-loc", {"promotionalText": "Respira con calma"}),
        ("ru-loc", {"promotionalText": "Дышите спокойнее"}),
    ]
