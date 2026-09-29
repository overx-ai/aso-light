"""Tests for the MAIN product-page screenshot tools (spec 010).

Drives ``screenshots_list`` / ``screenshots_upload`` / ``screenshots_delete``
end-to-end against a ``FakeASC`` client that models App Store Connect's
``appStoreVersions`` -> ``appStoreVersionLocalizations`` ->
``appScreenshotSets`` -> ``appScreenshots`` tree in memory. No network, no DB:
the MCP context helpers are monkeypatched exactly as ``test_mcp_metadata.py``
does it.

The centre of gravity is **counting**: an interrupted bulk upload leaves some
locales silently short, and Apple only says so at submit time. The list tests
pin the per locale x display type counts, the gap worklist, and the fact that
an Apple-``FAILED`` asset never counts as a shipped screenshot.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from PIL import Image as PILImage

from app.core.config import settings
from app.mcp.server import mcp
from app.mcp.tools import screenshots as screenshot_tools
from app.models.app import App
from app.schemas.screenshots import (
    MAX_SCREENSHOT_BYTES,
    decode_screenshot_payload,
    is_valid_display_type,
)
from app.services.asc import screenshots as shots
from app.services.asc.cpp import ASCCustomProductPageService
from app.services.asc.errors import ASCAPIError
from app.services.asc.experiment import ASCExperimentService
from tests._async_harness import run_async

TEMPLATE_URL = "https://cdn.apple/img/{w}x{h}.{f}"
RENDERED_URL = "https://cdn.apple/img/1290x2796.png"


# ------------------------------------------------------------------
# Fake ASC
# ------------------------------------------------------------------


class FakeASC:
    """In-memory App Store Connect stand-in for the screenshot tree.

    ``sets`` maps ``set_id -> {"display_type", "localization_id", "shots"}``
    where ``shots`` is the ordered list of screenshot ids; ``screenshots`` maps
    ``shot_id -> {"file_name", "state", "errors"}``. Every call is recorded on
    ``calls`` as ``(method, path)``.
    """

    def __init__(
        self,
        *,
        versions: list[dict],
        localizations: dict[str, dict[str, str]],
        sets: dict[str, dict] | None = None,
        screenshots: dict[str, dict] | None = None,
        commit_state: str = "COMPLETE",
        commit_errors: list[str] | None = None,
        cpp_versions: dict[str, list[dict]] | None = None,
        cpp_localizations: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.versions = versions
        self.localizations = localizations
        # cpp id -> its versions; CPP version id -> {locale: localization id}.
        self.cpp_versions = cpp_versions or {}
        self.cpp_localizations = cpp_localizations or {}
        self.sets = sets or {}
        self.screenshots = screenshots or {}
        self.commit_state = commit_state
        self.commit_errors = commit_errors or []
        self.calls: list[tuple[str, str]] = []
        self.uploaded_bytes: list[bytes] = []
        self._seq = 0

    # -- helpers ----------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _shot_resource(self, shot_id: str) -> dict:
        shot = self.screenshots[shot_id]
        return {
            "type": "appScreenshots",
            "id": shot_id,
            "attributes": {
                "fileName": shot["file_name"],
                "sourceFileChecksum": shot.get("checksum"),
                "imageAsset": {
                    "templateUrl": TEMPLATE_URL,
                    "width": 1290,
                    "height": 2796,
                },
                "assetDeliveryState": {
                    "state": shot.get("state", "COMPLETE"),
                    "errors": [
                        {"description": e} for e in shot.get("errors", [])
                    ],
                },
            },
        }

    def set_for(self, localization_id: str, display_type: str) -> dict | None:
        for shot_set in self.sets.values():
            if (
                shot_set["localization_id"] == localization_id
                and shot_set["display_type"] == display_type
            ):
                return shot_set
        return None

    # -- ASCClient surface ------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path))
        parts = path.strip("/").split("/")

        if parts[0] == "apps" and parts[-1] == "appStoreVersions":
            data = self.versions
            states = (params or {}).get("filter[appStoreState]")
            if states:
                allowed = set(states.split(","))
                data = [
                    v
                    for v in data
                    if (v.get("attributes") or {}).get("appStoreState") in allowed
                ]
            return {"data": data}

        if parts[0] == "appStoreVersions" and parts[-1] == (
            "appStoreVersionLocalizations"
        ):
            locales = self.localizations.get(parts[1], {})
            return {
                "data": [
                    {
                        "type": "appStoreVersionLocalizations",
                        "id": loc_id,
                        "attributes": {"locale": locale},
                    }
                    for locale, loc_id in locales.items()
                ]
            }

        if parts[0] == "appCustomProductPages" and parts[-1] == (
            "appCustomProductPageVersions"
        ):
            return {"data": self.cpp_versions.get(parts[1], [])}

        if parts[0] == "appCustomProductPageVersions" and parts[-1] == (
            "appCustomProductPageLocalizations"
        ):
            return {
                "data": [
                    {
                        "type": "appCustomProductPageLocalizations",
                        "id": loc_id,
                        "attributes": {"locale": locale},
                    }
                    for locale, loc_id in self.cpp_localizations.get(parts[1], {}).items()
                ]
            }

        if parts[0] == "appCustomProductPageLocalizations" and len(parts) == 2:
            assert (params or {}).get("include") == "appCustomProductPageVersion"
            owner = next(
                version
                for versions in self.cpp_versions.values()
                for version in versions
                if parts[1] in self.cpp_localizations.get(version["id"], {}).values()
            )
            return {
                "data": {"type": "appCustomProductPageLocalizations", "id": parts[1]},
                "included": [owner],
            }

        if parts[0] in {
            "appStoreVersionLocalizations",
            "appCustomProductPageLocalizations",
            "appStoreVersionExperimentTreatmentLocalizations",
        } and parts[-1] == "appScreenshotSets":
            data: list[dict] = []
            included: list[dict] = []
            for set_id, shot_set in self.sets.items():
                if shot_set["localization_id"] != parts[1]:
                    continue
                data.append({
                    "id": set_id,
                    "attributes": {
                        "screenshotDisplayType": shot_set["display_type"],
                    },
                    "relationships": {
                        "appScreenshots": {
                            "data": [
                                {"type": "appScreenshots", "id": shot_id}
                                for shot_id in shot_set["shots"]
                            ],
                        },
                    },
                })
                included.extend(
                    self._shot_resource(shot_id) for shot_id in shot_set["shots"]
                )
            return {"data": data, "included": included}

        if parts[0] == "appScreenshotSets" and parts[-1] == "appScreenshots":
            return {
                "data": [
                    self._shot_resource(shot_id)
                    for shot_id in self.sets[parts[1]]["shots"]
                ]
            }

        if parts[0] == "appScreenshots" and len(parts) == 2:
            return {"data": self._shot_resource(parts[1])}

        raise AssertionError(f"unexpected GET {path}")

    async def _get_all_pages(
        self, path: str, params: dict | None = None
    ) -> list[dict]:
        response = await self._get(path, params)
        return response.get("data", [])

    async def _post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path))
        body = (json or {}).get("data", {})
        attrs = body.get("attributes", {})

        if path == "/appScreenshotSets":
            set_id = self._next_id("set")
            relationship = body["relationships"]
            # The relationship key names the parent type — this is what makes
            # the shared helper parent-agnostic.
            parent = next(iter(relationship.values()))["data"]
            self.sets[set_id] = {
                "display_type": attrs["screenshotDisplayType"],
                "localization_id": parent["id"],
                "shots": [],
            }
            return {"data": {"id": set_id}}

        if path == "/appCustomProductPageLocalizations":
            version_id = body["relationships"]["appCustomProductPageVersion"]["data"]["id"]
            loc_id = self._next_id("cpp-loc")
            self.cpp_localizations.setdefault(version_id, {})[attrs["locale"]] = loc_id
            return {"data": {"id": loc_id, "attributes": {"locale": attrs["locale"]}}}

        if path == "/appScreenshots":
            shot_id = self._next_id("shot")
            set_id = body["relationships"]["appScreenshotSet"]["data"]["id"]
            self.screenshots[shot_id] = {
                "file_name": attrs["fileName"],
                "state": "AWAITING_UPLOAD",
                "errors": [],
            }
            self.sets[set_id]["shots"].append(shot_id)
            return {
                "data": {
                    "id": shot_id,
                    "attributes": {
                        "uploadOperations": [
                            {
                                "url": "https://upload.apple/put",
                                "offset": 0,
                                "length": attrs["fileSize"],
                                "requestHeaders": [
                                    {"name": "Content-Type", "value": "image/png"},
                                ],
                            },
                        ],
                    },
                }
            }

        raise AssertionError(f"unexpected POST {path}")

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("PATCH", path))
        parts = path.strip("/").split("/")

        if parts[0] == "appScreenshots" and len(parts) == 2:
            self.screenshots[parts[1]]["state"] = self.commit_state
            self.screenshots[parts[1]]["errors"] = list(self.commit_errors)
            self.screenshots[parts[1]]["checksum"] = (
                (json or {}).get("data", {}).get("attributes", {})
                .get("sourceFileChecksum")
            )
            return {"data": self._shot_resource(parts[1])}

        if parts[0] == "appScreenshotSets" and parts[-1] == "appScreenshots":
            self.sets[parts[1]]["shots"] = [
                ref["id"] for ref in (json or {}).get("data", [])
            ]
            return {}

        raise AssertionError(f"unexpected PATCH {path}")

    async def _delete(self, path: str) -> None:
        self.calls.append(("DELETE", path))
        parts = path.strip("/").split("/")
        if parts[0] == "appScreenshots":
            self.screenshots.pop(parts[1], None)
            for shot_set in self.sets.values():
                if parts[1] in shot_set["shots"]:
                    shot_set["shots"].remove(parts[1])
            return
        if parts[0] == "appScreenshotSets":
            self.sets.pop(parts[1], None)
            return
        raise AssertionError(f"unexpected DELETE {path}")

    async def _put_binary(
        self, url: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self.calls.append(("PUT", url))
        self.uploaded_bytes.append(data)


# ------------------------------------------------------------------
# MCP wiring
# ------------------------------------------------------------------


def _app() -> App:
    return App(
        id=7,
        credential_id=1,
        asc_app_id="asc-777",
        bundle_id="ai.overx.refresher",
        name="Refresher",
        platform="ios",
    )


@asynccontextmanager
async def _fake_session_scope():
    yield object()


async def _fake_resolve_app(app_id: int, session) -> App:
    app = _app()
    app.id = app_id
    return app


def _patch_tools(monkeypatch, client: FakeASC) -> None:
    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def _fake_asc_client_for_app(app: App, session):
        return _Ctx()

    monkeypatch.setattr(screenshot_tools, "session_scope", _fake_session_scope)
    monkeypatch.setattr(screenshot_tools, "resolve_app", _fake_resolve_app)
    monkeypatch.setattr(
        screenshot_tools, "_get_asc_client_for_app", _fake_asc_client_for_app
    )
    monkeypatch.setattr(screenshot_tools, "_VERIFY_DELAY_SECONDS", 0)


def _version(
    version_id: str = "ver-1",
    state: str = "PREPARE_FOR_SUBMISSION",
    version_string: str = "1.5.0",
    created: str = "2026-08-01T00:00:00Z",
) -> dict:
    return {
        "type": "appStoreVersions",
        "id": version_id,
        "attributes": {
            "appStoreState": state,
            "versionString": version_string,
            "createdDate": created,
        },
    }


def _client_with_two_locales(**overrides) -> FakeASC:
    """en-US fully populated (3 iPhone shots), de-DE short (1)."""
    screenshots = {
        f"shot-en-{i}": {"file_name": f"en-{i}.png", "state": "COMPLETE"}
        for i in range(3)
    }
    screenshots["shot-de-0"] = {"file_name": "de-0.png", "state": "COMPLETE"}
    sets = {
        "set-en-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "loc-en",
            "shots": ["shot-en-0", "shot-en-1", "shot-en-2"],
        },
        "set-de-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "loc-de",
            "shots": ["shot-de-0"],
        },
    }
    kwargs = {
        "versions": [_version()],
        "localizations": {"ver-1": {"en-US": "loc-en", "de-DE": "loc-de"}},
        "sets": sets,
        "screenshots": screenshots,
    }
    kwargs.update(overrides)
    return FakeASC(**kwargs)


async def _tool(name: str):
    tool = await mcp.get_tool(name)
    assert tool is not None
    return tool


# ==================================================================
# screenshots_list — counting + completeness
# ==================================================================


def test_list_counts_per_locale_and_flags_the_short_one(monkeypatch):
    """The finished locales set the target; the interrupted one becomes a gap."""
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        return await tool.fn(app_id=7)

    result = run_async(go())

    assert result.version_id == "ver-1"
    assert result.version_state == "PREPARE_FOR_SUBMISSION"
    assert result.version_string == "1.5.0"
    assert result.display_types == ["APP_IPHONE_67"]
    assert result.expected_by_display_type == {"APP_IPHONE_67": 3}
    assert result.total_screenshots == 4

    counts = {
        row.locale: row.display_types[0].count for row in result.locales
    }
    assert counts == {"de-DE": 1, "en-US": 3}

    assert result.complete is False
    assert [(g.locale, g.display_type, g.count, g.missing) for g in result.gaps] == [
        ("de-DE", "APP_IPHONE_67", 1, 2),
    ]


def test_list_reports_an_entirely_missing_display_type_as_zero(monkeypatch):
    """A locale with no set at all is the worst kind of short — still a gap."""
    client = _client_with_two_locales()
    client.sets["set-en-ipad"] = {
        "display_type": "APP_IPAD_PRO_3GEN_129",
        "localization_id": "loc-en",
        "shots": ["shot-en-0"],
    }
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        return await tool.fn(app_id=7)

    result = run_async(go())

    de = next(row for row in result.locales if row.locale == "de-DE")
    ipad = next(
        s for s in de.display_types if s.display_type == "APP_IPAD_PRO_3GEN_129"
    )
    assert ipad.count == 0
    assert ipad.set_id is None
    assert ipad.complete is False
    assert ("de-DE", "APP_IPAD_PRO_3GEN_129") in [
        (g.locale, g.display_type) for g in result.gaps
    ]


def test_list_does_not_count_an_apple_failed_asset(monkeypatch):
    """A FAILED asset occupies a slot but is not a shipped screenshot."""
    client = _client_with_two_locales()
    client.screenshots["shot-en-2"]["state"] = "FAILED"
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        return await tool.fn(app_id=7, expected_count=3)

    result = run_async(go())

    en = next(row for row in result.locales if row.locale == "en-US")
    assert en.display_types[0].count == 2
    assert en.display_types[0].failed == ["shot-en-2"]
    assert en.complete is False


def test_list_expected_count_pins_the_target(monkeypatch):
    """With a pinned target even the 'best' locale can be short."""
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        return await tool.fn(app_id=7, expected_count=6)

    result = run_async(go())
    assert result.expected_by_display_type == {"APP_IPHONE_67": 6}
    assert {g.locale: g.missing for g in result.gaps} == {"en-US": 3, "de-DE": 5}


def test_list_omits_assets_by_default_and_includes_them_on_request(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        lean = await tool.fn(app_id=7)
        full = await tool.fn(app_id=7, include_assets=True)
        return lean, full

    lean, full = run_async(go())

    assert lean.locales[0].display_types[0].screenshots == []
    de = next(row for row in full.locales if row.locale == "de-DE")
    shot = de.display_types[0].screenshots[0]
    assert shot.id == "shot-de-0"
    assert shot.file_name == "de-0.png"
    assert shot.display_type == "APP_IPHONE_67"
    assert shot.source_url == RENDERED_URL
    assert shot.state == "COMPLETE"


def test_list_can_be_narrowed_to_locales_and_display_types(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        return await tool.fn(
            app_id=7, locales=["de-DE"], display_types=["APP_IPHONE_65"],
        )

    result = run_async(go())
    assert [row.locale for row in result.locales] == ["de-DE"]
    # A requested-but-unconfigured family is reported, not silently dropped.
    assert result.display_types == ["APP_IPHONE_65"]
    assert result.gaps[0].count == 0


def test_list_rejects_an_unknown_locale(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        await tool.fn(app_id=7, locales=["fr-FR"])

    with pytest.raises(ToolError, match="fr-FR"):
        run_async(go())


def test_list_rejects_an_unknown_display_type(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        await tool.fn(app_id=7, display_types=["APP_IPHONE_99"])

    with pytest.raises(ToolError, match="Unknown display_type"):
        run_async(go())


# ==================================================================
# Editable-version resolution
# ==================================================================


def test_live_version_fails_with_a_message_naming_the_state(monkeypatch):
    """A live version has no editable sets — say so, don't leak a 409."""
    client = _client_with_two_locales(
        versions=[_version(state="READY_FOR_SALE")],
    )
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        await tool.fn(app_id=7)

    with pytest.raises(ToolError, match="READY_FOR_SALE"):
        run_async(go())


