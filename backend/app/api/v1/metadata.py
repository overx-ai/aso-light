"""Metadata editor + cross-localization HTTP endpoints.

Two routers live in this module:

* ``router`` — mounted at ``/apps`` in ``app/api/v1/__init__.py``. Carries
  every per-app metadata endpoint (snapshot read/sync, single-locale
  CRUD, bulk fan-out preview/apply, AI translation, keyword coverage).
* ``keywords_extra_router`` — mounted at ``/keywords``. Carries the
  global cross-localization grid (territories x indexed-locales joined
  with ``Territory.gdp_per_capita_usd``). Not app-scoped.

Every per-app endpoint enforces ASC ownership via ``_get_verified_app``.
ASC-touching mutations follow the same ``async with await
_get_asc_client_for_app(...)`` lifecycle used by the pricing router so
the underlying httpx client is always closed.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.config import settings
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.app import App
from app.models.keyword import KeywordTracking
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.territory import Territory
from app.schemas.metadata import (
    AppMetadataLocalizationOut,
    AppMetadataSnapshotOut,
    AppMetadataStateOut,
    BulkApplyIn,
    BulkApplyOut,
    BulkApplyResult,
    BulkPreviewIn,
    BulkPreviewOut,
    CrossLocalizationGridItem,
    CrossLocalizationGridOut,
    KeywordCoverageItem,
    KeywordCoverageOut,
    LocaleUpsertIn,
    TranslateIn,
    TranslateOut,
    TranslateSuggestionItem,
)
from app.services.asc.errors import ASCAPIError
from app.services.keywords.cross_localization import CROSS_LOCALIZATION_DATA
from app.services.metadata.bulk import (
    APP_INFO_FIELDS as BULK_APP_INFO_FIELDS,
    FIELD_TO_ASC_ATTR,
    VERSION_FIELDS as BULK_VERSION_FIELDS,
    BulkMetadataService,
)
from app.services.metadata.client import (
    ASCMetadataService,
    MetadataNotEditableError,
)
from app.services.metadata.coloring import classify_keyword
from app.services.metadata.snapshot import MetadataSnapshotService
from app.services.metadata.translate import (
    FIELD_CHAR_LIMITS as TRANSLATE_FIELD_LIMITS,
    AnthropicTranslator,
    TranslationQuotaExceededError,
    translate_with_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metadata"])
keywords_extra_router = APIRouter(tags=["keywords"])

# Translator-supported field kinds (matches FieldKind Literal in translate.py).
_TRANSLATABLE_FIELDS: frozenset[str] = frozenset(TRANSLATE_FIELD_LIMITS.keys())

# Fields that live on each ``kind`` of localization row — used both to look up
# source text for translation and to project coverage from the snapshot.
_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "app_info": tuple(BULK_APP_INFO_FIELDS),
    "version": tuple(BULK_VERSION_FIELDS),
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _validate_kind(kind: str) -> None:
    """Validate the ``{kind}`` path parameter."""
    if kind not in {"app_info", "version"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="kind must be 'app_info' or 'version'",
        )


async def _get_state_row(
    session: AsyncSession, app_id: int,
) -> AppMetadataState | None:
    result = await session.execute(
        select(AppMetadataState).where(AppMetadataState.app_id == app_id)
    )
    return result.scalar_one_or_none()


async def _load_localization(
    session: AsyncSession, app_id: int, kind: str, locale: str,
) -> AppMetadataLocalization | None:
    result = await session.execute(
        select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app_id,
            AppMetadataLocalization.kind == kind,
            AppMetadataLocalization.locale == locale,
        )
    )
    return result.scalar_one_or_none()


async def _build_snapshot_out(
    session: AsyncSession, app_id: int,
) -> AppMetadataSnapshotOut | None:
    """Compose the combined snapshot payload from cached rows.

    Returns ``None`` when there is no ``AppMetadataState`` row yet — the
    caller (GET endpoint) translates that into a 204 so the UI can prompt
    the user to call ``POST /sync``.
    """
    state = await _get_state_row(session, app_id)
    if state is None:
        return None

    rows_result = await session.execute(
        select(AppMetadataLocalization)
        .where(AppMetadataLocalization.app_id == app_id)
        .order_by(AppMetadataLocalization.kind, AppMetadataLocalization.locale)
    )
    rows = rows_result.scalars().all()

    app_info_rows = [
        AppMetadataLocalizationOut.model_validate(r)
        for r in rows
        if r.kind == "app_info"
    ]
    version_rows = [
        AppMetadataLocalizationOut.model_validate(r)
        for r in rows
        if r.kind == "version"
    ]
    return AppMetadataSnapshotOut(
        app_info=app_info_rows,
        versions=version_rows,
        state=AppMetadataStateOut.from_model(state),
    )


def _attrs_for_asc(kind: str, body: LocaleUpsertIn) -> dict[str, Any]:
    """Project a :class:`LocaleUpsertIn` into the ASC ``attributes`` dict.

    Only fields appropriate for ``kind`` are included, and only fields the
    caller actually set (``model_dump(exclude_unset=True)``) are forwarded
    so we never accidentally clear an untouched field.
    """
    set_fields = body.model_dump(exclude_unset=True)
    allowed = set(_FIELDS_BY_KIND[kind])
    out: dict[str, Any] = {}
    for snake, value in set_fields.items():
        if snake not in allowed:
            continue
        asc_key = FIELD_TO_ASC_ATTR.get(snake)
        if asc_key is None:
            continue
        out[asc_key] = value
    return out


# ------------------------------------------------------------------
# Snapshot read / sync
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/metadata",
    response_model=AppMetadataSnapshotOut | None,
)
async def get_metadata_snapshot(
    app_id: int,
    response: Response,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppMetadataSnapshotOut | None:
    """Return the cached metadata snapshot.

    Returns ``204 No Content`` when the app has never been synced; the UI
    uses that signal to prompt the user to call ``POST /sync``.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    snapshot = await _build_snapshot_out(session, app_id)
    if snapshot is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return snapshot


