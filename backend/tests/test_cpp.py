"""Unit tests for the ASC Custom Product Page service.

Drives :class:`app.services.asc.cpp.ASCCustomProductPageService` against a
``FakeASCClient`` that records every call and replays canned JSON:API
responses. Asserts the endpoint paths + request bodies for the CPP CRUD
helpers, the version/localization reads, and the ``imageAsset.templateUrl``
``{w}``/``{h}``/``{f}`` substitution in the screenshot shaping path.

No network, no DB. The async entrypoints are driven via the shared
``run_async`` harness so the module stays consistent with the rest of the
backend test suite.
"""
from __future__ import annotations

import pytest

from app.services.asc.cpp import ASCCustomProductPageService
from tests._async_harness import run_async


class FakeASCClient:
    """Records ASC calls and returns scripted responses.

    ``responses`` maps ``(method, path)`` -> payload. ``_get_all_pages``
    returns the ``data`` list of the matching GET payload. Every call is
    appended to ``calls`` as ``(method, path, json)`` for assertions.
    """

    def __init__(self, responses: dict[tuple[str, str], dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def _lookup(self, method: str, path: str) -> dict:
        return self.responses.get((method, path), {"data": []})

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, None))
        return self._lookup("GET", path)

    async def _get_all_pages(
        self, path: str, params: dict | None = None
    ) -> list[dict]:
        self.calls.append(("GET_ALL", path, None))
        return self._lookup("GET", path).get("data", [])

    async def _post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, json))
        return self._lookup("POST", path)

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("PATCH", path, json))
        return self._lookup("PATCH", path)

    async def _delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))


def _service(responses=None) -> tuple[ASCCustomProductPageService, FakeASCClient]:
    client = FakeASCClient(responses)
    return ASCCustomProductPageService(client), client  # type: ignore[arg-type]


# ------------------------------------------------------------------
# CRUD
# ------------------------------------------------------------------


def test_list_cpps_hits_app_scoped_endpoint():
    svc, client = _service({
        ("GET", "/apps/app-123/appCustomProductPages"): {
            "data": [{"id": "cpp-1", "attributes": {"name": "A", "visible": True}}],
        },
    })
    rows = run_async(svc.list_cpps("app-123"))
    assert rows[0]["id"] == "cpp-1"
    assert client.calls == [
        ("GET_ALL", "/apps/app-123/appCustomProductPages", None),
    ]


def test_get_cpp_returns_data_resource():
    svc, client = _service({
        ("GET", "/appCustomProductPages/cpp-1"): {
            "data": {"id": "cpp-1", "attributes": {"name": "A"}},
        },
    })
    out = run_async(svc.get_cpp("cpp-1"))
    assert out["id"] == "cpp-1"
    assert ("GET", "/appCustomProductPages/cpp-1", None) in client.calls


def test_create_cpp_posts_app_relationship_and_attributes():
    svc, client = _service({
        ("POST", "/appCustomProductPages"): {"data": {"id": "cpp-new"}},
    })
    out = run_async(svc.create_cpp("app-123", "My CPP", visible=False))
    assert out["id"] == "cpp-new"

    method, path, body = next(
        c for c in client.calls if c[0] == "POST"
    )
    assert path == "/appCustomProductPages"
    data = body["data"]
    assert data["type"] == "appCustomProductPages"
    assert data["attributes"] == {"name": "My CPP", "visible": False}
    assert data["relationships"]["app"]["data"] == {
        "type": "apps", "id": "app-123",
    }


def test_update_cpp_sends_only_provided_attributes():
    svc, client = _service({
        ("PATCH", "/appCustomProductPages/cpp-1"): {"data": {"id": "cpp-1"}},
    })
    run_async(svc.update_cpp("cpp-1", name="Renamed"))
    _method, path, body = next(c for c in client.calls if c[0] == "PATCH")
    assert path == "/appCustomProductPages/cpp-1"
    assert body["data"]["attributes"] == {"name": "Renamed"}
    assert body["data"]["id"] == "cpp-1"