def test_upload_against_a_live_version_names_the_state_too(monkeypatch):
    client = _client_with_two_locales(
        versions=[_version(state="READY_FOR_SALE")],
    )
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="de-1.png",
        )

    with pytest.raises(ToolError, match="READY_FOR_SALE"):
        run_async(go())


def test_no_version_at_all_is_reported_clearly(monkeypatch):
    client = _client_with_two_locales(versions=[])
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_list")
        await tool.fn(app_id=7)

    with pytest.raises(ToolError, match="no App Store version"):
        run_async(go())


def test_resolve_editable_version_prefers_the_newest_editable_one():
    client = FakeASC(
        versions=[
            _version("ver-old", created="2026-01-01T00:00:00Z"),
            _version("ver-new", created="2026-08-01T00:00:00Z"),
            _version("ver-live", state="READY_FOR_SALE", created="2026-09-01T00:00:00Z"),
        ],
        localizations={},
    )
    service = shots.ASCVersionScreenshotService(client)  # type: ignore[arg-type]
    version = run_async(service.resolve_editable_version("asc-777"))
    assert version.id == "ver-new"


# ==================================================================
# screenshots_upload
# ==================================================================


def _upload(client: FakeASC, **kwargs):
    async def go():
        tool = await _tool("screenshots_upload")
        return await tool.fn(**kwargs)

    return run_async(go())


