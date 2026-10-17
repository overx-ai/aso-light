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
    SyncBinder,
    asc_tool_error,
    check_delete_selectors,
    delete_from_localization,
    run_screenshot_sync,
)
from app.models.app import App
from app.schemas.cpp import (
    CPPEnsureLocalizationResponse,
    CPPKeywordsResponse,
    CPPKeywordsSetResult,
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
from app.services.asc.cpp import (
    ASCCustomProductPageService,
    CPPScreenshotService,
    CPPSource,
    CPPVersionNotEditableError,
    plan_search_keywords,
)
from app.services.asc.screenshots import (
    ASCVersionScreenshotService,
    SyncTarget,
    locales_of,
)
from app.services.metadata.validation import FIELD_CHAR_LIMITS

logger = logging.getLogger(__name__)


PROMOTIONAL_TEXT_LIMIT = FIELD_CHAR_LIMITS["promotional_text"]


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
    app_id: int, cpp_id: str, locale: str, promotional_text: str | None = None,
) -> CPPEnsureLocalizationResponse:
    """Resolve (or create) a CPP localization, returning its ``localization_id``.

    ``cpp_upload_screenshot`` needs an ``appCustomProductPageLocalizations`` id,
    which otherwise requires manually walking the CPP's versions then
    localizations. This resolves the CPP's editable (draft) version, reuses the
    localization whose ``locale`` matches, and creates one if absent — so a
    multi-locale CPP can be populated one locale at a time. Idempotent: repeat
    calls for the same ``(cpp_id, locale)`` return the same ``localization_id``.

    ``promotional_text`` (at most 170 characters) is written to that
    localization; omit it to leave the text as it is. A page whose only
    version is in review or live is refused, as for every CPP write.
    """
    if promotional_text is not None and len(promotional_text) > PROMOTIONAL_TEXT_LIMIT:
        raise ToolError(
            f"promotional_text is {len(promotional_text)} characters; Apple's "
            f"limit is {PROMOTIONAL_TEXT_LIMIT}."
        )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                version_id = await service.get_editable_version_id(cpp_id)
                localization_id = await service.find_or_create_localization_id(
                    version_id, locale,
                )
                written = None
                if promotional_text is not None:
                    updated = await service.update_localization(
                        localization_id, promotional_text
                    )
                    written = (updated.get("attributes") or {}).get(
                        "promotionalText", promotional_text
                    )
        return CPPEnsureLocalizationResponse(
            cpp_id=cpp_id,
            version_id=version_id,
            localization_id=localization_id,
            locale=locale,
            promotional_text=written,
        )


async def _editable_localization_id(
    cpp: ASCCustomProductPageService, cpp_id: str, locale: str
) -> str:
    """The ``locale`` localization on the CPP's editable version."""
    version = await cpp.get_editable_version(cpp_id)
    localizations = locales_of(await cpp.list_localizations(version.id))
    localization_id = localizations.get(locale)
    if localization_id is None:
        known = ", ".join(sorted(localizations)) or "none"
        raise ToolError(
            f"Custom Product Page {cpp_id} has no '{locale}' "
            f"localization. Existing locales: {known}."
        )
    return localization_id


# ==================================================================
# Search keywords (spec 017)
# ==================================================================


@mcp.tool(name="cpp_keywords_get")
async def get_cpp_keywords(app_id: int, cpp_id: str, locale: str) -> CPPKeywordsResponse:
    """A CPP localization's search keywords, and the ones Apple offers for it.

    A Custom Product Page can rank in organic search for keywords assigned to
    it. Reads the page's editable version when it has one, else the version
    that holds ``locale``. ``available`` lists the ``appKeywords`` ids Apple
    offers the app for ``locale``: only those can be assigned.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                try:
                    localization_id = await _editable_localization_id(
                        service, cpp_id, locale
                    )
                except CPPVersionNotEditableError:
                    localization_id = await service.get_cpp_localization_id(
                        cpp_id, locale
                    )
                    if localization_id is None:
                        raise ToolError(
                            f"Custom Product Page {cpp_id} has no '{locale}' localization."
                        )
                keywords = await service.list_search_keyword_ids(localization_id)
                available = await service.list_app_keyword_ids(app.asc_app_id, locale)
    return CPPKeywordsResponse(
        cpp_id=cpp_id,
        locale=locale,
        localization_id=localization_id,
        keywords=keywords,
        available=available,
    )


@mcp.tool(name="cpp_keywords_set")
async def set_cpp_keywords(
    app_id: int,
    cpp_id: str,
    locale: str,
    keywords: list[str],
    apply: bool = False,
) -> CPPKeywordsSetResult:
    """Make ``keywords`` the search keywords of a CPP localization.

    Diffs the list against the localization on the page's editable version and
    returns ``add`` / ``remove`` / ``keep`` with one row per keyword. Only a
    keyword Apple offers for the locale (see ``cpp_keywords_get``) can be
    assigned; any other is an ``error`` row, and nothing is written while one
    is. A keyword in a crowded 4.3(b) category (dating, horoscope, tarot, …)
    carries a ``warning`` but is not blocked. Dry run by default; ``apply``
    links and unlinks, then reads the set back into ``keywords``.
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            service = ASCCustomProductPageService(client)
            async with asc_tool_error():
                localization_id = await _editable_localization_id(service, cpp_id, locale)
                current = await service.list_search_keyword_ids(localization_id)
                offered = await service.list_app_keyword_ids(app.asc_app_id, locale)
                rows = plan_search_keywords(keywords, current, offered)
                by_action = {
                    action: [row.keyword for row in rows if row.action == action]
                    for action in ("add", "remove", "keep")
                }
                applied = apply and not any(row.action == "error" for row in rows)
                after = None
                if applied:
                    await service.link_search_keywords(
                        localization_id, by_action["add"], by_action["remove"]
                    )
                    after = await service.list_search_keyword_ids(localization_id)
    return CPPKeywordsSetResult(
        cpp_id=cpp_id,
        locale=locale,
        localization_id=localization_id,
        applied=applied,
        rows=rows,
        keywords=after,
        **by_action,
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


def cpp_target(
    cpp_id: str, service_type: type[CPPSource], *, check_families: bool
) -> SyncBinder:
    """Bind a sync to a CPP's editable version. A locale the page lacks may be
    created when it is one of the app's; ``check_families`` also loads the
    main listing's device families for ``missing_families``."""

    async def bind(client: ASCClient, app: App) -> SyncTarget:
        service = service_type(client)
        main = ASCVersionScreenshotService(client)
        version = await service.cpp.get_editable_version(cpp_id)
        return SyncTarget(
            service=service,
            version=version,
            localizations=await service.localizations_by_locale(version.id),
            label=f"app {app.name or app.asc_app_id}",
            creatable=await main.app_locales(app.asc_app_id),
            reference=await main.main_families(app.asc_app_id) if check_families else {},
        )

    return bind


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

    return await run_screenshot_sync(
        app_id,
        dir,
        cpp_target(cpp_id, CPPScreenshotService, check_families=True),
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
                localization_id = await _editable_localization_id(
                    service.cpp, cpp_id, locale
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