@router.post(
    "/{app_id}/metadata/sync",
    response_model=AppMetadataSnapshotOut,
)
async def sync_metadata(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppMetadataSnapshotOut:
    """Pull AppInfo + AppStoreVersion locales from ASC and upsert."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        asc_meta = ASCMetadataService(client)
        snapshot_service = MetadataSnapshotService(asc_meta, session)
        try:
            await snapshot_service.sync_app(app)
        except ASCAPIError as exc:
            logger.warning("ASC sync failed for app %s: %s", app_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ASC API error",
            ) from exc

    await session.commit()
    snapshot = await _build_snapshot_out(session, app_id)
    if snapshot is None:
        # Defensive: sync_app always upserts a state row.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Snapshot missing after sync",
        )
    return snapshot


# ------------------------------------------------------------------
# Single-locale CRUD
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/metadata/{kind}/{locale}",
    response_model=AppMetadataLocalizationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_locale(
    app_id: int,
    kind: str,
    locale: str,
    body: LocaleUpsertIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppMetadataLocalizationOut:
    """Create a new locale row for either AppInfo or AppStoreVersion."""
    _validate_kind(kind)
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    state = await _get_state_row(session, app_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sync metadata first via POST /metadata/sync",
        )
    parent_id = state.app_info_id if kind == "app_info" else state.editable_version_id
    if parent_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No editable {kind} parent available; re-sync metadata",
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc
        except ASCAPIError as exc:
            logger.warning(
                "ASC create %s/%s for app %s failed: %s",
                kind, locale, app_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ASC API error",
            ) from exc

        # Re-sync to ensure the snapshot is consistent with ASC truth.
        snapshot_service = MetadataSnapshotService(asc_meta, session)
        try:
            await snapshot_service.sync_app(app)
        except ASCAPIError as exc:
            logger.warning(
                "Snapshot re-sync after create failed for app %s: %s",
                app_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ASC API error",
            ) from exc

    await session.commit()

    row = await _load_localization(session, app_id, kind, locale)
    if row is None:
        # Defensive: ASC just returned the resource; snapshot should have it.
        # Fall back to the ASC payload so the caller still gets a useful
        # response.
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


@router.patch(
    "/{app_id}/metadata/{kind}/{locale}",
    response_model=AppMetadataLocalizationOut,
)
async def update_locale(
    app_id: int,
    kind: str,
    locale: str,
    body: LocaleUpsertIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppMetadataLocalizationOut:
    """Patch an existing locale row."""
    _validate_kind(kind)
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    row = await _load_localization(session, app_id, kind, locale)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {kind} localization for locale {locale!r}",
        )

    attrs = _attrs_for_asc(kind, body)
    if not attrs:
        # Nothing to patch — return the row unchanged.
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc
        except ASCAPIError as exc:
            logger.warning(
                "ASC update %s/%s for app %s failed: %s",
                kind, locale, app_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ASC API error",
            ) from exc

    # Mirror the change into the snapshot so the next GET reflects it
    # without an extra round-trip.
    set_fields = body.model_dump(exclude_unset=True)
    allowed = set(_FIELDS_BY_KIND[kind])
    for snake, value in set_fields.items():
        if snake in allowed:
            setattr(row, snake, value)

    await session.commit()
    await session.refresh(row)
    return AppMetadataLocalizationOut.model_validate(row)


@router.delete(
    "/{app_id}/metadata/{kind}/{locale}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_locale(
    app_id: int,
    kind: str,
    locale: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a locale row from ASC and the snapshot."""
    _validate_kind(kind)
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    row = await _load_localization(session, app_id, kind, locale)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {kind} localization for locale {locale!r}",
        )

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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc
        except ASCAPIError as exc:
            logger.warning(
                "ASC delete %s/%s for app %s failed: %s",
                kind, locale, app_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ASC API error",
            ) from exc

    await session.delete(row)
    await session.commit()