def test_upload_appends_and_reports_the_read_back_state(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    result = _upload(
        client,
        app_id=7,
        locale="de-DE",
        display_type="APP_IPHONE_67",
        file_base64="cG5nLWJ5dGVz",
        file_name="de-1.png",
    )

    assert result.locale == "de-DE"
    assert result.set_id == "set-de-67"
    assert result.position == 1
    assert result.replaced_screenshot_id is None
    assert result.verified is True
    assert result.warning is None
    assert result.screenshot.display_type == "APP_IPHONE_67"
    assert result.screenshot.source_url == RENDERED_URL
    assert client.uploaded_bytes == [b"png-bytes"]
    assert len(client.sets["set-de-67"]["shots"]) == 2
    # The read-back is a real GET on the committed asset, not the PATCH echo.
    assert ("GET", f"/appScreenshots/{result.screenshot.id}") in client.calls


def test_upload_creates_the_set_when_the_display_type_is_new(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    result = _upload(
        client,
        app_id=7,
        locale="de-DE",
        display_type="APP_IPAD_PRO_3GEN_129",
        file_base64="cG5n",
        file_name="de-ipad-0.png",
    )

    created = client.sets[result.set_id]
    assert created["display_type"] == "APP_IPAD_PRO_3GEN_129"
    assert created["localization_id"] == "loc-de"
    assert result.position == 0


def test_uploading_the_same_position_twice_leaves_exactly_one(monkeypatch):
    """Acceptance criterion: a resumed bulk run must not double a locale."""
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    common = dict(
        app_id=7,
        locale="de-DE",
        display_type="APP_IPHONE_67",
        file_base64="cG5n",
        position=0,
    )
    first = _upload(client, **common, file_name="de-0.png")
    second = _upload(client, **common, file_name="de-0.png")

    assert first.replaced_screenshot_id == "shot-de-0"
    assert second.replaced_screenshot_id == first.screenshot.id
    assert client.sets["set-de-67"]["shots"] == [second.screenshot.id]
    assert len(client.sets["set-de-67"]["shots"]) == 1


def test_upload_without_position_replaces_the_same_file_name_in_place(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    result = _upload(
        client,
        app_id=7,
        locale="en-US",
        display_type="APP_IPHONE_67",
        file_base64="cG5n",
        file_name="en-1.png",
    )

    assert result.position == 1
    assert result.replaced_screenshot_id == "shot-en-1"
    # Replaced in place: still 3 assets, and the new one kept slot 1.
    assert client.sets["set-en-67"]["shots"] == [
        "shot-en-0",
        result.screenshot.id,
        "shot-en-2",
    ]


def test_upload_without_position_appends_a_new_file_name(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    result = _upload(
        client,
        app_id=7,
        locale="en-US",
        display_type="APP_IPHONE_67",
        file_base64="cG5n",
        file_name="en-3.png",
    )

    assert result.position == 3
    assert result.replaced_screenshot_id is None
    assert client.sets["set-en-67"]["shots"][-1] == result.screenshot.id


def test_upload_rejects_a_position_past_the_end(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="de-9.png",
            position=5,
        )

    with pytest.raises(ToolError, match="past the end"):
        run_async(go())


def test_upload_reports_unverified_when_apple_is_still_processing(monkeypatch):
    """A 2xx is not verification — report only what the read-back confirms."""
    client = _client_with_two_locales(commit_state="UPLOAD_COMPLETE")
    _patch_tools(monkeypatch, client)

    result = _upload(
        client,
        app_id=7,
        locale="de-DE",
        display_type="APP_IPHONE_67",
        file_base64="cG5n",
        file_name="de-1.png",
    )

    assert result.verified is False
    assert result.warning is not None
    assert "UPLOAD_COMPLETE" in result.warning
    assert result.screenshot.state == "UPLOAD_COMPLETE"


def test_upload_raises_when_apple_marks_the_asset_failed(monkeypatch):
    client = _client_with_two_locales(
        commit_state="FAILED", commit_errors=["Wrong dimensions"],
    )
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="de-1.png",
        )

    with pytest.raises(ToolError, match="Wrong dimensions"):
        run_async(go())


def test_upload_rejects_a_locale_absent_from_the_version(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="pt-BR",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="pt-0.png",
        )

    with pytest.raises(ToolError, match="metadata_create_locale"):
        run_async(go())


def test_upload_rejects_bad_base64_and_empty_payloads(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def bad():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="not base64!!",
            file_name="de-1.png",
        )

    async def empty():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="",
            file_name="de-1.png",
        )

    with pytest.raises(ToolError, match="Invalid base64"):
        run_async(bad())
    with pytest.raises(ToolError, match="empty"):
        run_async(empty())
    # Nothing reached ASC.
    assert not any(method == "POST" for method, _ in client.calls)


# ==================================================================
# screenshots_delete
# ==================================================================


def test_delete_by_position_keeps_the_rest(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_delete")
        return await tool.fn(
            app_id=7,
            locale="en-US",
            display_type="APP_IPHONE_67",
            position=1,
        )

    result = run_async(go())
    assert result.deleted_screenshot_ids == ["shot-en-1"]
    assert result.deleted_set is False
    assert result.remaining == 2
    assert client.sets["set-en-67"]["shots"] == ["shot-en-0", "shot-en-2"]


def test_deleting_the_last_screenshot_prunes_the_set(monkeypatch):
    """No orphan (configured but empty) set is left — when pruning is asked for.

    Pruning is opt-in: the default flipped to False so that deleting the last
    screenshot cannot silently destroy the set configuration too.
    """
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_delete")
        return await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            screenshot_id="shot-de-0",
            prune_empty_set=True,
        )

    result = run_async(go())
    assert result.deleted_screenshot_ids == ["shot-de-0"]
    assert result.deleted_set is True
    assert result.remaining == 0
    assert "set-de-67" not in client.sets


def test_delete_can_keep_the_empty_set_when_asked(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_delete")
        return await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            position=0,
            prune_empty_set=False,
        )

    result = run_async(go())
    assert result.deleted_set is False
    assert client.sets["set-de-67"]["shots"] == []


def test_delete_all_clears_the_whole_display_type(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_delete")
        return await tool.fn(
            app_id=7,
            locale="en-US",
            display_type="APP_IPHONE_67",
            delete_all=True,
            prune_empty_set=True,
        )

    result = run_async(go())
    assert result.deleted_screenshot_ids == ["shot-en-0", "shot-en-1", "shot-en-2"]
    assert result.deleted_set is True
    assert "set-en-67" not in client.sets


def test_delete_requires_exactly_one_selector(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def none_given():
        tool = await _tool("screenshots_delete")
        await tool.fn(app_id=7, locale="de-DE", display_type="APP_IPHONE_67")

    async def two_given():
        tool = await _tool("screenshots_delete")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            position=0,
            delete_all=True,
        )

    with pytest.raises(ToolError, match="exactly one"):
        run_async(none_given())
    with pytest.raises(ToolError, match="exactly one"):
        run_async(two_given())


def test_delete_reports_a_missing_set_and_an_unknown_id(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def missing_set():
        tool = await _tool("screenshots_delete")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPAD_PRO_3GEN_129",
            position=0,
        )

    async def unknown_id():
        tool = await _tool("screenshots_delete")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            screenshot_id="shot-nope",
        )

    with pytest.raises(ToolError, match="nothing to delete"):
        run_async(missing_set())
    with pytest.raises(ToolError, match="shot-nope"):
        run_async(unknown_id())


# ==================================================================
# Contract: the shared helpers are unchanged for CPP + PPO
# ==================================================================


def _cpp_style_client() -> FakeASC:
    return FakeASC(
        versions=[],
        localizations={},
        sets={
            "set-cpp": {
                "display_type": "APP_IPHONE_67",
                "localization_id": "cpp-loc",
                "shots": ["shot-cpp"],
            },
        },
        screenshots={"shot-cpp": {"file_name": "hero.png", "state": "COMPLETE"}},
    )


def test_cpp_screenshot_shape_has_no_new_keys():
    """CPP's shaped dicts must stay exactly what ``Screenshot(**shot)`` expects."""
    client = _cpp_style_client()
    service = ASCCustomProductPageService(client)  # type: ignore[arg-type]
    sets = run_async(service.get_cpp_screenshots("cpp-loc"))
    assert set(sets[0]["screenshots"][0]) == {
        "id",
        "file_name",
        "display_type",
        "source_url",
    }
    assert sets[0]["screenshots"][0]["source_url"] == RENDERED_URL


def test_experiment_screenshot_shape_has_no_new_keys():
    client = FakeASC(
        versions=[],
        localizations={},
        sets={
            "set-ppo": {
                "display_type": "APP_IPHONE_67",
                "localization_id": "ppo-loc",
                "shots": ["shot-ppo"],
            },
        },
        screenshots={"shot-ppo": {"file_name": "a.png", "state": "COMPLETE"}},
    )
    service = ASCExperimentService(client)  # type: ignore[arg-type]
    sets = run_async(service.get_treatment_screenshots("ppo-loc"))
    assert set(sets[0]["screenshots"][0]) == {
        "id",
        "file_name",
        "display_type",
        "source_url",
    }


def test_delivery_state_is_opt_in_on_the_shared_fetch():
    """The default request must not even ask ASC for ``assetDeliveryState``."""
    seen: list[dict] = []

    class _Recorder(FakeASC):
        async def _get(self, path, params=None):
            seen.append(params or {})
            return await super()._get(path, params)

    client = _Recorder(
        versions=[],
        localizations={},
        sets=_cpp_style_client().sets,
        screenshots=_cpp_style_client().screenshots,
    )
    run_async(
        shots.fetch_screenshot_sets(
            client,  # type: ignore[arg-type]
            "/appCustomProductPageLocalizations/cpp-loc/appScreenshotSets",
        )
    )
    assert seen[0]["fields[appScreenshots]"] == "fileName,imageAsset"

    run_async(
        shots.fetch_screenshot_sets(
            client,  # type: ignore[arg-type]
            "/appCustomProductPageLocalizations/cpp-loc/appScreenshotSets",
            include_delivery_state=True,
        )
    )
    assert seen[1]["fields[appScreenshots]"] == (
        "fileName,imageAsset,assetDeliveryState"
    )


def test_cpp_upload_still_targets_its_own_parent_relationship():
    """``find_or_create_screenshot_set`` stays parent-agnostic."""
    client = FakeASC(versions=[], localizations={})
    service = ASCCustomProductPageService(client)  # type: ignore[arg-type]
    run_async(
        service.upload_screenshot_to_cpp(
            "cpp-loc", "APP_IPHONE_67", b"bytes", "hero.png",
        )
    )
    created = next(iter(client.sets.values()))
    assert created["localization_id"] == "cpp-loc"
    assert created["display_type"] == "APP_IPHONE_67"
    assert client.uploaded_bytes == [b"bytes"]


# ==================================================================
# Upload payload bounds — shared by main listing, CPP and PPO
# ==================================================================


def test_upload_rejects_an_oversized_payload(monkeypatch):
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    oversized = base64.b64encode(b"x" * (MAX_SCREENSHOT_BYTES + 1)).decode()

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64=oversized,
            file_name="huge.png",
        )

    with pytest.raises(ToolError, match="cap is"):
        run_async(go())
    assert not any(method == "POST" for method, _ in client.calls)


def test_every_upload_surface_shares_one_bounded_decode():
    """CPP + PPO used to decode unbounded; the cap must not be main-listing-only.

    ``cpp_upload_screenshot`` had no size check at all, so a client could make
    the server buffer an arbitrarily large decoded payload before ASC saw a
    byte. All three tools now funnel through ``decode_screenshot_payload``.
    """
    assert decode_screenshot_payload("cG5n") == b"png"

    with pytest.raises(ValueError, match="Invalid base64"):
        decode_screenshot_payload("not base64!!")
    with pytest.raises(ValueError, match="empty"):
        decode_screenshot_payload("")

    # Over the cap, both by encoded length (cheap path) and by decoded length.
    with pytest.raises(ValueError, match="cap"):
        decode_screenshot_payload("A" * (MAX_SCREENSHOT_BYTES * 2))
    with pytest.raises(ValueError, match="cap is"):
        decode_screenshot_payload(
            base64.b64encode(b"x" * (MAX_SCREENSHOT_BYTES + 1)).decode(),
        )
    # ...and the cheap path never materializes the decoded copy.
    assert decode_screenshot_payload(base64.b64encode(b"x" * 32).decode()) == b"x" * 32


def test_cpp_upload_is_bounded_and_validates_the_display_type(monkeypatch):
    from app.mcp.tools import cpp as cpp_tools

    client = FakeASC(versions=[], localizations={})

    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc) -> None:
            return None

    async def _fake_asc_client_for_app(app, session):
        return _Ctx()

    monkeypatch.setattr(cpp_tools, "session_scope", _fake_session_scope)
    monkeypatch.setattr(cpp_tools, "resolve_app", _fake_resolve_app)
    monkeypatch.setattr(cpp_tools, "_get_asc_client_for_app", _fake_asc_client_for_app)

    async def oversized():
        tool = await _tool("cpp_upload_screenshot")
        await tool.fn(
            app_id=7,
            localization_id="cpp-loc",
            display_type="APP_IPHONE_67",
            file_base64=base64.b64encode(b"x" * (MAX_SCREENSHOT_BYTES + 1)).decode(),
            file_name="huge.png",
        )

    async def bad_display_type():
        tool = await _tool("cpp_upload_screenshot")
        await tool.fn(
            app_id=7,
            localization_id="cpp-loc",
            display_type="APP_IPHONE_99",
            file_base64="cG5n",
            file_name="hero.png",
        )

    with pytest.raises(ToolError, match="cap is"):
        run_async(oversized())
    with pytest.raises(ToolError, match="Unknown display_type"):
        run_async(bad_display_type())
    # Neither one reached App Store Connect.
    assert client.calls == []


