"""MCP tools for the metadata editor surface.

Thin wrappers over ``app/api/v1/metadata.py``. Each tool re-uses the same
service classes (``MetadataSnapshotService``, ``BulkMetadataService``,
``ASCMetadataService``, ``AnthropicTranslator``) so behaviour stays in one
place.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_asc_client_for_app
from app.api.v1.metadata import (
    _FIELDS_BY_KIND,
    _TRANSLATABLE_FIELDS,
    _attrs_for_asc,
    _build_snapshot_out,
    _get_state_row,
    _load_localization,
    _validate_kind,
)
from app.core.config import settings
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.keyword import KeywordTracking
from app.models.metadata import AppMetadataLocalization
from app.schemas.metadata import (
    AppMetadataLocalizationOut,
    AppMetadataSnapshotOut,
    BulkApplyIn,
    BulkApplyOut,
    BulkApplyResult,
    BulkPreviewIn,
    BulkPreviewOut,
    KeywordCoverageItem,
    KeywordCoverageOut,
    LocaleUpsertIn,
    TranslateIn,
    TranslateOut,
    TranslateSuggestionItem,
)
from app.services.asc.errors import ASCAPIError
from app.services.metadata.bulk import BulkMetadataService
from app.services.metadata.client import (
    ASCMetadataService,
    MetadataNotEditableError,
)
from app.services.metadata.coloring import classify_keyword
from app.services.metadata.snapshot import MetadataSnapshotService
from app.services.metadata.translate import (
    AnthropicTranslator,
    TranslationQuotaExceededError,
    translate_with_cache,
)

logger = logging.getLogger(__name__)


def _kind_or_raise(kind: str) -> None:
    """Validate the kind argument; convert HTTPException into ToolError."""
    try:
        _validate_kind(kind)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


# ==================================================================
# Snapshot read / sync
# ==================================================================


@mcp.tool(name="metadata.get_snapshot")
async def get_snapshot(app_id: int) -> AppMetadataSnapshotOut | None:
    """Return the cached metadata snapshot, or None if never synced."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        return await _build_snapshot_out(session, app_id)