def test_update_cpp_with_no_fields_raises():
    svc, _client = _service()
    with pytest.raises(ValueError):
        run_async(svc.update_cpp("cpp-1"))


def test_delete_cpp_issues_delete():
    svc, client = _service()
    run_async(svc.delete_cpp("cpp-1"))
    assert ("DELETE", "/appCustomProductPages/cpp-1", None) in client.calls


# ------------------------------------------------------------------
# Versions / localizations
# ------------------------------------------------------------------


def test_list_versions_paginates_version_endpoint():
    svc, client = _service({
        ("GET", "/appCustomProductPages/cpp-1/appCustomProductPageVersions"): {
            "data": [{"id": "ver-1", "attributes": {"state": "PREPARE_FOR_SUBMISSION"}}],
        },
    })
    rows = run_async(svc.list_versions("cpp-1"))
    assert rows[0]["id"] == "ver-1"
    assert (
        "GET_ALL",
        "/appCustomProductPages/cpp-1/appCustomProductPageVersions",
        None,
    ) in client.calls


def test_list_localizations_paginates_version_localizations():
    path = (
        "/appCustomProductPageVersions/ver-1"
        "/appCustomProductPageLocalizations"
    )
    svc, client = _service({
        ("GET", path): {
            "data": [{"id": "loc-1", "attributes": {"locale": "en-US"}}],
        },
    })
    rows = run_async(svc.list_localizations("ver-1"))
    assert rows[0]["id"] == "loc-1"
    assert ("GET_ALL", path, None) in client.calls


# ------------------------------------------------------------------
# Screenshots — source_url templateUrl substitution
# ------------------------------------------------------------------


def _screenshot_sets_payload() -> dict:
    return {
        "data": [
            {
                "id": "set-1",
                "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
                "relationships": {
                    "appScreenshots": {"data": [{"id": "shot-1"}]},
                },
            },
        ],
        "included": [
            {
                "type": "appScreenshots",
                "id": "shot-1",
                "attributes": {
                    "fileName": "hero.png",
                    "imageAsset": {
                        "templateUrl": "https://cdn.apple/img/{w}x{h}.{f}",
                        "width": 1290,
                        "height": 2796,
                    },
                },
            },
        ],
    }


def test_get_cpp_screenshots_shapes_sets_and_builds_source_url():
    path = (
        "/appCustomProductPageLocalizations/loc-1/appScreenshotSets"
    )
    svc, _client = _service({("GET", path): _screenshot_sets_payload()})
    sets = run_async(svc.get_cpp_screenshots("loc-1"))
    assert len(sets) == 1
    shot = sets[0]["screenshots"][0]
    assert sets[0]["display_type"] == "APP_IPHONE_67"
    assert shot["file_name"] == "hero.png"
    # {w}/{h}/{f} substituted from the asset's own dimensions.
    assert shot["source_url"] == "https://cdn.apple/img/1290x2796.png"


def test_get_default_screenshots_uses_version_localization_endpoint():
    path = (
        "/appStoreVersionLocalizations/vloc-1/appScreenshotSets"
    )
    svc, client = _service({("GET", path): _screenshot_sets_payload()})
    sets = run_async(svc.get_default_screenshots("vloc-1"))
    assert sets[0]["screenshots"][0]["source_url"] == (
        "https://cdn.apple/img/1290x2796.png"
    )
    assert ("GET", path, None) in client.calls


def test_build_source_url_falls_back_to_marketing_resolution():
    out = ASCCustomProductPageService._build_source_url(
        {"templateUrl": "https://cdn.apple/{w}-{h}.{f}"}
    )
    assert out == "https://cdn.apple/1290-2796.png"


def test_build_source_url_none_when_no_template():
    assert ASCCustomProductPageService._build_source_url(None) is None
    assert ASCCustomProductPageService._build_source_url({}) is None
