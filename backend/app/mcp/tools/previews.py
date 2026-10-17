"""MCP tools that sync app preview videos from an export directory (spec 017).

``previews_sync``, ``cpp_previews_sync`` and ``experiment_previews_sync`` are
the screenshot syncs with ``appPreviewSets`` as the target: the same engine,
binders, allowlist and one-apply guard, run through
:func:`app.mcp.tools.screenshots.run_asset_sync`.
"""
from __future__ import annotations

from fastmcp import Context

from app.mcp.server import mcp
from app.mcp.tools.cpp import cpp_target
from app.mcp.tools.experiment import treatment_target
from app.mcp.tools.screenshots import SyncBinder, main_listing_target, run_asset_sync
from app.schemas.previews import PreviewSyncResult
from app.services.asc.asset_sync import PREVIEW
from app.services.asc.previews import (
    ASCVersionPreviewService,
    CPPPreviewService,
    TreatmentPreviewService,
)


async def _run_preview_sync(
    app_id: int,
    directory: str,
    bind: SyncBinder,
    *,
    locales: list[str] | None,
    preview_types: list[str] | None,
    apply: bool,
    ctx: Context | None,
) -> PreviewSyncResult:
    return await run_asset_sync(
        app_id,
        directory,
        bind,
        kind=PREVIEW,
        types=preview_types,
        locales=locales,
        apply=apply,
        ctx=ctx,
        result_type=PreviewSyncResult,
    )


@mcp.tool(name="previews_sync")
async def sync_version_previews(
    app_id: int,
    dir: str,  # noqa: A002 — the name callers and spec 017 use
    locales: list[str] | None = None,
    preview_types: list[str] | None = None,
    apply: bool = False,
    ctx: Context | None = None,
) -> PreviewSyncResult:
    """Make an export (``<dir>/<locale>/NN_*.mp4|mov|m4v``) the MAIN product page's app previews.

    ``screenshots_sync`` for videos, on the editable App Store version. Every
    file is probed with ffprobe and refused, named in an ``error`` row, unless
    it is 15–30 s, 886x1920 (iPhone, ``IPHONE_67``) or 1200x1600 (iPad,
    ``IPAD_PRO_3GEN_129``) in either orientation, H.264 at ≤30 fps, with a
    stereo audio track, and ≤500 MB. At most 3 previews per type per locale.
    An optional ``NN_*.poster.txt`` beside a video holds its poster frame as
    ``HH:MM:SS:FF`` (default ``00:00:05:00``); it is set when the preview
    uploads, so changing only the poster file does not re-upload.

    Each type present is replaced as a unit in filename order, a slot whose
    preview already has the file's MD5 is skipped, and other preview types
    are never touched. Dry run by default; with ``apply=True`` nothing is
    written while any row is an ``error``.

    Args:
        app_id: The local app id.
        dir: Absolute path under ``SCREENSHOT_SYNC_ROOTS``: one project's
            hero-ai export (its ``export_dir``,
            ``appstore-exports/previews/<project_id>``) or a fastlane ``previews/``.
        locales: Sync only these locale directories (default: every one).
        preview_types: Sync only these ``previewType`` values (default: every
            type the files map to). Required when one locale directory mixes two.
        apply: False (default) returns the plan and writes nothing.

    Returns:
        One row per locale x preview type (``skip`` | ``replace`` | ``upload``
        | ``error``; ``display_type`` holds the ``previewType``), what was left
        ``untouched``, and after an apply the read-back ``count`` per row plus
        the ``inventory``.
    """
    return await _run_preview_sync(
        app_id,
        dir,
        main_listing_target(ASCVersionPreviewService),
        locales=locales,
        preview_types=preview_types,
        apply=apply,
        ctx=ctx,
    )


@mcp.tool(name="cpp_previews_sync")
async def sync_cpp_previews(
    app_id: int,
    cpp_id: str,
    dir: str,  # noqa: A002 — the name callers and spec 017 use
    locales: list[str] | None = None,
    preview_types: list[str] | None = None,
    apply: bool = False,
    ctx: Context | None = None,
) -> PreviewSyncResult:
    """Make an export (``<dir>/<locale>/NN_*.mp4``) a Custom Product Page's app previews.

    ``previews_sync`` with the CPP's editable version as the target, under the
    same probe, limits and dry-run default. A locale the page has no
    localization for is planned as ``create_localization`` and created on
    apply; it must be one of the app's own locales. A page whose only version
    is in review is refused.

    Args:
        app_id: The local app id.
        cpp_id: The Custom Product Page id.
        dir: Absolute path under ``SCREENSHOT_SYNC_ROOTS``.
        locales: Sync only these locale directories (default: every one).
        preview_types: Sync only these ``previewType`` values.
        apply: False (default) returns the plan and writes nothing.
    """
    return await _run_preview_sync(
        app_id,
        dir,
        cpp_target(cpp_id, CPPPreviewService, check_families=False),
        locales=locales,
        preview_types=preview_types,
        apply=apply,
        ctx=ctx,
    )


@mcp.tool(name="experiment_previews_sync")
async def sync_experiment_previews(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    dir: str,  # noqa: A002 — the name callers and spec 017 use
    locales: list[str] | None = None,
    preview_types: list[str] | None = None,
    apply: bool = False,
    ctx: Context | None = None,
) -> PreviewSyncResult:
    """Make an export (``<dir>/<locale>/NN_*.mp4``) a PPO treatment's app previews.

    ``previews_sync`` with an experiment treatment as the target, under the
    same probe, limits and dry-run default. The experiment must belong to the
    app, the treatment to the experiment, and the experiment must still be
    editable (``PREPARE_FOR_SUBMISSION``, ``READY_FOR_REVIEW`` or
    ``REJECTED``); anything else is refused before any write. ``version_id``
    in the reply is the treatment id.

    Args:
        app_id: The local app id.
        experiment_id: The experiment (``appStoreVersionExperiments``) id.
        treatment_id: The treatment id, one of the experiment's.
        dir: Absolute path under ``SCREENSHOT_SYNC_ROOTS``.
        locales: Sync only these locale directories (default: every one).
        preview_types: Sync only these ``previewType`` values.
        apply: False (default) returns the plan and writes nothing.
    """
    return await _run_preview_sync(
        app_id,
        dir,
        treatment_target(experiment_id, treatment_id, TreatmentPreviewService),
        locales=locales,
        preview_types=preview_types,
        apply=apply,
        ctx=ctx,
    )
