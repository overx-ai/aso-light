from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastmcp.exceptions import ToolError

from app.mcp.server import mcp
from app.mcp.tools import metadata as metadata_tools
from app.models.app import App
from app.schemas.metadata import BulkApplyResult, BulkPreviewItem


def _app() -> App:
    return App(
        id=41,
        credential_id=1,
        asc_app_id="demo-metadata-diff",
        bundle_id="ai.overx.refresher.demo",
        name="Refresher Metadata Demo",
        platform="ios",
    )


@asynccontextmanager
async def _fake_session_scope():
    yield object()


class _FakeAscContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


async def _fake_asc_client_for_app(app: App, session):
    return _FakeAscContext()


async def _fake_resolve_app(app_id: int, session) -> App:
    app = _app()
    app.id = app_id
    return app


def _patch_mcp_dependencies(monkeypatch, fake_bulk_class) -> None:
    monkeypatch.setattr(metadata_tools, "session_scope", _fake_session_scope)
    monkeypatch.setattr(metadata_tools, "resolve_app", _fake_resolve_app)
    monkeypatch.setattr(metadata_tools, "_get_asc_client_for_app", _fake_asc_client_for_app)
    monkeypatch.setattr(metadata_tools, "ASCMetadataService", lambda client: object())
    monkeypatch.setattr(metadata_tools, "BulkMetadataService", fake_bulk_class)


def test_metadata_bulk_preview_tool_accepts_values_by_locale(monkeypatch):
    seen: dict[str, object] = {}

    class FakeBulkService:
        def __init__(self, asc, session) -> None:
            pass

        async def preview(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkPreviewItem]:
            seen["app_id"] = app.id
            seen["field"] = field
            seen["value"] = value
            seen["target_locales"] = target_locales
            seen["values_by_locale"] = values_by_locale
            seen["create_missing"] = create_missing
            return [
                BulkPreviewItem(locale="es-ES", current_value="Old ES", new_value="Nuevo"),
                BulkPreviewItem(locale="ru", current_value="Old RU", new_value="Новое"),
            ]

    async def go() -> None:
        _patch_mcp_dependencies(monkeypatch, FakeBulkService)
        tool = await mcp.get_tool("metadata_bulk_preview")
        assert tool is not None
        result = await tool.fn(
            app_id=41,
            field="promotional_text",
            target_locales=["es-ES", "ru"],
            value=None,
            values_by_locale={"es-ES": "Nuevo", "ru": "Новое"},
        )

        assert [item.new_value for item in result.items] == ["Nuevo", "Новое"]
        assert seen == {
            "app_id": 41,
            "field": "promotional_text",
            "value": None,
            "target_locales": ["es-ES", "ru"],
            "values_by_locale": {"es-ES": "Nuevo", "ru": "Новое"},
            "create_missing": False,
        }

    asyncio.run(go())


def test_metadata_bulk_apply_tool_accepts_values_by_locale(monkeypatch):
    seen: dict[str, object] = {}

    class FakeBulkService:
        def __init__(self, asc, session) -> None:
            pass

        async def apply(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            force: bool = False,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkApplyResult]:
            seen["app_id"] = app.id
            seen["field"] = field
            seen["value"] = value
            seen["target_locales"] = target_locales
            seen["force"] = force
            seen["values_by_locale"] = values_by_locale
            seen["create_missing"] = create_missing
            return [
                BulkApplyResult(locale="es-ES", status="applied"),
                BulkApplyResult(locale="ru", status="applied"),
            ]

    async def go() -> None:
        _patch_mcp_dependencies(monkeypatch, FakeBulkService)
        tool = await mcp.get_tool("metadata_bulk_apply")
        assert tool is not None
        result = await tool.fn(
            app_id=41,
            field="promotional_text",
            target_locales=["es-ES", "ru"],
            value=None,
            force=True,
            values_by_locale={"es-ES": "Nuevo", "ru": "Новое"},
        )

        assert result.applied == 2
        assert result.skipped == 0
        assert result.failed == 0
        assert seen == {
            "app_id": 41,
            "field": "promotional_text",
            "value": None,
            "target_locales": ["es-ES", "ru"],
            "force": True,
            "values_by_locale": {"es-ES": "Nuevo", "ru": "Новое"},
            "create_missing": False,
        }

    asyncio.run(go())


