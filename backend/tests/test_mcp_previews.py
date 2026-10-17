"""App preview sync (spec 017): the probe, and the shared engine on appPreviewSets.

The preview tree has the screenshot tree's shape, so the screenshot suite's
``FakeASC`` serves it once the resource names are swapped. ffprobe is faked
per file name, except in the one test that probes a real video.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from app.core.config import settings
from app.services.asc import previews
from app.services.asc.asset_sync import FileRefused
from tests._async_harness import run_async
from tests.test_mcp_screenshots import (
    CPP_ID,
    CPP_VERSION,
    WRITES,
    FakeASC,
    _cpp_version,
    _patch_tools,
    _tool,
    _version,
)

_TO_SCREENSHOT = (
    ("appPreviewSets", "appScreenshotSets"),
    ("appPreviewSet", "appScreenshotSet"),
    ("appPreviews", "appScreenshots"),
    ("previewType", "screenshotDisplayType"),
    ("videoDeliveryState", "assetDeliveryState"),
)
_TO_PREVIEW = tuple((new, old) for old, new in _TO_SCREENSHOT)


def _swap(value, pairs):
    text = json.dumps(value)
    for old, new in pairs:
        text = text.replace(old, new)
    return json.loads(text)


class PreviewASC(FakeASC):
    """``requests`` records every call as sent: preview paths and bodies."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.requests: list[tuple[str, str, dict | None]] = []

    async def _get(self, path, params=None):
        self.requests.append(("GET", path, None))
        response = await super()._get(_swap(path, _TO_SCREENSHOT), _swap(params, _TO_SCREENSHOT))
        return _swap(response, _TO_PREVIEW)

    async def _post(self, path, json=None):
        self.requests.append(("POST", path, json))
        response = await super()._post(_swap(path, _TO_SCREENSHOT), _swap(json, _TO_SCREENSHOT))
        return _swap(response, _TO_PREVIEW)

    async def _patch(self, path, json=None):
        self.requests.append(("PATCH", path, json))
        response = await super()._patch(_swap(path, _TO_SCREENSHOT), _swap(json, _TO_SCREENSHOT))
        return _swap(response, _TO_PREVIEW)

    async def _delete(self, path, json=None):
        self.requests.append(("DELETE", path, json))
        await super()._delete(_swap(path, _TO_SCREENSHOT))

    async def _put_binary(self, url, data, content_type="application/octet-stream"):
        self.requests.append(("PUT", url, None))
        await super()._put_binary(url, data, content_type)


def _probe(width=886, height=1920, duration=20.0, codec="h264", fps="30/1", channels=2):
    streams = [{
        "codec_type": "video",
        "codec_name": codec,
        "width": width,
        "height": height,
        "avg_frame_rate": fps,
    }]
    if channels:
        streams.append({"codec_type": "audio", "channels": channels})
    return {"format": {"duration": str(duration)}, "streams": streams}


@pytest.fixture
def out(tmp_path, monkeypatch):
    """An allowlisted export root; ``probes`` maps a file name to its ffprobe JSON."""
    root = tmp_path / "allowed"
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(root)])
    probes: dict[str, dict] = {}
    monkeypatch.setattr(previews, "run_ffprobe", lambda path: probes.get(path.name, _probe()))
    directory = root / "previews"
    directory.mkdir(parents=True)
    return directory, probes


def _video(path: Path, tag: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"video {tag}".encode() * 64)
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324


def _sync(monkeypatch, client: PreviewASC, name: str, directory: Path, **kwargs):
    _patch_tools(monkeypatch, client)

    async def go():
        return await (await _tool(name)).fn(app_id=7, dir=str(directory), **kwargs)

    return run_async(go())


def _writes(client: PreviewASC) -> list[tuple[str, str, dict | None]]:
    return [call for call in client.requests if call[0] in WRITES]


def _actions(result) -> dict[tuple[str, str | None], str]:
    return {(row.locale, row.display_type): row.action for row in result.rows}


def _listing_client() -> PreviewASC:
    return PreviewASC(
        versions=[_version()],
        localizations={"ver-1": {"en-US": "loc-en", "de-DE": "loc-de"}},
    )


def _cpp_client() -> PreviewASC:
    """The app ships en-US and de-DE; the CPP has en-US only."""
    return PreviewASC(
        versions=[_version(state="READY_FOR_SALE")],
        localizations={"ver-1": {"en-US": "loc-en", "de-DE": "loc-de"}},
        cpp_versions={CPP_ID: [_cpp_version()]},
        cpp_localizations={CPP_VERSION: {"en-US": "cloc-en"}},
    )


# ------------------------------------------------------------------
# Probe
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probe", "named"),
    [
        (_probe(width=1290, height=2796), "1290x2796 is not an App Store preview size"),
        (_probe(duration=35.2), "35.2 s long"),
        (_probe(duration=9), "9.0 s long"),
        (_probe(codec="hevc"), "hevc video, not H.264"),
        (_probe(fps="60/1"), "60.00 fps, over 30"),
        (_probe(channels=1), "1-channel audio, not stereo"),
        (_probe(channels=0), "no audio track"),
    ],
)
def test_probe_refuses_what_apple_rejects(probe, named):
    with pytest.raises(FileRefused, match=named):
        previews.preview_type_of(probe)