def test_upload_refuses_an_id_less_reservation(monkeypatch):
    """An id-less reserve must not become ``PATCH /appScreenshots/``.

    The commit step built its URL straight from ``reservation["data"]["id"]``,
    so an id-less (or shape-shifted) reservation either KeyError'd into a 500
    or PATCHed the *collection*. Shared by CPP and PPO, which run the same
    reserve -> PUT -> commit helper.
    """
    client = _client_with_two_locales()

    async def _idless_post(path, json=None):
        response = await FakeASC._post(client, path, json)
        if path == "/appScreenshots":
            return {"data": {"id": "", "attributes": response["data"]["attributes"]}}
        return response

    monkeypatch.setattr(client, "_post", _idless_post)
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="de-1.png",
        )

    with pytest.raises(ToolError, match="no screenshot id"):
        run_async(go())
    # Neither the PUT nor the commit PATCH was attempted.
    assert client.uploaded_bytes == []
    assert not any(method == "PATCH" for method, _ in client.calls)


def test_upload_refuses_an_id_less_commit_response(monkeypatch):
    """No id on the commit echo means the read-back would GET the collection."""
    client = _client_with_two_locales()

    async def _idless_patch(path, json=None):
        response = await FakeASC._patch(client, path, json)
        if path.startswith("/appScreenshots/"):
            return {"data": {}}
        return response

    monkeypatch.setattr(client, "_patch", _idless_patch)
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_upload")
        await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            file_base64="cG5n",
            file_name="de-1.png",
        )

    with pytest.raises(ToolError, match="no screenshot id"):
        run_async(go())
    assert not any(
        method == "GET" and path == "/appScreenshots/" for method, path in client.calls
    )


# ==================================================================
# screenshots_sync — a studio export directory as the version's screenshots
# (spec 013)
# ==================================================================

IPHONE_69 = (1320, 2868)
IPAD_13 = (2064, 2752)
WRITES = {"POST", "PATCH", "DELETE", "PUT"}


def _png(path: Path, size: tuple[int, int] = IPHONE_69, shade: int = 0) -> str:
    """Write a solid PNG and return its MD5 — the checksum ASC keeps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, (shade % 256, 40, 80)).save(path, format="PNG")
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324


def _export(out: Path, slides: dict[str, int], size=IPHONE_69) -> dict[str, list[str]]:
    """``<out>/<locale>/NN.png`` per locale; returns each locale's MD5s in order."""
    return {
        locale: [
            _png(out / locale / f"{index:02d}.png", size, shade=index)
            for index in range(1, count + 1)
        ]
        for locale, count in slides.items()
    }


