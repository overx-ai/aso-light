"""App preview videos synced from an export directory (spec 017).

The asset-sync engine (:mod:`app.services.asc.asset_sync`) bound to
``appPreviewSets`` -> ``appPreviews``. What is preview-specific lives here:
each file is probed with ffprobe and refused unless Apple would accept it,
and an optional ``NN_*.poster.txt`` beside it sets the poster frame. The
services mirror the screenshot ones: the main listing, a Custom Product Page
and a PPO treatment.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from app.core.config import settings
from app.services.asc.asset_sync import (
    PREVIEW,
    PREVIEW_AUDIO_CHANNELS,
    PREVIEW_DEFAULT_FRAME_TIME_CODE,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_FPS,
    PREVIEW_MAX_S,
    PREVIEW_MIN_S,
    PREVIEW_POSTER_SUFFIX,
    PREVIEW_SUFFIXES,
    PREVIEW_TYPE_BY_SIZE,
    PREVIEW_VIDEO_CODEC,
    ExportChangedError,
    ExportFile,
    FileRefused,
    LocalizationAssetService,
    SyncPathError,
    source_checksum,
    within_sync_roots,
)
from app.services.asc.cpp import CPPSource
from app.services.asc.experiment import TreatmentSource
from app.services.asc.screenshots import MainListingSource

_FFPROBE_TIMEOUT_S = 60
_HASH_CHUNK_BYTES = 8 * 1024 * 1024
_FRAME_TIME_CODE = re.compile(r"^\d{2}:\d{2}:\d{2}:\d{2}$")
_ACCEPTED_SIZES = " / ".join(f"{w}x{h}" for w, h in PREVIEW_TYPE_BY_SIZE)


def run_ffprobe(path: Path) -> dict:
    """ffprobe's JSON for ``path``: its format and every stream."""
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                settings.FFPROBE_BIN,
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=_FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise SyncPathError(
            f"ffprobe is not available at FFPROBE_BIN={settings.FFPROBE_BIN!r}; "
            "install ffmpeg or point FFPROBE_BIN at it."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FileRefused("ffprobe timed out reading it") from exc
    if completed.returncode != 0:
        raise FileRefused("not a readable video")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FileRefused("not a readable video") from exc


def _frame_rate(stream: dict) -> Fraction | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            rate = Fraction(stream.get(key) or "")
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return None


def preview_type_of(probe: dict) -> str:
    """The ``previewType`` a probed file uploads as; every rule it breaks is named."""
    streams = probe.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise FileRefused("no video stream")

    problems: list[str] = []
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    preview_type = PREVIEW.type_for_size(width, height)
    if preview_type is None:
        problems.append(
            f"{width}x{height} is not an App Store preview size "
            f"(accepted: {_ACCEPTED_SIZES}, either orientation)"
        )
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if not PREVIEW_MIN_S <= duration <= PREVIEW_MAX_S:
        problems.append(f"{duration:.1f} s long, not {PREVIEW_MIN_S}–{PREVIEW_MAX_S} s")
    if video.get("codec_name") != PREVIEW_VIDEO_CODEC:
        problems.append(f"{video.get('codec_name') or 'unknown'} video, not H.264")
    rate = _frame_rate(video)
    if rate is None:
        problems.append("unknown frame rate")
    elif rate > PREVIEW_MAX_FPS:
        problems.append(f"{float(rate):.2f} fps, over {PREVIEW_MAX_FPS}")
    if audio is None:
        problems.append("no audio track (Apple wants stereo)")
    elif int(audio.get("channels") or 0) != PREVIEW_AUDIO_CHANNELS:
        problems.append(f"{audio.get('channels')}-channel audio, not stereo")
    if problems:
        raise FileRefused("; ".join(problems))
    return preview_type


def _poster_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}{PREVIEW_POSTER_SUFFIX}")


def _frame_time_code(path: Path) -> str:
    poster = _poster_path(path)
    if not poster.is_file():
        return PREVIEW_DEFAULT_FRAME_TIME_CODE
    if not within_sync_roots(poster):
        raise FileRefused(f"{poster.name} resolves outside SCREENSHOT_SYNC_ROOTS")
    try:
        time_code = poster.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise FileRefused(f"{poster.name} is unreadable") from exc
    if not _FRAME_TIME_CODE.match(time_code):
        raise FileRefused(f"{poster.name} holds {time_code!r}, not HH:MM:SS:FF")
    return time_code


def _checked_file(path: Path) -> int:
    """The file's size, once it is a readable preview file within the roots."""
    if not within_sync_roots(path):
        raise FileRefused("resolves outside SCREENSHOT_SYNC_ROOTS")
    if not path.is_file():
        raise FileRefused("not a file")
    if path.suffix.lower() not in PREVIEW_SUFFIXES:
        raise FileRefused("not an MP4, MOV or M4V file")
    size = path.stat().st_size
    if size > PREVIEW_MAX_BYTES:
        raise FileRefused(f"over the {PREVIEW_MAX_BYTES}-byte cap")
    return size


def _md5_of(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 — Apple's sourceFileChecksum, not security
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise FileRefused(f"unreadable ({exc.strerror})") from exc
    return digest.hexdigest()


def read_preview_file(path: Path) -> tuple[str, ExportFile] | None:
    """``(previewType, file)`` for an Apple-valid preview; ``None`` for a poster."""
    if path.name.endswith(PREVIEW_POSTER_SUFFIX):
        return None
    size = _checked_file(path)
    preview_type = preview_type_of(run_ffprobe(path))
    attributes = {"previewFrameTimeCode": _frame_time_code(path)}
    return preview_type, ExportFile(path, _md5_of(path), size, attributes)


def read_planned_preview(export_file: ExportFile) -> bytes:
    """The file's bytes, refused unless they still hash to the planned MD5."""
    try:
        _checked_file(export_file.path)
        data = export_file.path.read_bytes()
    except (FileRefused, OSError) as exc:
        raise ExportChangedError(f"{export_file.path}: {exc}.") from exc
    if source_checksum(data) != export_file.md5:
        raise ExportChangedError(
            f"{export_file.path} changed after it was planned and was not "
            "uploaded; rerun the sync."
        )
    return data


class PreviewSyncService(LocalizationAssetService):
    """The asset-sync engine bound to app previews."""

    kind = PREVIEW

    def read_export_file(self, path: Path) -> tuple[str, ExportFile] | None:
        return read_preview_file(path)

    def read_planned_bytes(self, export_file: ExportFile) -> bytes:
        return read_planned_preview(export_file)


class ASCVersionPreviewService(MainListingSource, PreviewSyncService):
    """App previews on the app's MAIN product page (its editable version)."""


class CPPPreviewService(CPPSource, PreviewSyncService):
    """App previews on a Custom Product Page's editable version."""


class TreatmentPreviewService(TreatmentSource, PreviewSyncService):
    """App previews on a PPO treatment."""
