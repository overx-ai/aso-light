"""MCP tools for the app's MAIN product-page screenshots, plus visual compare.

``screenshots_list`` / ``screenshots_upload`` / ``screenshots_delete`` operate
on the editable App Store version's ``appStoreVersionLocalizations`` via
:class:`app.services.asc.screenshots.ASCVersionScreenshotService`.

``screenshots_list`` is the reason the trio exists: after a bulk localized
upload run, ASC's flaky post-upload polling can leave *some locales silently
short*, and Apple only says so at submit time. The tool reports per locale x
display type counts plus a ``gaps`` worklist, so a run can be resumed or
repaired without opening N locales in the App Store Connect UI.

``screenshots_compare`` is unrelated plumbing: it composites a DEFAULT vs
Custom Product Page montage and returns it as a FastMCP :class:`Image`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from app.api.v1._deps import _get_asc_client_for_app
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.schemas.screenshots import (
    MAX_SCREENSHOT_FILES,
    DisplayTypeScreenshotStatus,
    LocaleScreenshotStatus,
    Screenshot,
    ScreenshotDeleteResult,
    ScreenshotGap,
    ScreenshotUploadResult,
    VersionScreenshotInventory,
    decode_screenshot_payload,
    is_valid_display_type,
)
from app.services.asc.errors import ASCAPIError
from app.services.asc.screenshots import (
    ASSET_STATE_COMPLETE,
    ASSET_STATE_FAILED,
    ASCVersionScreenshotService,
    EditableVersion,
    VersionNotEditableError,
)
from app.services.visual.compare import build_comparison

logger = logging.getLogger(__name__)

# Read-back policy for a committed asset. Apple promotes ``UPLOAD_COMPLETE`` ->
# ``COMPLETE`` asynchronously, so we re-read a few times before reporting what
# we actually observed. Module-level so tests can drive it to zero delay.
_VERIFY_ATTEMPTS = 3
_VERIFY_DELAY_SECONDS = 1.0


@asynccontextmanager
async def _asc_tool_error() -> AsyncIterator[None]:
    """Surface ASC failures as single-line ``ToolError``s (mirrors cpp tools).

    :class:`VersionNotEditableError` is included here so "the version is live"
    reads as a sentence naming the state instead of a 409 several calls later.
    """
    try:
        yield
    except VersionNotEditableError as exc:
        raise ToolError(exc.message)
    except ASCAPIError as exc:
        raise ToolError(f"ASC API error: {exc.message}")


# ==================================================================
# Internal helpers
# ==================================================================


def _require_display_type(display_type: str) -> None:
    """Reject a typo'd device family before it reaches Apple as a 502."""
    if not is_valid_display_type(display_type):
        raise ToolError(f"Unknown display_type '{display_type}'.")


def _usable(screenshots: list[dict]) -> list[dict]:
    """Assets that actually count — Apple-``FAILED`` ones occupy a slot only."""
    return [s for s in screenshots if s.get("state") != ASSET_STATE_FAILED]


def _to_schema(shaped: dict, display_type: str) -> Screenshot:
    """Shaped service dict -> :class:`Screenshot` (display type stamped in)."""
    return Screenshot(**{**shaped, "display_type": display_type})


async def _resolve_localization(
    service: ASCVersionScreenshotService, asc_app_id: str, locale: str
) -> tuple[str, str]:
    """Resolve ``(version_id, localization_id)`` for a locale on the editable version.

    Raises:
        ToolError: When the editable version has no such locale — creating
            version localizations belongs to the metadata tools, not here.
    """
    version = await service.resolve_editable_version(asc_app_id)
    localizations = await service.localizations_by_locale(version.id)
    localization_id = localizations.get(locale)
    if localization_id is None:
        known = ", ".join(sorted(localizations)) or "none"
        raise ToolError(
            f"App Store version {version.version_string or version.id} has no "
            f"'{locale}' localization — create it first with "
            f"metadata_create_locale. Existing locales: {known}."
        )
    return version.id, localization_id