def _sync_client(md5s: dict[str, list[str]]) -> FakeASC:
    """en-US already matches the export; de-DE is stale and one longer; fr-FR
    has no set. en-US also carries a Watch and an iPad set the sync must not
    touch."""
    screenshots = {
        f"shot-en-{i}": {"file_name": f"{i + 1:02d}.png", "checksum": md5}
        for i, md5 in enumerate(md5s["en-US"])
    }
    screenshots.update({
        f"shot-de-{i}": {"file_name": f"old-{i}.png", "checksum": f"stale-{i}"}
        for i in range(3)
    })
    screenshots["shot-watch"] = {"file_name": "watch.png", "checksum": "w"}
    screenshots["shot-ipad"] = {"file_name": "ipad.png", "checksum": "i"}
    sets = {
        "set-en-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "loc-en",
            "shots": [f"shot-en-{i}" for i in range(len(md5s["en-US"]))],
        },
        "set-de-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "loc-de",
            "shots": ["shot-de-0", "shot-de-1", "shot-de-2"],
        },
        "set-en-watch": {
            "display_type": "APP_WATCH_ULTRA",
            "localization_id": "loc-en",
            "shots": ["shot-watch"],
        },
        "set-en-ipad": {
            "display_type": "APP_IPAD_PRO_3GEN_129",
            "localization_id": "loc-en",
            "shots": ["shot-ipad"],
        },
    }
    return FakeASC(
        versions=[_version()],
        localizations={
            "ver-1": {"en-US": "loc-en", "de-DE": "loc-de", "fr-FR": "loc-fr"},
        },
        sets=sets,
        screenshots=screenshots,
    )


@pytest.fixture
def export(tmp_path, monkeypatch):
    """An allowlisted export root holding en-US, de-DE and fr-FR, 2 slides each."""
    root = tmp_path / "allowed"
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(root)])
    out = root / "out"
    md5s = _export(out, {"en-US": 2, "de-DE": 2, "fr-FR": 2})
    return out, md5s


def _sync(monkeypatch, client: FakeASC, out: Path, **kwargs):
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_sync")
        return await tool.fn(app_id=7, dir=str(out), **kwargs)

    return run_async(go())


def _writes(client: FakeASC) -> list[tuple[str, str]]:
    return [call for call in client.calls if call[0] in WRITES]


def _actions(result) -> dict[tuple[str, str | None], str]:
    return {(row.locale, row.display_type): row.action for row in result.rows}


def _errors(result) -> list[str]:
    return [row.error or "" for row in result.rows if row.action == "error"]


def test_display_type_comes_from_one_size_table():
    assert shots.display_type_for_size(1320, 2868) == "APP_IPHONE_67"
    assert shots.display_type_for_size(1290, 2796) == "APP_IPHONE_67"
    assert shots.display_type_for_size(2868, 1320) == "APP_IPHONE_67"
    assert shots.display_type_for_size(2064, 2752) == "APP_IPAD_PRO_3GEN_129"
    assert shots.display_type_for_size(2732, 2048) == "APP_IPAD_PRO_3GEN_129"
    assert shots.display_type_for_size(1000, 1000) is None
    assert all(
        is_valid_display_type(display_type)
        for display_type in shots.DISPLAY_TYPE_BY_SIZE.values()
    )


def test_sync_dry_run_plans_skip_replace_upload_and_error(export, monkeypatch):
    out, md5s = export
    _png(out / "nl" / "01.png")
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out)

    assert result.applied is False
    assert _actions(result) == {
        ("de-DE", "APP_IPHONE_67"): "replace",
        ("en-US", "APP_IPHONE_67"): "skip",
        ("fr-FR", "APP_IPHONE_67"): "upload",
        ("nl", None): "error",
    }
    de = next(row for row in result.rows if row.locale == "de-DE")
    assert (de.files, de.existing, de.uploads, de.deletes) == (2, 3, 2, 3)
    fr = next(row for row in result.rows if row.locale == "fr-FR")
    assert (fr.files, fr.existing, fr.uploads, fr.deletes) == (2, 0, 2, 0)
    assert result.inventory is None
    assert _writes(client) == []


def test_sync_dry_run_is_the_plan_apply_then_executes(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)

    def plan(result):
        return [
            (r.locale, r.display_type, r.action, r.files, r.existing, r.uploads, r.deletes)
            for r in result.rows
        ]

    dry = _sync(monkeypatch, client, out)
    assert _writes(client) == []
    applied = _sync(monkeypatch, client, out, apply=True)

    assert applied.applied is True
    assert plan(dry) == plan(applied)


def test_sync_apply_replaces_each_type_as_a_unit_in_directory_order(
    export, monkeypatch
):
    out, md5s = export
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    def checksums(localization_id: str) -> list[str]:
        shot_set = client.set_for(localization_id, "APP_IPHONE_67")
        assert shot_set is not None
        return [client.screenshots[s]["checksum"] for s in shot_set["shots"]]

    assert checksums("loc-de") == md5s["de-DE"]
    assert checksums("loc-fr") == md5s["fr-FR"]
    assert client.sets["set-en-67"]["shots"] == ["shot-en-0", "shot-en-1"]
    for stale in ("shot-de-0", "shot-de-1", "shot-de-2"):
        assert stale not in client.screenshots

    # The verdict is the read-back inventory, not the upload responses.
    assert {row.locale: row.count for row in result.rows} == {
        "de-DE": 2, "en-US": 2, "fr-FR": 2,
    }
    assert result.inventory is not None
    assert result.inventory.gaps == []


def test_sync_rerun_is_all_skip_and_writes_nothing(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)
    _sync(monkeypatch, client, out, apply=True)
    client.calls.clear()

    again = _sync(monkeypatch, client, out, apply=True)

    assert set(_actions(again).values()) == {"skip"}
    assert _writes(client) == []


def test_sync_leaves_every_other_display_type_byte_identical(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)
    def other_sets():
        return copy.deepcopy({
            set_id: (client.sets[set_id], [
                client.screenshots[s] for s in client.sets[set_id]["shots"]
            ])
            for set_id in ("set-en-watch", "set-en-ipad")
        })

    before = other_sets()

    result = _sync(monkeypatch, client, out, apply=True)

    assert other_sets() == before
    touched = " ".join(path for _method, path in client.calls)
    assert "set-en-watch" not in touched
    assert "set-en-ipad" not in touched
    assert "shot-watch" not in touched
    assert "shot-ipad" not in touched
    assert set(result.untouched.display_types) == {
        "APP_WATCH_ULTRA", "APP_IPAD_PRO_3GEN_129",
    }


def test_sync_unknown_pixel_size_is_an_error_and_nothing_is_written(
    export, monkeypatch
):
    out, md5s = export
    _png(out / "en-US" / "03.png", size=(1000, 1000))
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert result.applied is False
    assert any("03.png" in e and "1000x1000" in e for e in _errors(result))
    assert _writes(client) == []


def test_sync_unknown_locale_directory_is_an_error_not_a_skip(export, monkeypatch):
    out, md5s = export
    _png(out / "nl" / "01.png")
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert result.applied is False
    assert ("nl", None) in _actions(result)
    assert any("nl" in e for e in _errors(result))
    assert _writes(client) == []


def test_sync_requested_locale_without_a_directory_is_an_error(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, locales=["en-US", "ja"])

    assert _actions(result)[("ja", None)] == "error"
    assert ("de-DE", "APP_IPHONE_67") not in _actions(result)


def test_sync_skips_variants_dot_dirs_and_top_level_files(export, monkeypatch):
    out, md5s = export
    _png(out / "variants" / "cpp-a" / "en-US" / "01.png")
    _png(out / ".history" / "01.png")
    (out / "findings.json").write_text("{}")
    (out / "qa.json").write_text("{}")
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert _errors(result) == []
    assert result.applied is True
    assert set(result.untouched.entries) >= {
        "variants", ".history", "findings.json", "qa.json",
    }


def test_sync_lists_version_locales_without_a_directory_as_untouched(
    tmp_path, monkeypatch
):
    root = tmp_path / "allowed"
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(root)])
    out = root / "out"
    md5s = _export(out, {"en-US": 2})
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out)

    assert result.untouched.locales == ["de-DE", "fr-FR"]


def test_sync_refuses_a_dir_outside_the_allowlist(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(root)])
    outside = tmp_path / "outside" / "out"
    md5s = _export(outside, {"en-US": 2})
    (root / "link").symlink_to(outside, target_is_directory=True)
    client = _sync_client({"en-US": md5s["en-US"]})

    with pytest.raises(ToolError, match="SCREENSHOT_SYNC_ROOTS"):
        _sync(monkeypatch, client, outside)
    with pytest.raises(ToolError, match="SCREENSHOT_SYNC_ROOTS"):
        _sync(monkeypatch, client, root / "link")
    assert client.calls == []


def test_sync_refuses_a_symlink_inside_the_export_that_leaves_the_allowlist(
    export, monkeypatch
):
    out, md5s = export
    escape = out.parent.parent / "outside"
    _png(escape / "evil.png")
    (out / "en-US" / "03.png").symlink_to(escape / "evil.png")
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert result.applied is False
    assert any("03.png" in e for e in _errors(result))
    assert _writes(client) == []