def test_probe_maps_both_orientations_of_both_sizes():
    assert previews.preview_type_of(_probe(886, 1920)) == "IPHONE_67"
    assert previews.preview_type_of(_probe(1920, 886, fps="30000/1001")) == "IPHONE_67"
    assert previews.preview_type_of(_probe(1600, 1200)) == "IPAD_PRO_3GEN_129"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_probe_reads_a_real_apple_valid_video(tmp_path):
    path = tmp_path / "01_preview.mp4"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=886x1920:r=1:d=16",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "16", "-c:v", "libx264", "-c:a", "aac", str(path),
        ],
        check=True,
    )

    assert previews.preview_type_of(previews.run_ffprobe(path)) == "IPHONE_67"


# ------------------------------------------------------------------
# Sync
# ------------------------------------------------------------------


def test_dry_run_plans_only_the_files_that_pass_the_probe(out, monkeypatch):
    directory, probes = out
    _video(directory / "en-US" / "01_hero.mp4", "en")
    (directory / "en-US" / "01_hero.poster.txt").write_text("00:00:07:12")
    _video(directory / "en-US" / "02_long.mp4", "long")
    probes["02_long.mp4"] = _probe(duration=41)
    _video(directory / "de-DE" / "01_hero.mp4", "de")
    client = _listing_client()

    result = _sync(monkeypatch, client, "previews_sync", directory, apply=True)

    assert result.applied is False
    assert _actions(result) == {
        ("de-DE", "IPHONE_67"): "upload",
        ("en-US", "IPHONE_67"): "upload",
        ("en-US", None): "error",
    }
    refused = next(row.error for row in result.rows if row.action == "error")
    assert "en-US/02_long.mp4" in refused and "41.0 s long" in refused
    assert next(r.files for r in result.rows if r.display_type and r.locale == "en-US") == 1
    assert _writes(client) == []


def test_cpp_apply_reserves_uploads_and_commits_then_a_rerun_is_a_noop(out, monkeypatch):
    directory, _probes = out
    en = _video(directory / "en-US" / "01_preview.mp4", "en")
    (directory / "en-US" / "01_preview.poster.txt").write_text("00:00:07:12\n")
    de = _video(directory / "de-DE" / "01_preview.mp4", "de")
    client = _cpp_client()

    result = _sync(monkeypatch, client, "cpp_previews_sync", directory, cpp_id=CPP_ID, apply=True)

    assert result.applied is True
    assert _actions(result) == {
        ("de-DE", "IPHONE_67"): "create_localization",
        ("en-US", "IPHONE_67"): "upload",
    }
    reservations = [body for method, path, body in client.requests if path == "/appPreviews"]
    assert [r["data"]["attributes"]["previewFrameTimeCode"] for r in reservations] == [
        "00:00:05:00", "00:00:07:12",
    ]
    assert {r["data"]["relationships"]["appPreviewSet"]["data"]["type"] for r in reservations} == {
        "appPreviewSets"
    }
    commits = [
        body["data"]["attributes"]
        for method, path, body in client.requests
        if method == "PATCH" and path.startswith("/appPreviews/")
    ]
    assert commits == [
        {"uploaded": True, "sourceFileChecksum": de},
        {"uploaded": True, "sourceFileChecksum": en},
    ]
    assert sum(1 for method, *_ in client.requests if method == "PUT") == 2
    assert {row.locale: row.count for row in result.rows} == {"de-DE": 1, "en-US": 1}

    client.requests.clear()
    again = _sync(monkeypatch, client, "cpp_previews_sync", directory, cpp_id=CPP_ID, apply=True)

    assert set(_actions(again).values()) == {"skip"}
    assert _writes(client) == []


def test_more_than_three_previews_of_one_type_is_an_error_before_any_write(out, monkeypatch):
    directory, _probes = out
    for index in range(1, 5):
        _video(directory / "en-US" / f"0{index}_clip.mp4", str(index))
    client = _listing_client()

    result = _sync(monkeypatch, client, "previews_sync", directory, apply=True)

    assert "Apple's cap is 3" in result.rows[0].error
    assert _writes(client) == []


def test_a_processing_preview_of_the_same_file_is_not_uploaded_again(out, monkeypatch):
    directory, _probes = out
    path = directory / "en-US" / "01_preview.mp4"
    _video(path, "en")
    client = _listing_client()
    client.sets = {"pset": {"display_type": "IPHONE_67", "localization_id": "loc-en", "shots": ["p1"]}}
    client.screenshots = {
        "p1": {"file_name": path.name, "file_size": path.stat().st_size, "state": "PROCESSING"},
    }

    result = _sync(monkeypatch, client, "previews_sync", directory, locales=["en-US"], apply=True)

    assert (result.rows[0].action, result.rows[0].processing) == ("skip", 1)
    assert _writes(client) == []


def test_a_missing_ffprobe_is_one_clear_error(tmp_path, monkeypatch):
    directory = tmp_path / "previews"
    _video(directory / "en-US" / "01_preview.mp4", "en")
    monkeypatch.setattr(settings, "SCREENSHOT_SYNC_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(settings, "FFPROBE_BIN", str(tmp_path / "no-ffprobe"))

    with pytest.raises(ToolError, match="ffprobe is not available"):
        _sync(monkeypatch, _listing_client(), "previews_sync", directory)