async def _verify_upload(
    service: ASCVersionScreenshotService, screenshot_id: str
) -> tuple[dict, bool, str | None]:
    """Read a committed asset back and report only what Apple confirms.

    Returns ``(shaped, verified, warning)``. Raises when Apple reports the
    asset as ``FAILED`` or attaches delivery errors — a 2xx on the commit
    PATCH is not verification.
    """
    shaped: dict = {}
    for attempt in range(_VERIFY_ATTEMPTS):
        shaped = await service.read_back(screenshot_id)
        state = shaped.get("state")
        errors = shaped.get("errors") or []
        if state == ASSET_STATE_FAILED or errors:
            detail = "; ".join(errors) if errors else "no detail given"
            raise ToolError(
                f"Apple rejected the uploaded screenshot (state={state}): {detail}"
            )
        if state == ASSET_STATE_COMPLETE:
            return shaped, True, None
        if attempt < _VERIFY_ATTEMPTS - 1 and _VERIFY_DELAY_SECONDS:
            await asyncio.sleep(_VERIFY_DELAY_SECONDS)
    return (
        shaped,
        False,
        f"Asset committed but Apple still reports state="
        f"{shaped.get('state') or 'unknown'} (not {ASSET_STATE_COMPLETE}). "
        f"Re-run screenshots_list before submitting.",
    )


def _build_inventory(
    *,
    app_id: int,
    version: EditableVersion,
    localizations: dict[str, str],
    sets_by_locale: dict[str, list[dict]],
    display_types_filter: list[str] | None,
    expected_count: int | None,
    include_assets: bool,
) -> VersionScreenshotInventory:
    """Fold per-locale screenshot sets into the counts/completeness report.

    Expectation per display type is ``expected_count`` when the caller pins
    one, else the **highest count any locale has** for that device family (at
    least 1). That is what makes an interrupted bulk run visible: the locales
    that finished define the target, and the ones that did not show up as gaps.
    """
    # locale -> display type -> (set_id, screenshots)
    indexed: dict[str, dict[str, tuple[str, list[dict]]]] = {
        locale: {
            shot_set["display_type"]: (
                shot_set["id"],
                shot_set.get("screenshots", []),
            )
            for shot_set in shot_sets
            if shot_set.get("display_type")
        }
        for locale, shot_sets in sets_by_locale.items()
    }

    if display_types_filter:
        display_types = list(dict.fromkeys(display_types_filter))
    else:
        display_types = sorted(
            {display_type for per_type in indexed.values() for display_type in per_type}
        )

    expected_by_type: dict[str, int] = {}
    for display_type in display_types:
        if expected_count is not None:
            expected_by_type[display_type] = expected_count
            continue
        observed = (
            len(_usable(per_type[display_type][1]))
            for per_type in indexed.values()
            if display_type in per_type
        )
        expected_by_type[display_type] = max(max(observed, default=0), 1)

    locale_rows: list[LocaleScreenshotStatus] = []
    gaps: list[ScreenshotGap] = []
    total = 0
    for locale in sorted(indexed):
        statuses: list[DisplayTypeScreenshotStatus] = []
        locale_total = 0
        locale_complete = True
        for display_type in display_types:
            set_id, screenshots = indexed[locale].get(display_type, (None, []))
            count = len(_usable(screenshots))
            expected = expected_by_type[display_type]
            missing = max(expected - count, 0)
            complete = missing == 0
            locale_total += count
            locale_complete = locale_complete and complete
            statuses.append(
                DisplayTypeScreenshotStatus(
                    display_type=display_type,
                    set_id=set_id,
                    count=count,
                    expected=expected,
                    missing=missing,
                    complete=complete,
                    failed=[
                        s["id"]
                        for s in screenshots
                        if s.get("state") == ASSET_STATE_FAILED
                    ],
                    screenshots=(
                        [_to_schema(s, display_type) for s in screenshots]
                        if include_assets
                        else []
                    ),
                )
            )
            if not complete:
                gaps.append(
                    ScreenshotGap(
                        locale=locale,
                        display_type=display_type,
                        count=count,
                        expected=expected,
                        missing=missing,
                    )
                )
        total += locale_total
        locale_rows.append(
            LocaleScreenshotStatus(
                locale=locale,
                localization_id=localizations[locale],
                total=locale_total,
                complete=locale_complete,
                display_types=statuses,
            )
        )

    return VersionScreenshotInventory(
        app_id=app_id,
        version_id=version.id,
        version_state=version.state,
        version_string=version.version_string,
        locales=locale_rows,
        display_types=display_types,
        expected_by_display_type=expected_by_type,
        total_screenshots=total,
        gaps=gaps,
        complete=not gaps,
    )


