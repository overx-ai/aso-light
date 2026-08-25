"""Sync App Store Connect metadata to local snapshot tables.

Pulls the editable AppInfo + AppStoreVersion locales from ASC and upserts
them into :class:`AppMetadataLocalization` / :class:`AppMetadataState` so
the metadata editor can render without round-tripping ASC on every read.

Idempotent: re-running ``sync_app(app)`` produces the same snapshot rows
(updates in place, deletes locales that no longer exist in ASC).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.services.metadata.client import (
    EDITABLE_VERSION_STATES,
    READ_ONLY_VERSION_STATES_PROMO_ONLY,
    ASCMetadataService,
)

# Field tuples — exported for the route layer + tests.
APP_INFO_FIELDS: tuple[str, ...] = ("name", "subtitle", "privacy_policy_url")
VERSION_FIELDS: tuple[str, ...] = (
    "description",
    "keywords",
    "promotional_text",
    "whats_new",
    "marketing_url",
    "support_url",
)

# Used to compute the "what's editable right now" projection.
ALL_APP_INFO_FIELDS_FOR_EDITABILITY: list[str] = list(APP_INFO_FIELDS)
ALL_VERSION_FIELDS_FOR_EDITABILITY: list[str] = list(VERSION_FIELDS)

# ASC camelCase → our snake_case column names.
_APP_INFO_ATTR_MAP: dict[str, str] = {
    "name": "name",
    "subtitle": "subtitle",
    "privacyPolicyUrl": "privacy_policy_url",
}
_VERSION_ATTR_MAP: dict[str, str] = {
    "description": "description",
    "keywords": "keywords",
    "promotionalText": "promotional_text",
    "whatsNew": "whats_new",
    "marketingUrl": "marketing_url",
    "supportUrl": "support_url",
}


@dataclass
class SnapshotResult:
    """Summary of a single ``sync_app`` call."""

    app_id: int
    app_info_localizations: int
    version_localizations: int
    editable_version_state: str | None
    editable_fields: list[str]


class MetadataSnapshotService:
    """Pull ASC AppInfo + AppStoreVersion trees into snapshot tables."""

    def __init__(self, asc: ASCMetadataService, session: AsyncSession) -> None:
        self.asc = asc
        self.session = session

    async def sync_app(self, app: App) -> SnapshotResult:
        """Pull current ASC metadata into snapshot tables. Idempotent.

        - Picks the editable AppInfo (PREPARE_FOR_SUBMISSION if present,
          else falls back to the first one returned).
        - Picks the editable AppStoreVersion (any of
          ``EDITABLE_VERSION_STATES``); if none, falls back to a live
          version (``READY_FOR_DISTRIBUTION``) so the UI can still surface
          the promo-only field.
        - Upserts each locale row keyed by ``(app_id, kind, locale)``.
        - Deletes snapshot rows for locales that no longer exist in ASC.
        - Computes ``editable_fields``: AppInfo fields are always included;
          version fields depend on the version state.
        """
        # ---- Step A: AppInfo + locales ---------------------------------
        app_infos = await self.asc.list_app_infos(app.asc_app_id)
        chosen_app_info = self._pick_editable_app_info(app_infos)
        app_info_id: str | None = None
        app_info_locs: list[dict] = []
        if chosen_app_info is not None:
            app_info_id = chosen_app_info["id"]
            app_info_locs = await self.asc.list_app_info_localizations(
                app_info_id,
            )

        # ---- Step B: editable AppStoreVersion + locales ----------------
        versions = await self.asc.list_app_store_versions(
            app.asc_app_id,
            filter_states=list(EDITABLE_VERSION_STATES),
        )
        if not versions:
            versions = await self.asc.list_app_store_versions(
                app.asc_app_id,
                filter_states=list(READ_ONLY_VERSION_STATES_PROMO_ONLY),
            )

        chosen_version = self._pick_editable_version(versions)
        version_id: str | None = None
        version_state: str | None = None
        version_locs: list[dict] = []
        if chosen_version is not None:
            version_id = chosen_version["id"]
            # Three names for one thing across ASC API versions: newer
            # responses carry ``appVersionState`` (and that is where
            # ``READY_FOR_DISTRIBUTION`` shows up), older ones
            # ``appStoreState``, some just ``state``. Reading only the first
            # yields None on a live app, which silently drops
            # ``promotional_text`` from editable_fields.
            _version_attrs = chosen_version.get("attributes", {}) or {}
            version_state = (
                _version_attrs.get("appStoreState")
                or _version_attrs.get("appVersionState")
                or _version_attrs.get("state")
            )
            version_locs = await self.asc.list_version_localizations(
                version_id,
            )

        # ---- Step C: compute editable_fields ---------------------------
        editable_fields: list[str] = []
        if app_info_id is not None:
            editable_fields.extend(ALL_APP_INFO_FIELDS_FOR_EDITABILITY)
        if version_state in EDITABLE_VERSION_STATES:
            editable_fields.extend(ALL_VERSION_FIELDS_FOR_EDITABILITY)
        elif version_state in READ_ONLY_VERSION_STATES_PROMO_ONLY:
            editable_fields.append("promotional_text")

        # ---- Step D: upsert localization rows --------------------------
        now = datetime.now(timezone.utc)
        if app_info_id is not None:
            await self._upsert_localizations(
                app_id=app.id,
                kind="app_info",
                parent_id=app_info_id,
                asc_locs=app_info_locs,
                attr_map=_APP_INFO_ATTR_MAP,
                synced_at=now,
            )
        if version_id is not None:
            await self._upsert_localizations(
                app_id=app.id,
                kind="version",
                parent_id=version_id,
                asc_locs=version_locs,
                attr_map=_VERSION_ATTR_MAP,
                synced_at=now,
            )

        # ---- Step E: upsert AppMetadataState row -----------------------
        state_row = await self._get_state_row(app.id)
        if state_row is None:
            state_row = AppMetadataState(app_id=app.id)
            self.session.add(state_row)
        state_row.editable_version_id = version_id
        state_row.editable_version_state = version_state
        state_row.app_info_id = app_info_id
        state_row.editable_fields_json = editable_fields
        state_row.last_synced_at = now

        # ---- Step F: flush + return ------------------------------------
        await self.session.flush()
        return SnapshotResult(
            app_id=app.id,
            app_info_localizations=len(app_info_locs),
            version_localizations=len(version_locs) if version_id else 0,
            editable_version_state=version_state,
            editable_fields=editable_fields,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _upsert_localizations(
        self,
        *,
        app_id: int,
        kind: str,
        parent_id: str,
        asc_locs: list[dict],
        attr_map: dict[str, str],
        synced_at: datetime,
    ) -> None:
        """Upsert one kind of localization rows; delete stale locales."""
        # Index existing rows for this (app_id, kind) by locale.
        existing_stmt = select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app_id,
            AppMetadataLocalization.kind == kind,
        )
        existing_result = await self.session.execute(existing_stmt)
        existing_by_locale: dict[str, AppMetadataLocalization] = {
            row.locale: row for row in existing_result.scalars().all()
        }

        seen_locales: set[str] = set()
        for resource in asc_locs:
            attrs = resource.get("attributes", {}) or {}
            locale = attrs.get("locale")
            if not locale:
                # Defensive: ASC always returns locale, but skip if missing.
                continue
            seen_locales.add(locale)

            row = existing_by_locale.get(locale)
            if row is None:
                row = AppMetadataLocalization(
                    app_id=app_id,
                    kind=kind,
                    locale=locale,
                )
                self.session.add(row)

            row.asc_localization_id = resource["id"]
            row.asc_parent_id = parent_id
            for asc_key, column in attr_map.items():
                setattr(row, column, attrs.get(asc_key))
            row.synced_at = synced_at

        # Delete rows for locales that are no longer in ASC.
        stale_locales = set(existing_by_locale.keys()) - seen_locales
        if stale_locales:
            await self.session.execute(
                delete(AppMetadataLocalization).where(
                    AppMetadataLocalization.app_id == app_id,
                    AppMetadataLocalization.kind == kind,
                    AppMetadataLocalization.locale.in_(stale_locales),
                )
            )

    async def _get_state_row(self, app_id: int) -> AppMetadataState | None:
        stmt = select(AppMetadataState).where(
            AppMetadataState.app_id == app_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _pick_editable_app_info(app_infos: list[dict]) -> dict | None:
        """Prefer ``PREPARE_FOR_SUBMISSION``; else first AppInfo returned.

        Apple has shipped two different attribute names for the AppInfo
        lifecycle state over the years (``appStoreState`` historically,
        ``state`` on newer responses). We check both, defensively.
        """
        if not app_infos:
            return None
        for info in app_infos:
            attrs = info.get("attributes", {}) or {}
            state = attrs.get("appStoreState") or attrs.get("state")
            if state == "PREPARE_FOR_SUBMISSION":
                return info
        return app_infos[0]

    @staticmethod
    def _pick_editable_version(versions: list[dict]) -> dict | None:
        """Return the most-recent version from the list, or ``None``.

        Sort by ``attributes.createdDate`` desc; missing dates sort last.
        """
        if not versions:
            return None

        def _sort_key(v: dict) -> str:
            return (v.get("attributes", {}) or {}).get("createdDate", "") or ""

        return sorted(versions, key=_sort_key, reverse=True)[0]
