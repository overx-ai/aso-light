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

import base64
from contextlib import asynccontextmanager

import pytest
from fastmcp.exceptions import ToolError

from app.mcp.server import mcp
from app.mcp.tools import screenshots as screenshot_tools
from app.models.app import App
from app.schemas.screenshots import (
    MAX_SCREENSHOT_BYTES,
    decode_screenshot_payload,
)
from app.services.asc import screenshots as shots
from app.services.asc.cpp import ASCCustomProductPageService
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
    ) -> None:
        self.versions = versions
        self.localizations = localizations
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
    """Acceptance criterion: no orphan (configured but empty) set is left."""
    client = _client_with_two_locales()
    _patch_tools(monkeypatch, client)

    async def go():
        tool = await _tool("screenshots_delete")
        return await tool.fn(
            app_id=7,
            locale="de-DE",
            display_type="APP_IPHONE_67",
            screenshot_id="shot-de-0",
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