# ==================================================================
# Main product-page screenshots
# ==================================================================


@mcp.tool(name="screenshots_list")
async def list_version_screenshots(
    app_id: int,
    locales: list[str] | None = None,
    display_types: list[str] | None = None,
    expected_count: int | None = None,
    include_assets: bool = False,
) -> VersionScreenshotInventory:
    """Count the MAIN product page's screenshots per locale x display type.

    The resume/repair primitive for a bulk localized screenshot run: it answers
    "which locales are short, and by how many" from the API, instead of opening
    every locale in App Store Connect after Apple rejects the version at submit
    time. Reads the app's **editable** App Store version; a live or locked
    version fails with a message naming its state.

    Args:
        app_id: The local app id.
        locales: Restrict the report to these locales (default: every locale
            on the editable version).
        display_types: Restrict to these device families, and report them even
            where no set exists yet (default: every family any locale uses).
        expected_count: Pin the per-locale target. Default: the highest count
            any locale has for that device family (at least 1) — so the locales
            that finished define what the short ones are missing.
        include_assets: Include each screenshot's id / file name / CDN url /
            delivery state. Off by default: a 40-locale report is a counting
            tool, and the assets multiply its size several-fold.

    Returns:
        A :class:`VersionScreenshotInventory` whose ``gaps`` list is the
        repair worklist and whose ``complete`` flag answers "is this version
        submittable?". ``count`` excludes assets Apple marked ``FAILED``.
    """
    for display_type in display_types or []:
        _require_display_type(display_type)

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCVersionScreenshotService(client)
            async with _asc_tool_error():
                version = await service.resolve_editable_version(app.asc_app_id)
                localizations = await service.localizations_by_locale(version.id)
                if locales:
                    unknown = [loc for loc in locales if loc not in localizations]
                    if unknown:
                        raise ToolError(
                            "Not a locale on App Store version "
                            f"{version.version_string or version.id}: "
                            f"{', '.join(unknown)}."
                        )
                    wanted = {loc: localizations[loc] for loc in locales}
                else:
                    wanted = localizations

                sets_by_locale: dict[str, list[dict]] = {}
                for locale, localization_id in wanted.items():
                    sets_by_locale[locale] = await service.get_screenshot_sets(
                        localization_id
                    )

    return _build_inventory(
        app_id=app_id,
        version=version,
        localizations=wanted,
        sets_by_locale=sets_by_locale,
        display_types_filter=display_types,
        expected_count=expected_count,
        include_assets=include_assets,
    )


