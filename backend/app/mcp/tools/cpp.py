"""MCP tools for managing Custom Product Pages (CPP).

Thin wrappers over :class:`app.services.asc.cpp.ASCCustomProductPageService`.
Each tool resolves the local ``app_id`` to its owning credential via
``resolve_app`` (enforcing the ``app.credential_id -> credential.user_id``
chain), builds an :class:`ASCClient`, and converts ASC API failures into
``ToolError`` so MCP clients see a single-line message.
"""
from __future__ import annotations

import logging

from fastmcp import Context
from fastmcp.exceptions import ToolError

from app.api.v1._deps import _get_asc_client_for_app
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.mcp.tools.screenshots import (
    asc_tool_error,
    check_delete_selectors,
    delete_from_localization,
    run_screenshot_sync,
)
from app.models.app import App
from app.schemas.cpp import (
    CPPEnsureLocalizationResponse,
    CPPListResponse,
    CPPResponse,
    Screenshot,
    ScreenshotSet,
    ScreenshotSetListResponse,
)
from app.schemas.screenshots import (
    ScreenshotDeleteResult,
    ScreenshotSyncResult,
    decode_screenshot_payload,
    is_valid_display_type,
)
from app.services.asc.client import ASCClient
from app.services.asc.cpp import ASCCustomProductPageService, CPPScreenshotService
from app.services.asc.screenshots import ASCVersionScreenshotService, SyncTarget

logger = logging.getLogger(__name__)


def _to_cpp_response(resource: dict) -> CPPResponse:
    """Shape a raw ``appCustomProductPages`` resource into a CPPResponse."""
    attrs = resource.get("attributes", {})
    return CPPResponse(
        id=resource.get("id", ""),
        name=attrs.get("name"),
        visible=attrs.get("visible"),
    )


# ==================================================================
# Custom Product Pages — CRUD
# ==================================================================


@mcp.tool(name="cpp_list")
async def list_cpps(app_id: int) -> CPPListResponse:
    """List the Custom Product Pages for an app."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                resources = await service.list_cpps(app.asc_app_id)
        return CPPListResponse(
            items=[_to_cpp_response(r) for r in resources]
        )


@mcp.tool(name="cpp_get")
async def get_cpp(app_id: int, cpp_id: str) -> CPPResponse:
    """Fetch a single Custom Product Page by its ASC id."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                resource = await service.get_cpp(cpp_id)
        return _to_cpp_response(resource)


@mcp.tool(name="cpp_create")
async def create_cpp(
    app_id: int, name: str, locale: str = "en-US", visible: bool = True,
) -> CPPResponse:
    """Create a Custom Product Page.

    ASC requires the first version + a localization inline on create, so the
    page is seeded with a ``locale`` localization (default ``en-US``); add more
    locales with ``cpp_ensure_localization``.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                resource = await service.create_cpp(
                    app.asc_app_id, name, locale=locale, visible=visible,
                )
        return _to_cpp_response(resource)


@mcp.tool(name="cpp_update")
async def update_cpp(
    app_id: int,
    cpp_id: str,
    name: str | None = None,
    visible: bool | None = None,
) -> CPPResponse:
    """Update a Custom Product Page's name and/or visibility."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                try:
                    resource = await service.update_cpp(
                        cpp_id, name=name, visible=visible,
                    )
                except ValueError as exc:
                    raise ToolError(str(exc))
        return _to_cpp_response(resource)


@mcp.tool(name="cpp_delete")
async def delete_cpp(app_id: int, cpp_id: str) -> dict[str, bool]:
    """Delete a Custom Product Page."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                await service.delete_cpp(cpp_id)
        return {"deleted": True}


# ==================================================================
# Localizations
# ==================================================================


@mcp.tool(name="cpp_ensure_localization")
async def ensure_cpp_localization(
    app_id: int, cpp_id: str, locale: str,
) -> CPPEnsureLocalizationResponse:
    """Resolve (or create) a CPP localization, returning its ``localization_id``.

    ``cpp_upload_screenshot`` needs an ``appCustomProductPageLocalizations`` id,
    which otherwise requires manually walking the CPP's versions then
    localizations. This resolves the CPP's editable (draft) version, reuses the
    localization whose ``locale`` matches, and creates one if absent — so a
    multi-locale CPP can be populated one locale at a time. Idempotent: repeat
    calls for the same ``(cpp_id, locale)`` return the same ``localization_id``.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                version_id = await service.get_editable_version_id(cpp_id)
                localization_id = await service.find_or_create_localization_id(
                    version_id, locale,
                )
        return CPPEnsureLocalizationResponse(
            cpp_id=cpp_id,
            version_id=version_id,
            localization_id=localization_id,
            locale=locale,
        )


# ==================================================================
# Screenshots
# ==================================================================