def test_sync_more_than_ten_files_of_one_type_is_an_error_before_any_write(
    export, monkeypatch
):
    out, md5s = export
    for index in range(3, 12):
        _png(out / "de-DE" / f"{index:02d}.png", shade=index)
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert _actions(result)[("de-DE", "APP_IPHONE_67")] == "error"
    assert result.applied is False
    assert _writes(client) == []


def test_sync_two_display_types_in_one_locale_need_a_filter(export, monkeypatch):
    out, md5s = export
    _png(out / "en-US" / "ipad.png", size=IPAD_13)
    client = _sync_client(md5s)

    mixed = _sync(monkeypatch, client, out)
    assert any("ipad.png" in e for e in _errors(mixed))

    narrowed = _sync(monkeypatch, client, out, display_types=["APP_IPHONE_67"])
    assert _errors(narrowed) == []
    assert _actions(narrowed)[("en-US", "APP_IPHONE_67")] == "skip"


def test_sync_refuses_files_that_are_not_screenshots(export, monkeypatch):
    out, md5s = export
    locale_dir = out / "en-US"
    (locale_dir / "03.png").write_text("not an image")
    PILImage.new("RGB", IPHONE_69).save(locale_dir / "04.png", format="GIF")
    (locale_dir / "05").mkdir()
    (locale_dir / "notes.txt").write_text("x")
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    errors = " ".join(_errors(result))
    assert "en-US/03.png: not a readable image" in errors
    assert "en-US/04.png: a GIF image" in errors
    assert "en-US/05: not a file" in errors
    assert "en-US/notes.txt: not a PNG or JPEG file" in errors
    assert result.applied is False
    assert _writes(client) == []


def test_sync_refuses_a_file_over_the_size_cap(export, monkeypatch):
    out, md5s = export
    monkeypatch.setattr(shots, "MAX_SCREENSHOT_BYTES", 100)
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert any("over the 100-byte cap" in e for e in _errors(result))
    assert result.applied is False
    assert _writes(client) == []


def test_sync_refuses_a_locale_directory_symlinked_out_of_the_allowlist(
    export, monkeypatch
):
    out, md5s = export
    escape = out.parent.parent / "outside" / "fr-FR"
    _export(escape.parent, {"fr-FR": 2})
    for png in (out / "fr-FR").iterdir():
        png.unlink()
    (out / "fr-FR").rmdir()
    (out / "fr-FR").symlink_to(escape, target_is_directory=True)
    client = _sync_client(md5s)

    result = _sync(monkeypatch, client, out, apply=True)

    assert _actions(result)[("fr-FR", None)] == "error"
    assert result.applied is False
    assert _writes(client) == []


def test_sync_apply_uploads_only_the_bytes_the_plan_checked(export):
    out, md5s = export
    path = out / "en-US" / "01.png"
    planned = shots.ExportFile(path, md5s["en-US"][0])
    original = path.read_bytes()
    assert shots.read_planned_bytes(planned) == original

    _png(path, shade=99)
    with pytest.raises(shots.ExportChangedError, match="changed after"):
        shots.read_planned_bytes(planned)

    # Same bytes as planned, but now reached through a link out of the roots.
    escape = out.parent.parent / "outside" / "01.png"
    escape.parent.mkdir(parents=True)
    escape.write_bytes(original)
    path.unlink()
    path.symlink_to(escape)
    with pytest.raises(shots.ExportChangedError, match="SCREENSHOT_SYNC_ROOTS"):
        shots.read_planned_bytes(planned)


def test_sync_stops_when_a_file_changes_between_plan_and_apply(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)
    plan_sync = shots.ASCVersionScreenshotService.plan_sync

    async def plan_then_edit(self, steps):
        planned = await plan_sync(self, steps)
        _png(out / "de-DE" / "01.png", shade=99)
        return planned

    monkeypatch.setattr(shots.ASCVersionScreenshotService, "plan_sync", plan_then_edit)

    with pytest.raises(ToolError, match="changed after it was planned"):
        _sync(monkeypatch, client, out, apply=True)
    # de-DE is the first step: its stale slot is not deleted for a refused file.
    assert _writes(client) == []


def test_sync_replaces_a_failed_asset_even_when_its_checksum_matches(tmp_path):
    export_file = shots.ExportFile(tmp_path / "01.png", "abc")
    step = shots.SyncStep(
        "en-US",
        "APP_IPHONE_67",
        [export_file],
        localization_id="loc-en",
        existing=[{"id": "s1", "checksum": "abc", "state": shots.ASSET_STATE_FAILED}],
    )

    assert (step.action, step.uploads, step.deletes) == ("replace", 1, 1)


# ==================================================================
# cpp_screenshots_sync / cpp_screenshots_delete — the same sync, a CPP source
# (spec 015)
# ==================================================================

CPP_ID = "cpp-1"
CPP_VERSION = "cver-1"


def _cpp_version(state: str = "PREPARE_FOR_SUBMISSION") -> dict:
    return {
        "type": "appCustomProductPageVersions",
        "id": CPP_VERSION,
        "attributes": {"state": state, "version": "1"},
    }


def _cpp_sync_client(
    md5s: dict[str, list[str]], state: str = "PREPARE_FOR_SUBMISSION"
) -> FakeASC:
    """The app ships en-US, de-DE and fr-FR. The CPP has en-US (matches the
    export, plus a Watch set) and de-DE (stale, one longer); it lacks fr-FR."""
    screenshots = {
        f"cshot-en-{i}": {"file_name": f"{i + 1:02d}.png", "checksum": md5}
        for i, md5 in enumerate(md5s["en-US"])
    }
    screenshots.update({
        f"cshot-de-{i}": {"file_name": f"old-{i}.png", "checksum": f"stale-{i}"}
        for i in range(3)
    })
    screenshots["cshot-watch"] = {"file_name": "watch.png", "checksum": "w"}
    sets = {
        "cset-en-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "cloc-en",
            "shots": [f"cshot-en-{i}" for i in range(len(md5s["en-US"]))],
        },
        "cset-de-67": {
            "display_type": "APP_IPHONE_67",
            "localization_id": "cloc-de",
            "shots": ["cshot-de-0", "cshot-de-1", "cshot-de-2"],
        },
        "cset-en-watch": {
            "display_type": "APP_WATCH_ULTRA",
            "localization_id": "cloc-en",
            "shots": ["cshot-watch"],
        },
    }
    return FakeASC(
        versions=[_version(state="READY_FOR_SALE")],
        localizations={
            "ver-1": {"en-US": "loc-en", "de-DE": "loc-de", "fr-FR": "loc-fr"},
        },
        sets=sets,
        screenshots=screenshots,
        cpp_versions={CPP_ID: [_cpp_version(state)]},
        cpp_localizations={CPP_VERSION: {"en-US": "cloc-en", "de-DE": "cloc-de"}},
    )


def _patch_cpp_tools(monkeypatch, client: FakeASC) -> None:
    from app.mcp.tools import cpp as cpp_tools

    _patch_tools(monkeypatch, client)

    async def _fake_asc_client_for_app(app: App, session):
        return await screenshot_tools._get_asc_client_for_app(app, session)

    monkeypatch.setattr(cpp_tools, "session_scope", _fake_session_scope)
    monkeypatch.setattr(cpp_tools, "resolve_app", _fake_resolve_app)
    monkeypatch.setattr(cpp_tools, "_get_asc_client_for_app", _fake_asc_client_for_app)


def _cpp_call(monkeypatch, client: FakeASC, name: str, **kwargs):
    _patch_cpp_tools(monkeypatch, client)

    async def go():
        tool = await _tool(name)
        return await tool.fn(app_id=7, **kwargs)

    return run_async(go())


def _cpp_sync(monkeypatch, client: FakeASC, out: Path, **kwargs):
    return _cpp_call(
        monkeypatch, client, "cpp_screenshots_sync", cpp_id=CPP_ID, dir=str(out), **kwargs
    )


def _cpp_checksums(client: FakeASC, localization_id: str) -> list[str]:
    shot_set = client.set_for(localization_id, "APP_IPHONE_67")
    assert shot_set is not None
    return [client.screenshots[s]["checksum"] for s in shot_set["shots"]]


def test_cpp_sync_dry_run_plans_create_localization_replace_and_skip(
    export, monkeypatch
):
    out, md5s = export
    client = _cpp_sync_client(md5s)

    result = _cpp_sync(monkeypatch, client, out)

    assert result.applied is False
    assert result.version_id == CPP_VERSION
    assert _actions(result) == {
        ("de-DE", "APP_IPHONE_67"): "replace",
        ("en-US", "APP_IPHONE_67"): "skip",
        ("fr-FR", "APP_IPHONE_67"): "create_localization",
    }
    fr = next(row for row in result.rows if row.locale == "fr-FR")
    assert (fr.files, fr.existing, fr.uploads, fr.deletes) == (2, 0, 2, 0)
    de = next(row for row in result.rows if row.locale == "de-DE")
    assert (de.files, de.existing, de.uploads, de.deletes) == (2, 3, 2, 3)
    assert result.inventory is None
    assert _writes(client) == []