@mcp.tool(name="screenshots_upload")
async def upload_version_screenshot(
    app_id: int,
    locale: str,
    display_type: str,
    file_base64: str,
    file_name: str,
    position: int | None = None,
) -> ScreenshotUploadResult:
    """Upload one screenshot to the MAIN product page for a locale.

    Resolves the editable App Store version's localization for ``locale``,
    finds (or creates) the ``display_type`` set, and runs the reserve -> PUT ->
    commit flow — then **reads the asset back** and reports the delivery state
    Apple confirms, not the HTTP status.

    Idempotent per ``(locale, display_type, position)``: an existing screenshot
    in the target slot is deleted before the new one is uploaded and moved into
    that slot, so a resumed bulk run replaces rather than doubling a locale.
    With ``position`` omitted, a screenshot with the same ``file_name`` is
    replaced in place; otherwise the new asset is appended.

    Args:
        app_id: The local app id.
        locale: The App Store locale (e.g. ``de-DE``). It must already exist on
            the editable version — create it with ``metadata_create_locale``.
        display_type: Apple's ``screenshotDisplayType`` (e.g. ``APP_IPHONE_67``).
        file_base64: The PNG/JPEG bytes, base64-encoded.
        file_name: The file name to register with Apple.
        position: 0-based slot in the set. Omit to replace by file name, or
            append when the name is new.

    Returns:
        A :class:`ScreenshotUploadResult`; ``verified`` is true only when Apple
        reports the asset ``COMPLETE``, otherwise ``warning`` names the state.
    """
    _require_display_type(display_type)
    if position is not None and position < 0:
        raise ToolError("position must be 0 or greater.")
    try:
        file_bytes = decode_screenshot_payload(file_base64)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCVersionScreenshotService(client)
            async with _asc_tool_error():
                _version_id, localization_id = await _resolve_localization(
                    service, app.asc_app_id, locale
                )
                set_id = await service.ensure_screenshot_set(
                    localization_id, display_type
                )
                existing = await service.list_set_screenshots(set_id)

                if position is None:
                    same_name = next(
                        (
                            index
                            for index, shot in enumerate(existing)
                            if shot.get("file_name") == file_name
                        ),
                        None,
                    )
                    target = same_name if same_name is not None else len(existing)
                else:
                    if position > len(existing):
                        raise ToolError(
                            f"position {position} is past the end of the "
                            f"{display_type} set for {locale}, which holds "
                            f"{len(existing)} screenshot(s)."
                        )
                    target = position

                replaced_id: str | None = None
                if target < len(existing):
                    replaced_id = existing[target]["id"]
                    # Delete first: Apple caps a set at MAX_SCREENSHOT_FILES,
                    # so a replace-by-upload-then-delete would fail on a full
                    # set — and a resumed run must not double the locale.
                    await service.delete_screenshot(replaced_id)
                    existing.pop(target)

                if len(existing) >= MAX_SCREENSHOT_FILES:
                    raise ToolError(
                        f"The {display_type} set for {locale} already holds "
                        f"{MAX_SCREENSHOT_FILES} screenshots (Apple's cap). "
                        "Delete one first with screenshots_delete."
                    )

                resource = await service.upload_to_set(set_id, file_bytes, file_name)
                screenshot_id = resource.get("id", "")
                if not screenshot_id:
                    # An id-less commit response would make the read-back GET
                    # ``/appScreenshots/`` (the collection) and the reorder
                    # PATCH an empty slot — say so instead of "verified".
                    raise ToolError(
                        f"ASC returned no screenshot id for {file_name} in the "
                        f"{display_type} set of {locale}; re-run "
                        "screenshots_list to see what landed."
                    )

                if target != len(existing):
                    order = [shot["id"] for shot in existing]
                    order.insert(target, screenshot_id)
                    await service.reorder_set(set_id, order)

                shaped, verified, warning = await _verify_upload(service, screenshot_id)

    return ScreenshotUploadResult(
        locale=locale,
        localization_id=localization_id,
        display_type=display_type,
        set_id=set_id,
        position=target,
        replaced_screenshot_id=replaced_id,
        screenshot=_to_schema(shaped, display_type),
        verified=verified,
        warning=warning,
    )