def test_metadata_bulk_preview_tool_preserves_single_value_mode(monkeypatch):
    seen: dict[str, object] = {}

    class FakeBulkService:
        def __init__(self, asc, session) -> None:
            pass

        async def preview(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkPreviewItem]:
            seen["value"] = value
            seen["values_by_locale"] = values_by_locale
            seen["create_missing"] = create_missing
            return [
                BulkPreviewItem(locale="es-ES", current_value="Old", new_value=value),
                BulkPreviewItem(locale="ru", current_value="Old", new_value=value),
            ]

    async def go() -> None:
        _patch_mcp_dependencies(monkeypatch, FakeBulkService)
        tool = await mcp.get_tool("metadata_bulk_preview")
        assert tool is not None
        result = await tool.fn(
            app_id=41,
            field="promotional_text",
            target_locales=["es-ES", "ru"],
            value="Same text",
        )

        assert [item.new_value for item in result.items] == ["Same text", "Same text"]
        assert seen == {
            "value": "Same text",
            "values_by_locale": None,
            "create_missing": False,
        }

    asyncio.run(go())


def test_metadata_bulk_preview_tool_surfaces_missing_localized_value(monkeypatch):
    class FakeBulkService:
        def __init__(self, asc, session) -> None:
            pass

        async def preview(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkPreviewItem]:
            raise ValueError("Missing proposed value for target locale ru")

    async def go() -> None:
        _patch_mcp_dependencies(monkeypatch, FakeBulkService)
        tool = await mcp.get_tool("metadata_bulk_preview")
        assert tool is not None
        with pytest.raises(ToolError, match="Missing proposed value for target locale ru"):
            await tool.fn(
                app_id=41,
                field="promotional_text",
                target_locales=["es-ES", "ru"],
                value=None,
                values_by_locale={"es-ES": "Nuevo"},
            )

    asyncio.run(go())


def test_metadata_bulk_tools_thread_create_missing(monkeypatch):
    """R1: both bulk tools forward ``create_missing`` to the service.

    The flag is the only door to locale creation over MCP, so a tool that
    silently drops it would leave a 30-locale expansion looking like 30 skips.
    """
    seen: dict[str, object] = {}

    class FakeBulkService:
        def __init__(self, asc, session) -> None:
            pass

        async def preview(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkPreviewItem]:
            seen["preview_create_missing"] = create_missing
            return [
                BulkPreviewItem(
                    locale="de-DE",
                    current_value=None,
                    new_value="Atme besser",
                    action="create",
                ),
            ]

        async def apply(
            self,
            app: App,
            field: str,
            value: str | None,
            target_locales: list[str],
            *,
            force: bool = False,
            values_by_locale: dict[str, str | None] | None = None,
            create_missing: bool = False,
        ) -> list[BulkApplyResult]:
            seen["apply_create_missing"] = create_missing
            return [BulkApplyResult(locale="de-DE", status="applied")]

    async def go() -> None:
        _patch_mcp_dependencies(monkeypatch, FakeBulkService)

        preview_tool = await mcp.get_tool("metadata_bulk_preview")
        preview = await preview_tool.fn(
            app_id=41,
            field="promotional_text",
            target_locales=["de-DE"],
            value="Atme besser",
            create_missing=True,
        )
        assert [item.action for item in preview.items] == ["create"]

        apply_tool = await mcp.get_tool("metadata_bulk_apply")
        applied = await apply_tool.fn(
            app_id=41,
            field="promotional_text",
            target_locales=["de-DE"],
            value="Atme besser",
            create_missing=True,
        )
        assert applied.applied == 1

        assert seen == {
            "preview_create_missing": True,
            "apply_create_missing": True,
        }

    asyncio.run(go())