@mcp.tool(name="cpp_list_screenshots")
async def list_cpp_screenshots(
    app_id: int, localization_id: str,
) -> ScreenshotSetListResponse:
    """List the screenshot sets (+ assets) for a CPP localization.

    ``localization_id`` is an ``appCustomProductPageLocalizations`` id —
    obtained by walking a CPP's versions then localizations.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                sets = await service.get_cpp_screenshots(localization_id)
        return ScreenshotSetListResponse(
            items=[
                ScreenshotSet(
                    id=s["id"],
                    display_type=s.get("display_type"),
                    screenshots=[
                        Screenshot(**shot) for shot in s.get("screenshots", [])
                    ],
                )
                for s in sets
            ]
        )


@mcp.tool(name="cpp_upload_screenshot")
async def upload_cpp_screenshot(
    app_id: int,
    localization_id: str,
    display_type: str,
    file_base64: str,
    file_name: str,
) -> Screenshot:
    """Upload a marketing screenshot to a CPP localization.

    Base64-decodes ``file_base64`` and runs the 3-step reserve -> PUT ->
    commit upload flow against the standard ``appScreenshotSets`` /
    ``appScreenshots`` model (a set for ``display_type`` is created on
    demand).

    Args:
        app_id: The local app id.
        localization_id: An ``appCustomProductPageLocalizations`` id (walk
            a CPP's versions then localizations to obtain it).
        display_type: Apple's ``screenshotDisplayType`` (e.g.
            ``APP_IPHONE_67``) identifying the device family.
        file_base64: The screenshot bytes, base64-encoded.
        file_name: The file name to register with Apple.

    Returns:
        The created :class:`Screenshot`.
    """
    if not is_valid_display_type(display_type):
        raise ToolError(f"Unknown display_type '{display_type}'.")
    try:
        file_bytes = decode_screenshot_payload(file_base64)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                await service.assert_localization_editable(localization_id)
                resource = await service.upload_screenshot_to_cpp(
                    localization_id,
                    display_type,
                    file_bytes,
                    file_name,
                )

    attrs = resource.get("attributes", {})
    return Screenshot(
        id=resource.get("id", ""),
        file_name=attrs.get("fileName") or file_name,
        display_type=display_type,
        source_url=ASCCustomProductPageService._build_source_url(
            attrs.get("imageAsset")
        ),
    )


@mcp.tool(name="cpp_screenshots_sync")
async def sync_cpp_screenshots(
    app_id: int,
    cpp_id: str,
    dir: str,  # noqa: A002 — the name callers and spec 015 use
    locales: list[str] | None = None,
    display_types: list[str] | None = None,
    apply: bool = False,
    ctx: Context | None = None,
) -> ScreenshotSyncResult:
    """Make a studio variant export (``<dir>/<locale>/NN.png``) a Custom Product Page's screenshots.

    ``screenshots_sync`` with the CPP's editable version as the target: the
    same directory rules, size table and ``SCREENSHOT_SYNC_ROOTS`` allowlist,
    each display type replaced as a unit in filename order, unchanged slots
    skipped by MD5, other display types never touched. A locale directory the
    page has no localization for is planned as ``create_localization`` and
    created on apply; it must be one of the app's own locales.

    Dry run by default; with ``apply=True`` nothing is written while any row
    is an ``error``. A page whose only version is in review is refused. A row
    Apple fails twice during the apply carries ``error`` while the other rows
    still apply, as in ``screenshots_sync``.

    App Store Connect refuses to submit a page that lacks a device family the
    app ships, and a sync of one family reports clean regardless. So the reply
    carries ``missing_families``: per locale, the display types the app's
    editable main listing holds that this page still lacks after the sync
    (empty when complete). It never blocks an apply; sync the family too.

    Args:
        app_id: The local app id.
        cpp_id: The Custom Product Page id.
        dir: Absolute path under ``SCREENSHOT_SYNC_ROOTS``, e.g. a studio
            ``out/variants/<name>``.
        locales: Sync only these locale directories (default: every one).
        display_types: Sync only these device families (default: every family
            the files map to).
        apply: False (default) returns the plan and writes nothing.

    Returns:
        Rows per locale x display type (``skip`` | ``replace`` | ``upload`` |
        ``create_localization`` | ``error``); after an apply, the read-back
        ``count`` per row, the ``inventory`` with its gaps, and
        ``missing_families``.
    """

    async def bind(client: ASCClient, app: App) -> SyncTarget:
        service = CPPScreenshotService(client)
        main = ASCVersionScreenshotService(client)
        version = await service.cpp.get_editable_version(cpp_id)
        return SyncTarget(
            service=service,
            version=version,
            localizations=await service.localizations_by_locale(version.id),
            label=f"app {app.name or app.asc_app_id}",
            creatable=await main.app_locales(app.asc_app_id),
            reference=await main.main_families(app.asc_app_id),
        )

    return await run_screenshot_sync(
        app_id,
        dir,
        bind,
        locales=locales,
        display_types=display_types,
        apply=apply,
        ctx=ctx,
    )


@mcp.tool(name="cpp_screenshots_delete")
async def delete_cpp_screenshots(
    app_id: int,
    cpp_id: str,
    locale: str,
    display_type: str,
    screenshot_id: str | None = None,
    position: int | None = None,
    delete_all: bool = False,
    prune_empty_set: bool = False,
) -> ScreenshotDeleteResult:
    """Delete screenshot(s) from a Custom Product Page localization.

    ``screenshots_delete`` for a CPP's editable version. Pass exactly one
    selector: ``screenshot_id``, ``position`` (0-based) or ``delete_all=True``.
    ``prune_empty_set`` also deletes the set once it is empty. Consent-gated.
    """
    check_delete_selectors(display_type, screenshot_id, position, delete_all)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = CPPScreenshotService(client)
            async with asc_tool_error():
                version = await service.cpp.get_editable_version(cpp_id)
                localizations = await service.localizations_by_locale(version.id)
                localization_id = localizations.get(locale)
                if localization_id is None:
                    known = ", ".join(sorted(localizations)) or "none"
                    raise ToolError(
                        f"Custom Product Page {cpp_id} has no '{locale}' "
                        f"localization. Existing locales: {known}."
                    )
                return await delete_from_localization(
                    service,
                    localization_id,
                    locale,
                    display_type,
                    screenshot_id=screenshot_id,
                    position=position,
                    delete_all=delete_all,
                    prune_empty_set=prune_empty_set,
                )