@mcp.tool(name="screenshots_delete")
async def delete_version_screenshots(
    app_id: int,
    locale: str,
    display_type: str,
    screenshot_id: str | None = None,
    position: int | None = None,
    delete_all: bool = False,
    prune_empty_set: bool = True,
) -> ScreenshotDeleteResult:
    """Delete screenshot(s) from the MAIN product page for a locale.

    Pass exactly one selector: ``screenshot_id``, ``position``, or
    ``delete_all=True`` (the whole device family, for replacing a wrong set).
    Emptying a set prunes it by default — an empty set is a *configured but
    incomplete* device family, which is what Apple rejects at submit time.

    Args:
        app_id: The local app id.
        locale: The App Store locale (e.g. ``de-DE``).
        display_type: Apple's ``screenshotDisplayType`` (e.g. ``APP_IPHONE_67``).
        screenshot_id: Delete this specific ``appScreenshots`` id.
        position: Delete the screenshot in this 0-based slot.
        delete_all: Delete every screenshot in the set.
        prune_empty_set: Also delete the set once it is empty (default true).

    Returns:
        A :class:`ScreenshotDeleteResult` with the deleted ids, whether the set
        was pruned, and how many screenshots remain.
    """
    _require_display_type(display_type)
    selectors = [screenshot_id is not None, position is not None, delete_all]
    if sum(selectors) != 1:
        raise ToolError(
            "Pass exactly one of screenshot_id, position, or delete_all=True."
        )
    if position is not None and position < 0:
        raise ToolError("position must be 0 or greater.")

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCVersionScreenshotService(client)
            async with _asc_tool_error():
                _version_id, localization_id = await _resolve_localization(
                    service, app.asc_app_id, locale
                )
                shot_set = await service.find_screenshot_set(
                    localization_id, display_type
                )
                if shot_set is None:
                    raise ToolError(
                        f"{locale} has no {display_type} screenshot set on the "
                        "editable version — nothing to delete."
                    )
                set_id = shot_set["id"]
                existing = await service.list_set_screenshots(set_id)

                if delete_all:
                    targets = list(existing)
                elif position is not None:
                    if position >= len(existing):
                        raise ToolError(
                            f"position {position} is out of range for the "
                            f"{display_type} set of {locale}, which holds "
                            f"{len(existing)} screenshot(s)."
                        )
                    targets = [existing[position]]
                else:
                    match = next(
                        (s for s in existing if s["id"] == screenshot_id), None
                    )
                    if match is None:
                        raise ToolError(
                            f"Screenshot {screenshot_id} is not in the "
                            f"{display_type} set for {locale}."
                        )
                    targets = [match]

                for shot in targets:
                    await service.delete_screenshot(shot["id"])

                remaining = len(existing) - len(targets)
                deleted_set = False
                if remaining == 0 and prune_empty_set:
                    await service.delete_set(set_id)
                    deleted_set = True

    return ScreenshotDeleteResult(
        locale=locale,
        display_type=display_type,
        set_id=set_id,
        deleted_screenshot_ids=[shot["id"] for shot in targets],
        deleted_set=deleted_set,
        remaining=remaining,
    )


# ==================================================================
# Visual compare (CPP vs default page)
# ==================================================================


@mcp.tool(name="screenshots_compare")
async def compare_screenshots(
    app_id: int,
    cpp_id: str,
    locale: str,
    display_type: str,
) -> Image:
    """Composite a BEFORE/AFTER screenshot montage for a CPP vs the default page.

    Args:
        app_id: The local app id.
        cpp_id: The Custom Product Page id whose screenshots are the
            "after" (new) side of the comparison.
        locale: The App Store locale (e.g. ``en-US``).
        display_type: Apple's ``screenshotDisplayType`` (e.g.
            ``APP_IPHONE_67``) selecting the device family.

    Returns:
        A FastMCP :class:`Image` wrapping the composited PNG.
    """
    _require_display_type(display_type)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            try:
                png_bytes = await build_comparison(
                    client,
                    app.asc_app_id,
                    cpp_id,
                    locale,
                    display_type,
                )
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")
            except Exception:
                logger.exception(
                    "compare montage build failed for app=%s cpp=%s",
                    app_id,
                    cpp_id,
                )
                raise ToolError("Could not build the comparison montage.")
    return Image(data=png_bytes, format="png")