# ------------------------------------------------------------------
# Bulk fan-out
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/metadata/bulk/preview",
    response_model=BulkPreviewOut,
)
async def bulk_preview(
    app_id: int,
    body: BulkPreviewIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BulkPreviewOut:
    """Compute a per-locale diff for a bulk fan-out. No ASC writes."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    # The bulk service does not need an ASC client for preview, but the
    # constructor takes one — pass a service backed by a short-lived client.
    async with await _get_asc_client_for_app(app, session) as client:
        bulk = BulkMetadataService(ASCMetadataService(client), session)
        try:
            items = await bulk.preview(
                app, body.field, body.value, body.target_locales,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
            ) from exc
    return BulkPreviewOut(items=items)


@router.post(
    "/{app_id}/metadata/bulk/apply",
    response_model=BulkApplyOut,
)
async def bulk_apply(
    app_id: int,
    body: BulkApplyIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BulkApplyOut:
    """Replay a bulk plan against ASC and persist the snapshot deltas."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        bulk = BulkMetadataService(ASCMetadataService(client), session)
        try:
            results: list[BulkApplyResult] = await bulk.apply(
                app, body.field, body.value, body.target_locales,
                force=body.force,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
            ) from exc
        except MetadataNotEditableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc

    await session.commit()

    applied = sum(1 for r in results if r.status == "applied")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    return BulkApplyOut(
        applied=applied,
        skipped=skipped,
        failed=failed,
        results=results,
    )


# ------------------------------------------------------------------
# AI translation suggestions
# ------------------------------------------------------------------


@router.post(
    "/{app_id}/metadata/translate",
    response_model=TranslateOut,
)
async def translate_metadata(
    app_id: int,
    body: TranslateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TranslateOut:
    """Suggest Claude translations for (target_locale x field) pairs.

    Source text is read from the snapshot row at ``(source_locale, field)``;
    if no source exists for a field on the source locale, that field is
    silently skipped. Translations are returned to the UI for review and
    are NEVER applied to ASC by this endpoint.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI translation not configured. Set ANTHROPIC_API_KEY.",
        )

    # Reject fields the translator cannot handle (URLs, etc.).
    bad = [f for f in body.fields if f not in _TRANSLATABLE_FIELDS]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Fields not translatable: {bad}. "
                f"Translatable fields: {sorted(_TRANSLATABLE_FIELDS)}"
            ),
        )

    # Load source-locale snapshot rows for both kinds so we can look up
    # source text for any requested field in a single query.
    src_rows_result = await session.execute(
        select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app_id,
            AppMetadataLocalization.locale == body.source_locale,
        )
    )
    src_rows = src_rows_result.scalars().all()
    src_by_kind: dict[str, AppMetadataLocalization] = {
        r.kind: r for r in src_rows
    }

    def _source_for(field: str) -> str | None:
        for kind, kind_fields in _FIELDS_BY_KIND.items():
            if field in kind_fields:
                row = src_by_kind.get(kind)
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
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    # Cache rows are added inside ``translate_with_cache``; persist them.
    await session.commit()
    return TranslateOut(items=items)


# ------------------------------------------------------------------
# Keyword coverage
# ------------------------------------------------------------------


@router.get(
    "/{app_id}/metadata/keyword-coverage",
    response_model=KeywordCoverageOut,
)
async def keyword_coverage(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KeywordCoverageOut:
    """Classify each tracked keyword against each snapshot locale.

    Combines AppInfo (name/subtitle) and AppStoreVersion (keywords) per
    locale so ``classify_keyword`` can apply the title>subtitle>keywords
    precedence rule. A locale that exists in only one of the two trees
    is still classified — missing fields are simply ``None``.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

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

    # Collapse (locale -> {name, subtitle, keywords}) across both kinds.
    by_locale: dict[str, dict[str, str | None]] = {}
    for r in rows:
        bucket = by_locale.setdefault(
            r.locale, {"name": None, "subtitle": None, "keywords": None},
        )
        if r.kind == "app_info":
            bucket["name"] = r.name
            bucket["subtitle"] = r.subtitle
        else:  # version
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


# ------------------------------------------------------------------
# Cross-localization grid (global, NOT app-scoped)
# ------------------------------------------------------------------


@keywords_extra_router.get(
    "/cross-localization-grid",
    response_model=CrossLocalizationGridOut,
)
async def cross_localization_grid(
    _current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CrossLocalizationGridOut:
    """Cross-localization grid joined with ``Territory.gdp_per_capita_usd``.

    The default UI sort is GDP per capita descending; we surface the raw
    number so the frontend (or any client) can sort however it likes.
    Territories absent from the static ``CROSS_LOCALIZATION_DATA`` table
    are not returned. Territories present in the static data but missing
    from our DB get a ``None`` GDP value rather than being dropped.
    """
    territory_codes = {
        entry["territory_code"] for entry in CROSS_LOCALIZATION_DATA
    }
    if not territory_codes:
        return CrossLocalizationGridOut(items=[])

    rows_result = await session.execute(
        select(Territory.code, Territory.gdp_per_capita_usd).where(
            Territory.code.in_(territory_codes),
        )
    )
    gdp_by_code: dict[str, float | None] = {
        code: gdp for code, gdp in rows_result.all()
    }

    items = [
        CrossLocalizationGridItem(
            territory_code=entry["territory_code"],
            locale=entry["locale"],
            gdp_per_capita_usd=gdp_by_code.get(entry["territory_code"]),
            has_metadata=False,
        )
        for entry in CROSS_LOCALIZATION_DATA
        if entry.get("is_indexed", True)
    ]
    return CrossLocalizationGridOut(items=items)
