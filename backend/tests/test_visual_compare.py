"""Unit tests for the Pillow before/after screenshot compositor.

Drives :func:`app.services.visual.compare.build_comparison` end to end with
the ASC service methods stubbed (no Apple network) and image downloads
mocked: a fake ``httpx.AsyncClient`` returns in-memory PNG bytes for the CDN
URLs. Asserts the compositor returns valid decodable PNG bytes for the happy
path, the mismatched-count path, and the all-missing (placeholder-only) path.
"""
from __future__ import annotations

import io

import httpx
from PIL import Image

import app.services.visual.compare as compare_mod
from app.services.visual.compare import build_comparison
from tests._async_harness import run_async


def _png_bytes(width: int = 60, height: int = 130, color=(120, 80, 200)) -> bytes:
    """Render a tiny solid PNG to stand in for a downloaded screenshot."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class _FakeHTTPClient:
    """Async context-manager replacement for ``httpx.AsyncClient``.

    ``url_map`` maps a source_url -> PNG bytes; unknown URLs 404 so the
    compositor renders a placeholder cell.
    """

    def __init__(self, url_map: dict[str, bytes], **_kwargs):
        self._url_map = url_map

    async def __aenter__(self) -> "_FakeHTTPClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        if url in self._url_map:
            return _FakeResponse(self._url_map[url])
        return _FakeResponse(b"", status_code=404)


def _patch_service(monkeypatch, *, default_shots, cpp_shots):
    """Stub the ASC service methods the compositor calls.

    ``default_shots`` / ``cpp_shots`` are the flat screenshot dicts for the
    requested display type; we wrap each into a single set so
    ``_collect_screenshots`` flattens it back out.
    """
    Service = compare_mod.ASCCustomProductPageService

    async def fake_default_loc(self, asc_app_id, locale):
        return "vloc-1"

    async def fake_cpp_loc(self, cpp_id, locale):
        return "loc-1"

    async def fake_default_screens(self, vloc_id):
        return [{"display_type": "APP_IPHONE_67", "screenshots": default_shots}]

    async def fake_cpp_screens(self, loc_id):
        return [{"display_type": "APP_IPHONE_67", "screenshots": cpp_shots}]

    async def fake_get_cpp(self, cpp_id):
        return {"id": cpp_id, "attributes": {"name": "Variant A"}}

    monkeypatch.setattr(
        Service, "get_default_version_localization_id", fake_default_loc
    )
    monkeypatch.setattr(Service, "get_cpp_localization_id", fake_cpp_loc)
    monkeypatch.setattr(Service, "get_default_screenshots", fake_default_screens)
    monkeypatch.setattr(Service, "get_cpp_screenshots", fake_cpp_screens)
    monkeypatch.setattr(Service, "get_cpp", fake_get_cpp)


def _assert_valid_png(data: bytes) -> Image.Image:
    assert isinstance(data, bytes) and len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    img.load()
    assert img.format == "PNG"
    return img


def test_build_comparison_renders_valid_png(monkeypatch):
    before_url = "https://cdn/before-1.png"
    after_url = "https://cdn/after-1.png"
    url_map = {before_url: _png_bytes(), after_url: _png_bytes()}

    _patch_service(
        monkeypatch,
        default_shots=[{"source_url": before_url}],
        cpp_shots=[{"source_url": after_url}],
    )
    monkeypatch.setattr(
        compare_mod.httpx,
        "AsyncClient",
        lambda **kw: _FakeHTTPClient(url_map, **kw),
    )

    data = run_async(
        build_comparison(
            asc_client=object(),  # unused: every service method is stubbed
            asc_app_id="app-1",
            cpp_id="cpp-1",
            locale="en-US",
            display_type="APP_IPHONE_67",
        )
    )
    img = _assert_valid_png(data)
    assert img.width > 0 and img.height > 0


def test_build_comparison_handles_mismatched_counts_and_missing_cells(monkeypatch):
    # BEFORE has two screenshots (one fails to download), AFTER has one — the
    # row alignment must pad with a placeholder and not raise.
    before_ok = "https://cdn/before-ok.png"
    before_broken = "https://cdn/before-broken.png"  # not in url_map -> 404
    after_url = "https://cdn/after.png"
    url_map = {before_ok: _png_bytes(), after_url: _png_bytes()}

    _patch_service(
        monkeypatch,
        default_shots=[
            {"source_url": before_ok},
            {"source_url": before_broken},
        ],
        cpp_shots=[{"source_url": after_url}],
    )
    monkeypatch.setattr(
        compare_mod.httpx,
        "AsyncClient",
        lambda **kw: _FakeHTTPClient(url_map, **kw),
    )

    data = run_async(
        build_comparison(
            asc_client=object(),
            asc_app_id="app-1",
            cpp_id="cpp-1",
            locale="en-US",
            display_type="APP_IPHONE_67",
        )
    )
    _assert_valid_png(data)


def test_build_comparison_all_missing_renders_placeholder_montage(monkeypatch):
    # No matching screenshots at all — every cell is a placeholder, the
    # montage still encodes to a valid PNG (columns floor at 1).
    _patch_service(monkeypatch, default_shots=[], cpp_shots=[])
    monkeypatch.setattr(
        compare_mod.httpx,
        "AsyncClient",
        lambda **kw: _FakeHTTPClient({}, **kw),
    )

    data = run_async(
        build_comparison(
            asc_client=object(),
            asc_app_id="app-1",
            cpp_id="cpp-1",
            locale="en-US",
            display_type="APP_IPHONE_67",
        )
    )
    _assert_valid_png(data)