def test_cpp_sync_apply_replaces_as_a_unit_and_creates_the_missing_localization(
    export, monkeypatch
):
    out, md5s = export
    client = _cpp_sync_client(md5s)

    result = _cpp_sync(monkeypatch, client, out, apply=True)

    assert result.applied is True
    assert _cpp_checksums(client, "cloc-de") == md5s["de-DE"]
    for stale in ("cshot-de-0", "cshot-de-1", "cshot-de-2"):
        assert stale not in client.screenshots
    fr_loc = client.cpp_localizations[CPP_VERSION]["fr-FR"]
    assert _cpp_checksums(client, fr_loc) == md5s["fr-FR"]
    assert client.sets["cset-en-67"]["shots"] == ["cshot-en-0", "cshot-en-1"]
    # The main listing's own localizations were never written to.
    assert all(
        shot_set["localization_id"] not in {"loc-en", "loc-de", "loc-fr"}
        for shot_set in client.sets.values()
    )

    assert {row.locale: row.count for row in result.rows} == {
        "de-DE": 2, "en-US": 2, "fr-FR": 2,
    }
    assert result.inventory is not None
    assert result.inventory.gaps == []


def test_cpp_sync_creates_a_locale_once_for_all_its_display_types(
    export, monkeypatch
):
    out, md5s = export
    ipad = _png(out / "fr-FR" / "10.png", IPAD_13)
    client = _cpp_sync_client(md5s)
    list_path = f"/appCustomProductPageVersions/{CPP_VERSION}/appCustomProductPageLocalizations"

    result = _cpp_sync(
        monkeypatch,
        client,
        out,
        display_types=["APP_IPHONE_67", "APP_IPAD_PRO_3GEN_129"],
        apply=True,
    )

    fr_loc = client.cpp_localizations[CPP_VERSION]["fr-FR"]
    assert _cpp_checksums(client, fr_loc) == md5s["fr-FR"]
    fr_ipad = client.set_for(fr_loc, "APP_IPAD_PRO_3GEN_129")
    assert [client.screenshots[s]["checksum"] for s in fr_ipad["shots"]] == [ipad]
    # bind reads the list once, the first fr-FR step once; the second reuses it.
    assert client.calls.count(("GET", list_path)) == 2
    assert client.calls.count(("POST", "/appCustomProductPageLocalizations")) == 1
    counts = {(row.locale, row.display_type): row.count for row in result.rows}
    assert counts[("fr-FR", "APP_IPAD_PRO_3GEN_129")] == 1


def test_cpp_sync_rerun_is_all_skip_with_zero_writes(export, monkeypatch):
    out, md5s = export
    client = _cpp_sync_client(md5s)
    _cpp_sync(monkeypatch, client, out, apply=True)
    client.calls.clear()

    again = _cpp_sync(monkeypatch, client, out, apply=True)

    assert set(_actions(again).values()) == {"skip"}
    assert _writes(client) == []


def test_cpp_sync_never_touches_another_display_type(export, monkeypatch):
    out, md5s = export
    client = _cpp_sync_client(md5s)
    before = copy.deepcopy(
        (client.sets["cset-en-watch"], client.screenshots["cshot-watch"])
    )

    result = _cpp_sync(monkeypatch, client, out, apply=True)

    assert (client.sets["cset-en-watch"], client.screenshots["cshot-watch"]) == before
    touched = " ".join(path for _method, path in client.calls)
    assert "cset-en-watch" not in touched
    assert "cshot-watch" not in touched
    assert result.untouched.display_types == ["APP_WATCH_ULTRA"]


IPHONE, IPAD = "APP_IPHONE_67", "APP_IPAD_PRO_3GEN_129"


def _main_ships(client: FakeASC, *display_types: str) -> FakeASC:
    """The app's main listing is editable and holds a set of each type in
    every locale, as the app ships those device families."""
    client.versions = [_version()]
    for localization_id in ("loc-en", "loc-de", "loc-fr"):
        for display_type in display_types:
            client.sets[f"main-{localization_id}-{display_type}"] = {
                "display_type": display_type,
                "localization_id": localization_id,
                "shots": [],
            }
    return client


def test_cpp_sync_names_the_device_family_the_page_would_lack(export, monkeypatch):
    out, md5s = export
    client = _main_ships(_cpp_sync_client(md5s), IPHONE, IPAD)

    dry = _cpp_sync(monkeypatch, client, out)
    applied = _cpp_sync(monkeypatch, client, out, apply=True)

    lacking = {"en-US": [IPAD], "de-DE": [IPAD], "fr-FR": [IPAD]}
    assert dry.missing_families == lacking
    assert applied.applied
    assert applied.missing_families == lacking


def test_cpp_sync_reports_no_missing_family_once_it_is_synced_too(export, monkeypatch):
    out, md5s = export
    ipad_out = out.parent / "ipad"
    _export(ipad_out, {"en-US": 2, "de-DE": 2, "fr-FR": 2}, size=IPAD_13)
    client = _main_ships(_cpp_sync_client(md5s), IPHONE, IPAD)

    _cpp_sync(monkeypatch, client, out, apply=True)
    done = _cpp_sync(monkeypatch, client, ipad_out, apply=True)

    assert done.missing_families == {}
    assert _cpp_sync(monkeypatch, client, out).missing_families == {}


def test_cpp_sync_reports_nothing_when_the_main_listing_is_not_editable(
    export, monkeypatch
):
    out, md5s = export
    client = _cpp_sync_client(md5s)

    assert _cpp_sync(monkeypatch, client, out).missing_families == {}


def test_main_listing_sync_never_reports_missing_families(export, monkeypatch):
    out, md5s = export

    result = _sync(monkeypatch, _sync_client(md5s), out)

    assert result.missing_families == {}


def test_cpp_sync_keeps_013s_directory_rules(export, monkeypatch):
    out, md5s = export
    _png(out / "variants" / "cpp-a" / "en-US" / "01.png")
    _png(out / ".history" / "01.png")
    (out / "findings.json").write_text("{}")
    client = _cpp_sync_client(md5s)

    clean = _cpp_sync(monkeypatch, client, out)
    assert _errors(clean) == []
    assert set(clean.untouched.entries) >= {"variants", ".history", "findings.json"}

    _png(out / "en-US" / "03.png", size=(1000, 1000))
    _png(out / "nl" / "01.png")
    client.calls.clear()

    refused = _cpp_sync(monkeypatch, client, out, apply=True)

    assert refused.applied is False
    errors = " ".join(_errors(refused))
    assert "03.png" in errors and "1000x1000" in errors
    assert _actions(refused)[("nl", None)] == "error"
    assert "nl-NL, not nl" in errors
    assert _writes(client) == []


def test_cpp_sync_refuses_a_dir_outside_the_allowlist_before_any_asc_call(
    tmp_path, monkeypatch
):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(root)])
    outside = tmp_path / "outside" / "out"
    md5s = _export(outside, {"en-US": 2})
    client = _cpp_sync_client({"en-US": md5s["en-US"]})

    with pytest.raises(ToolError, match="SCREENSHOT_SYNC_ROOTS"):
        _cpp_sync(monkeypatch, client, outside)
    assert client.calls == []


def test_every_cpp_write_tool_refuses_a_version_in_review(export, monkeypatch):
    out, md5s = export
    client = _cpp_sync_client(md5s, state="IN_REVIEW")
    calls = {
        "cpp_screenshots_sync": {"cpp_id": CPP_ID, "dir": str(out), "apply": True},
        "cpp_screenshots_delete": {
            "cpp_id": CPP_ID,
            "locale": "en-US",
            "display_type": "APP_IPHONE_67",
            "position": 0,
        },
        "cpp_ensure_localization": {"cpp_id": CPP_ID, "locale": "fr-FR"},
        "cpp_upload_screenshot": {
            "localization_id": "cloc-en",
            "display_type": "APP_IPHONE_67",
            "file_base64": "cG5n",
            "file_name": "hero.png",
        },
    }

    for name, kwargs in calls.items():
        with pytest.raises(ToolError, match="IN_REVIEW"):
            _cpp_call(monkeypatch, client, name, **kwargs)
    assert _writes(client) == []


def test_cpp_screenshots_delete_removes_one_slot_and_keeps_the_rest(monkeypatch):
    client = _cpp_sync_client({"en-US": ["a", "b"]})

    result = _cpp_call(
        monkeypatch,
        client,
        "cpp_screenshots_delete",
        cpp_id=CPP_ID,
        locale="de-DE",
        display_type="APP_IPHONE_67",
        position=1,
    )

    assert result.deleted_screenshot_ids == ["cshot-de-1"]
    assert result.remaining == 2
    assert client.sets["cset-de-67"]["shots"] == ["cshot-de-0", "cshot-de-2"]