@mcp.tool(name="metadata.sync")
async def sync_metadata(app_id: int) -> AppMetadataSnapshotOut:
    """Pull AppInfo + AppStoreVersion locales from ASC and upsert."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        async with await _get_asc_client_for_app(app, session) as client:
            asc_meta = ASCMetadataService(client)
            snapshot_service = MetadataSnapshotService(asc_meta, session)
            try:
                await snapshot_service.sync_app(app)
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")

        snapshot = await _build_snapshot_out(session, app_id)
        if snapshot is None:
            raise ToolError("Snapshot missing after sync")
        return snapshot


# ==================================================================
# Single-locale CRUD
# ==================================================================


@mcp.tool(name="metadata.get_locale")
async def get_locale(
    app_id: int, kind: str, locale: str,
) -> AppMetadataLocalizationOut:
    """Return a single cached locale row from the snapshot.

    Convenience accessor over the snapshot — same data, filtered to one
    ``(kind, locale)`` pair. ``kind`` is ``app_info`` or ``version``.
    """
    _kind_or_raise(kind)
    async with session_scope() as session:
        await resolve_app(app_id, session)
        row = await _load_localization(session, app_id, kind, locale)
        if row is None:
            raise ToolError(f"No {kind} localization for locale {locale!r}")
        return AppMetadataLocalizationOut.model_validate(row)


@mcp.tool(name="metadata.create_locale")
async def create_locale(
    app_id: int,
    kind: str,
    locale: str,
    fields: dict[str, Any],
) -> AppMetadataLocalizationOut:
    """Create a new locale row for either AppInfo or AppStoreVersion.

    ``fields`` is a partial :class:`LocaleUpsertIn` payload (any of
    name, subtitle, privacy_policy_url, description, keywords,
    promotional_text, whats_new, marketing_url, support_url).
    """
    _kind_or_raise(kind)
    body = LocaleUpsertIn.model_validate(fields)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)

        state = await _get_state_row(session, app_id)
        if state is None:
            raise ToolError("Sync metadata first via metadata.sync")
        parent_id = (
            state.app_info_id if kind == "app_info" else state.editable_version_id
        )
        if parent_id is None:
            raise ToolError(
                f"No editable {kind} parent available; re-sync metadata"
            )

        attrs = _attrs_for_asc(kind, body)

        async with await _get_asc_client_for_app(app, session) as client:
            asc_meta = ASCMetadataService(client)
            try:
                if kind == "app_info":
                    created = await asc_meta.create_app_info_localization(
                        parent_id, locale, attrs,
                    )
                else:
                    created = await asc_meta.create_version_localization(
                        parent_id, locale, attrs,
                    )
            except MetadataNotEditableError as exc:
                raise ToolError(str(exc))
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")

            snapshot_service = MetadataSnapshotService(asc_meta, session)
            try:
                await snapshot_service.sync_app(app)
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error during snapshot resync: {exc.message}")

        row = await _load_localization(session, app_id, kind, locale)
        if row is None:
            attrs_returned = (created.get("attributes") or {})
            return AppMetadataLocalizationOut(
                id=0,
                app_id=app_id,
                kind=kind,  # type: ignore[arg-type]
                asc_localization_id=created.get("id", ""),
                asc_parent_id=parent_id,
                locale=locale,
                name=attrs_returned.get("name"),
                subtitle=attrs_returned.get("subtitle"),
                privacy_policy_url=attrs_returned.get("privacyPolicyUrl"),
                description=attrs_returned.get("description"),
                keywords=attrs_returned.get("keywords"),
                promotional_text=attrs_returned.get("promotionalText"),
                whats_new=attrs_returned.get("whatsNew"),
                marketing_url=attrs_returned.get("marketingUrl"),
                support_url=attrs_returned.get("supportUrl"),
                synced_at=state.last_synced_at,
            )
        return AppMetadataLocalizationOut.model_validate(row)


@mcp.tool(name="metadata.update_locale")
async def update_locale(
    app_id: int,
    kind: str,
    locale: str,
    fields: dict[str, Any],
) -> AppMetadataLocalizationOut:
    """Patch an existing locale row. ``fields`` is a partial LocaleUpsertIn."""
    _kind_or_raise(kind)
    body = LocaleUpsertIn.model_validate(fields)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        row = await _load_localization(session, app_id, kind, locale)
        if row is None:
            raise ToolError(f"No {kind} localization for locale {locale!r}")

        attrs = _attrs_for_asc(kind, body)
        if not attrs:
            return AppMetadataLocalizationOut.model_validate(row)

        state = await _get_state_row(session, app_id)
        version_state = state.editable_version_state if state else None

        async with await _get_asc_client_for_app(app, session) as client:
            asc_meta = ASCMetadataService(client)
            try:
                if kind == "app_info":
                    await asc_meta.update_app_info_localization(
                        row.asc_localization_id, attrs,
                    )
                else:
                    await asc_meta.update_version_localization(
                        row.asc_localization_id,
                        attrs,
                        version_state=version_state,
                    )
            except MetadataNotEditableError as exc:
                raise ToolError(str(exc))
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")

        set_fields = body.model_dump(exclude_unset=True)
        allowed = set(_FIELDS_BY_KIND[kind])
        for snake, value in set_fields.items():
            if snake in allowed:
                setattr(row, snake, value)

        await session.flush()
        await session.refresh(row)
        return AppMetadataLocalizationOut.model_validate(row)


@mcp.tool(name="metadata.delete_locale")
async def delete_locale(
    app_id: int, kind: str, locale: str,
) -> dict[str, bool]:
    """Delete a locale row from ASC and the local snapshot."""
    _kind_or_raise(kind)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        row = await _load_localization(session, app_id, kind, locale)
        if row is None:
            raise ToolError(f"No {kind} localization for locale {locale!r}")

        async with await _get_asc_client_for_app(app, session) as client:
            asc_meta = ASCMetadataService(client)
            try:
                if kind == "app_info":
                    await asc_meta.delete_app_info_localization(
                        row.asc_localization_id,
                    )
                else:
                    await asc_meta.delete_version_localization(
                        row.asc_localization_id,
                    )
            except MetadataNotEditableError as exc:
                raise ToolError(str(exc))
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")

        await session.delete(row)
        return {"deleted": True}


# ==================================================================
# Bulk fan-out
# ==================================================================


@mcp.tool(name="metadata.bulk_preview")
async def bulk_preview(
    app_id: int,
    field: str,
    target_locales: list[str],
    value: str | None = None,
) -> BulkPreviewOut:
    """Compute a per-locale diff for a bulk fan-out (no ASC writes)."""
    body = BulkPreviewIn(field=field, value=value, target_locales=target_locales)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            bulk = BulkMetadataService(ASCMetadataService(client), session)
            try:
                items = await bulk.preview(
                    app, body.field, body.value, body.target_locales,
                )
            except ValueError as exc:
                raise ToolError(str(exc))
        return BulkPreviewOut(items=items)


@mcp.tool(name="metadata.bulk_apply")
async def bulk_apply(
    app_id: int,
    field: str,
    target_locales: list[str],
    value: str | None = None,
    force: bool = False,
) -> BulkApplyOut:
    """Replay a bulk plan against ASC and persist the snapshot deltas."""
    body = BulkApplyIn(
        field=field, value=value, target_locales=target_locales, force=force,
    )
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            bulk = BulkMetadataService(ASCMetadataService(client), session)
            try:
                results: list[BulkApplyResult] = await bulk.apply(
                    app, body.field, body.value, body.target_locales,
                    force=body.force,
                )
            except ValueError as exc:
                raise ToolError(str(exc))
            except MetadataNotEditableError as exc:
                raise ToolError(str(exc))

        applied = sum(1 for r in results if r.status == "applied")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")
        return BulkApplyOut(
            applied=applied,
            skipped=skipped,
            failed=failed,
            results=results,
        )


# ==================================================================
# AI translation suggestions
# ==================================================================


@mcp.tool(name="metadata.translate")
async def translate_metadata(
    app_id: int,
    source_locale: str,
    target_locales: list[str],
    fields: list[str],
) -> TranslateOut:
    """Suggest Claude translations for (target_locale x field) pairs.

    Source text is read from the snapshot at ``(source_locale, field)``;
    fields with no source text are silently skipped. Translations are
    returned for review only — never auto-applied to ASC.
    """
    body = TranslateIn(
        source_locale=source_locale,
        target_locales=target_locales,
        fields=fields,
    )
    async with session_scope() as session:
        await resolve_app(app_id, session)

        if not settings.ANTHROPIC_API_KEY:
            raise ToolError(
                "AI translation not configured. Set ANTHROPIC_API_KEY."
            )

        bad = [f for f in body.fields if f not in _TRANSLATABLE_FIELDS]
        if bad:
            raise ToolError(
                f"Fields not translatable: {bad}. "
                f"Translatable fields: {sorted(_TRANSLATABLE_FIELDS)}"
            )

        src_rows_result = await session.execute(
            select(AppMetadataLocalization).where(
                AppMetadataLocalization.app_id == app_id,
                AppMetadataLocalization.locale == body.source_locale,
            )
        )
        src_by_kind: dict[str, AppMetadataLocalization] = {
            r.kind: r for r in src_rows_result.scalars().all()
        }

        def _source_for(field: str) -> str | None:
            for k, kind_fields in _FIELDS_BY_KIND.items():
                if field in kind_fields:
                    row = src_by_kind.get(k)
                    if row is None:
                        return None
                    return getattr(row, field, None)
            return None

        translator = AnthropicTranslator(api_key=settings.ANTHROPIC_API_KEY)
        items: list[TranslateSuggestionItem] = []

        try:
            for target_locale in body.target_locales:
                for field in body.fields:
                    source_text = _source_for(field)
                    if not source_text:
                        continue
                    translation, cached = await translate_with_cache(
                        translator=translator,
                        session=session,
                        app_id=app_id,
                        text=source_text,
                        source_locale=body.source_locale,
                        target_locale=target_locale,
                        field_kind=field,  # type: ignore[arg-type]
                    )
                    items.append(
                        TranslateSuggestionItem(
                            locale=target_locale,
                            field=field,
                            suggestion=translation,
                            cached=cached,
                        )
                    )
        except TranslationQuotaExceededError as exc:
            raise ToolError(str(exc))

        return TranslateOut(items=items)


# ==================================================================
# Keyword coverage
# ==================================================================


@mcp.tool(name="metadata.keyword_coverage")
async def keyword_coverage(app_id: int) -> KeywordCoverageOut:
    """Classify each tracked keyword against each snapshot locale.

    Combines AppInfo (name/subtitle) and AppStoreVersion (keywords) per
    locale so ``classify_keyword`` can apply the title>subtitle>keywords
    precedence rule.
    """
    async with session_scope() as session:
        await resolve_app(app_id, session)

        trackings_result = await session.execute(
            select(KeywordTracking)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == app_id)
        )
        trackings = trackings_result.scalars().all()
        if not trackings:
            return KeywordCoverageOut(items=[])

        rows_result = await session.execute(
            select(AppMetadataLocalization).where(
                AppMetadataLocalization.app_id == app_id,
            )
        )
        rows = rows_result.scalars().all()
        if not rows:
            return KeywordCoverageOut(items=[])

        by_locale: dict[str, dict[str, str | None]] = {}
        for r in rows:
            bucket = by_locale.setdefault(
                r.locale, {"name": None, "subtitle": None, "keywords": None},
            )
            if r.kind == "app_info":
                bucket["name"] = r.name
                bucket["subtitle"] = r.subtitle
            else:
                bucket["keywords"] = r.keywords

        items: list[KeywordCoverageItem] = []
        for tracking in trackings:
            keyword_text = tracking.keyword.text
            for locale, fields in by_locale.items():
                placement = classify_keyword(
                    keyword_text,
                    fields["name"],
                    fields["subtitle"],
                    fields["keywords"],
                )
                items.append(
                    KeywordCoverageItem(
                        keyword=keyword_text,
                        locale=locale,
                        placement=placement,
                    )
                )
        return KeywordCoverageOut(items=items)