def test_cpp_screenshots_delete_needs_a_fresh_consent_token(monkeypatch):
    import mcp.types as mt
    from fastmcp.server.middleware import MiddlewareContext

    from app.mcp import consent

    consent.reset_consent_state()
    monkeypatch.setattr(consent, "get_access_token", lambda: None)
    reached: list = []

    async def call_next(context):
        reached.append(context)
        return "EXECUTED"

    def call(arguments: dict):
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="cpp_screenshots_delete", arguments=arguments
            )
        )
        return run_async(consent.ConsentGate().on_call_tool(context, call_next))

    arguments = {
        "app_id": 7,
        "cpp_id": CPP_ID,
        "locale": "de-DE",
        "display_type": "APP_IPHONE_67",
        "delete_all": True,
    }
    assert "cpp_screenshots_delete" in consent.DESTRUCTIVE
    with pytest.raises(ToolError, match="CONSENT REQUIRED"):
        call(arguments)
    with pytest.raises(ToolError, match="unknown or already used"):
        call({**arguments, consent.CONFIRM_ARG: "not-a-token"})
    assert reached == []
    consent.reset_consent_state()


# ==================================================================
# Sync apply progress + one apply per page (bug 006)
# ==================================================================


class _RecordingContext:
    """Stands in for the FastMCP ``Context`` a client with a progressToken gets."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        self.reports.append((progress, total, message))


def _rows_reported(ctx: _RecordingContext) -> list[tuple[float, float | None]]:
    """Distinct (done, total) states, in order — a heartbeat repeats the last one."""
    states: list[tuple[float, float | None]] = []
    for progress, total, _message in ctx.reports:
        if not states or states[-1] != (progress, total):
            states.append((progress, total))
    return states


def test_sync_apply_reports_progress_once_per_row(export, monkeypatch):
    out, md5s = export
    ctx = _RecordingContext()

    result = _sync(monkeypatch, _sync_client(md5s), out, apply=True, ctx=ctx)

    assert result.applied is True
    assert len(result.rows) == 3
    assert _rows_reported(ctx) == [(0, 3), (1, 3), (2, 3), (3, 3)]
    messages = " ".join(message or "" for _p, _t, message in ctx.reports)
    for locale in ("de-DE", "en-US", "fr-FR"):
        assert locale in messages


def test_cpp_sync_apply_reports_progress_once_per_row(export, monkeypatch):
    out, md5s = export
    ctx = _RecordingContext()

    result = _cpp_sync(monkeypatch, _cpp_sync_client(md5s), out, apply=True, ctx=ctx)

    assert result.applied is True
    assert _rows_reported(ctx) == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_sync_heartbeat_reports_while_one_row_is_slow(export, monkeypatch):
    from app.mcp import progress

    out, md5s = export
    ctx = _RecordingContext()
    monkeypatch.setattr(progress, "PROGRESS_HEARTBEAT_SECONDS", 0.01)
    apply_step = shots.LocalizationScreenshotService.apply_sync_step

    async def slow_step(self, step, version_id):
        await asyncio.sleep(0.1)
        await apply_step(self, step, version_id)

    monkeypatch.setattr(
        shots.LocalizationScreenshotService, "apply_sync_step", slow_step
    )

    result = _sync(monkeypatch, _sync_client(md5s), out, apply=True, ctx=ctx)

    assert _rows_reported(ctx) == [(0, 3), (1, 3), (2, 3), (3, 3)]
    # The beat's timer always falls due before a slow row's, so every written
    # row repeats the state before it at least once however slow the host.
    states = Counter((progress, total) for progress, total, _m in ctx.reports)
    written = [i for i, row in enumerate(result.rows) if row.action != "skip"]
    assert written
    assert all(states[(i, 3)] >= 2 for i in written)


def test_sync_dry_run_and_no_context_report_nothing(export, monkeypatch):
    out, md5s = export
    ctx = _RecordingContext()

    dry = _sync(monkeypatch, _sync_client(md5s), out, ctx=ctx)
    applied = _sync(monkeypatch, _sync_client(md5s), out, apply=True)

    assert dry.applied is False and ctx.reports == []
    assert applied.applied is True


def test_sync_progress_reaches_a_real_mcp_client(export, monkeypatch):
    from fastmcp import Client

    out, md5s = export
    _patch_tools(monkeypatch, _sync_client(md5s))
    seen: list[tuple[float, float | None]] = []

    async def on_progress(progress, total, message) -> None:
        seen.append((progress, total))

    async def go():
        async with Client(mcp, progress_handler=on_progress) as client:
            return await client.call_tool(
                "screenshots_sync", {"app_id": 7, "dir": str(out), "apply": True}
            )

    result = run_async(go())

    assert result.structured_content["applied"] is True
    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_sync_context_is_not_a_tool_argument():
    async def go():
        return [
            (await _tool(name)).parameters["properties"]
            for name in ("screenshots_sync", "cpp_screenshots_sync")
        ]

    for properties in run_async(go()):
        assert "ctx" not in properties
        assert "apply" in properties


def test_sync_progress_survives_a_client_that_went_away(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)

    class _GoneContext:
        async def report_progress(self, *args, **kwargs) -> None:
            raise RuntimeError("session closed")

    result = _sync(monkeypatch, client, out, apply=True, ctx=_GoneContext())

    assert result.applied is True
    assert _cpp_checksums(client, "loc-de") == md5s["de-DE"]


def _blocking_apply(monkeypatch):
    """Hold every apply inside its first row until ``release`` is set."""
    release = asyncio.Event()
    entered = asyncio.Event()
    apply_step = shots.LocalizationScreenshotService.apply_sync_step

    async def held_step(self, step, version_id):
        entered.set()
        await release.wait()
        await apply_step(self, step, version_id)

    monkeypatch.setattr(
        shots.LocalizationScreenshotService, "apply_sync_step", held_step
    )
    return entered, release


def test_a_second_apply_on_the_same_page_is_refused_while_one_runs(
    export, monkeypatch
):
    out, md5s = export
    client = _sync_client(md5s)
    _patch_tools(monkeypatch, client)

    plan_sync = shots.LocalizationScreenshotService.plan_sync
    plans: list[str] = []

    async def counted_plan(self, steps):
        plans.append("plan")
        return await plan_sync(self, steps)

    monkeypatch.setattr(shots.LocalizationScreenshotService, "plan_sync", counted_plan)

    async def go():
        entered, release = _blocking_apply(monkeypatch)
        tool = await _tool("screenshots_sync")
        first = asyncio.create_task(tool.fn(app_id=7, dir=str(out), apply=True))
        await entered.wait()
        with pytest.raises(ToolError, match="already applying"):
            await asyncio.wait_for(
                tool.fn(app_id=7, dir=str(out), apply=True), timeout=5
            )
        assert len(plans) == 1, "the refused apply planned from a page mid-write"
        dry = await tool.fn(app_id=7, dir=str(out))
        release.set()
        return await first, dry, await tool.fn(app_id=7, dir=str(out), apply=True)

    first, dry, after = run_async(go())

    assert first.applied is True
    assert dry.applied is False
    assert after.applied is True


def test_the_apply_guard_is_released_when_an_apply_fails(export, monkeypatch):
    out, md5s = export
    client = _sync_client(md5s)
    apply_step = shots.LocalizationScreenshotService.apply_sync_step
    failures = [ASCAPIError(500, {"errors": [{"detail": "upstream exploded"}]})]

    async def failing_once(self, step, version_id):
        if failures:
            raise failures.pop()
        await apply_step(self, step, version_id)

    monkeypatch.setattr(
        shots.LocalizationScreenshotService, "apply_sync_step", failing_once
    )

    with pytest.raises(ToolError):
        _sync(monkeypatch, client, out, apply=True)
    assert _sync(monkeypatch, client, out, apply=True).applied is True


def test_a_cancelled_apply_releases_the_guard_and_stops_the_heartbeat(
    export, monkeypatch
):
    from app.mcp import progress

    out, md5s = export
    _patch_tools(monkeypatch, _sync_client(md5s))
    monkeypatch.setattr(progress, "PROGRESS_HEARTBEAT_SECONDS", 0.01)
    ctx = _RecordingContext()

    async def go():
        entered, release = _blocking_apply(monkeypatch)
        tool = await _tool("screenshots_sync")
        first = asyncio.create_task(
            tool.fn(app_id=7, dir=str(out), apply=True, ctx=ctx)
        )
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        reported = len(ctx.reports)
        await asyncio.sleep(0.05)
        assert len(ctx.reports) == reported
        release.set()
        return await tool.fn(app_id=7, dir=str(out), apply=True)

    assert run_async(go()).applied is True
