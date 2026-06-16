"""Bulk fan-out service for App Store metadata updates.

The editor lets a user pick a single field (e.g. ``promotional_text``) and
broadcast a value to N target locales. We split that into two phases:

* :meth:`BulkMetadataService.preview` — pure read; computes a per-locale
  diff (current value, new value, char overflow, would-skip flag) without
  touching ASC.
* :meth:`BulkMetadataService.apply` — replays the same plan as PATCH calls
  against ASC, returning a per-locale status matrix
  (``applied`` / ``skipped`` / ``failed``).

Both phases share a single source of truth: the snapshot rows in
``app_metadata_localizations`` plus the editable-version flags in
``app_metadata_state``.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.schemas.metadata import (
    MAX_BULK_TARGET_LOCALES,
    BulkApplyResult,
    BulkPreviewItem,
)
from app.services.asc.errors import ASCAPIError
from app.services.metadata.client import (
    ASCMetadataService,
    MetadataNotEditableError,
)
from app.services.metadata.guard import (
    FieldsNotEditableError,
    assert_fields_editable,
)
from app.services.metadata.validation import char_overflow, validate_field

logger = logging.getLogger(__name__)

# Map our snake_case field names to the camelCase attribute keys ASC expects
# in the JSON:API ``attributes`` object.
FIELD_TO_ASC_ATTR: dict[str, str] = {
    "name": "name",
    "subtitle": "subtitle",
    "privacy_policy_url": "privacyPolicyUrl",
    "description": "description",
    "keywords": "keywords",
    "promotional_text": "promotionalText",
    "whats_new": "whatsNew",
    "marketing_url": "marketingUrl",
    "support_url": "supportUrl",
}

# Field-kind partition. Drives which ASC tree (AppInfo vs
# AppStoreVersion) the bulk write targets.
APP_INFO_FIELDS: frozenset[str] = frozenset({
    "name",
    "subtitle",
    "privacy_policy_url",
})
VERSION_FIELDS: frozenset[str] = frozenset({
    "description",
    "keywords",
    "promotional_text",
    "whats_new",
    "marketing_url",
    "support_url",
})


def _skipped(locale: str, reason: str | None) -> BulkApplyResult:
    """A ``skipped`` per-locale outcome carrying the (sanitized) reason."""
    return BulkApplyResult(locale=locale, status="skipped", error=reason)


def _failed(locale: str, error: str) -> BulkApplyResult:
    """A ``failed`` per-locale outcome carrying a sanitized error string."""
    return BulkApplyResult(locale=locale, status="failed", error=error)


class BulkMetadataService:
    """Plan + replay bulk metadata edits across many locales.

    Both :meth:`preview` and :meth:`apply` build the same per-locale plan;
    ``apply`` then walks that plan and PATCHes each locale, recording
    per-locale outcomes. :meth:`preview` never commits. :meth:`apply` commits
    the snapshot mirror incrementally — once per successfully applied locale —
    so an interruption mid-batch cannot discard already-applied edits; the
    caller's trailing commit then only finalizes bookkeeping.
    """

    def __init__(
        self,
        asc: ASCMetadataService,
        session: AsyncSession,
    ) -> None:
        self.asc = asc
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_inputs(
        self,
        field: str,
        value: str | None,
        target_locales: list[str],
        values_by_locale: dict[str, str | None] | None = None,
    ) -> str:
        """Normalize + validate the bulk request, returning ``kind``.

        In localized mode (``values_by_locale`` provided) every target locale
        must have a proposed value, and each value must pass field validation.
        In same-value mode the single ``value`` is validated once.
        """
        if field not in (APP_INFO_FIELDS | VERSION_FIELDS):
            raise ValueError(
                f"Unknown metadata field: {field!r}. "
                f"Must be one of {sorted(APP_INFO_FIELDS | VERSION_FIELDS)}"
            )
        if len(target_locales) > MAX_BULK_TARGET_LOCALES:
            raise ValueError(
                f"target_locales capped at {MAX_BULK_TARGET_LOCALES}, "
                f"got {len(target_locales)}"
            )
        if values_by_locale is not None:
            for locale in target_locales:
                if locale not in values_by_locale:
                    raise ValueError(
                        f"Missing proposed value for target locale {locale}"
                    )
                ok, err = validate_field(field, values_by_locale[locale])
                if not ok:
                    raise ValueError(err)
        else:
            ok, err = validate_field(field, value)
            if not ok:
                raise ValueError(err)
        return "app_info" if field in APP_INFO_FIELDS else "version"

    @staticmethod
    def _value_for(
        locale: str,
        value: str | None,
        values_by_locale: dict[str, str | None] | None,
    ) -> str | None:
        """Resolve the proposed value for a single locale."""
        if values_by_locale is not None:
            return values_by_locale.get(locale)
        return value

    @staticmethod
    def _is_hard_skip(item: BulkPreviewItem) -> bool:
        """Whether a built skip is one ``force`` may NOT override.

        Char-overflow (would fail at ASC and waste a request) and a missing
        version row (nothing to PATCH) are hard skips. The only soft skip is
        ``unchanged``, which ``force`` re-applies. Field-editability is enforced
        separately via :func:`assert_fields_editable` and is never overridable.
        """
        return (
            item.char_overflow_by > 0
            or item.reason == "no existing version localization to update"
        )

    async def _load_existing(
        self,
        app: App,
        kind: str,
        target_locales: list[str],
    ) -> dict[str, AppMetadataLocalization]:
        """Load snapshot rows keyed by locale for the requested set."""
        if not target_locales:
            return {}
        stmt = select(AppMetadataLocalization).where(
            AppMetadataLocalization.app_id == app.id,
            AppMetadataLocalization.kind == kind,
            AppMetadataLocalization.locale.in_(target_locales),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.locale: row for row in rows}

    async def _load_state(self, app: App) -> AppMetadataState | None:
        """Load the per-app editable-version snapshot, if any."""
        stmt = select(AppMetadataState).where(
            AppMetadataState.app_id == app.id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def _build_items(
        self,
        field: str,
        value: str | None,
        kind: str,
        target_locales: list[str],
        existing: dict[str, AppMetadataLocalization],
        state: AppMetadataState | None,
        values_by_locale: dict[str, str | None] | None = None,
    ) -> list[BulkPreviewItem]:
        """Build the per-locale diff list shared by preview + apply.

        When ``values_by_locale`` is provided, each locale's proposed value
        (and therefore its char-overflow check) is resolved independently.
        """
        editable_fields = (
            list(state.editable_fields_json or []) if state else None
        )
        items: list[BulkPreviewItem] = []
        for locale in target_locales:
            row = existing.get(locale)
            current = getattr(row, field, None) if row else None
            new_value = self._value_for(locale, value, values_by_locale)
            overflow = char_overflow(field, new_value)
            would_skip: bool = False
            reason: str | None = None

            if current == new_value:
                would_skip, reason = True, "unchanged"
            elif overflow > 0:
                would_skip, reason = (
                    True,
                    f"value exceeds {field} limit by {overflow} chars",
                )
            elif (
                editable_fields is not None
                and field not in editable_fields
            ):
                would_skip, reason = (
                    True,
                    "field not editable in version state "
                    f"{state.editable_version_state if state else None}",
                )
            elif row is None and kind == "version":
                would_skip, reason = (
                    True,
                    "no existing version localization to update",
                )

            items.append(
                BulkPreviewItem(
                    locale=locale,
                    current_value=current,
                    new_value=new_value,
                    char_overflow_by=overflow,
                    would_skip=would_skip,
                    reason=reason,
                )
            )
        return items

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def preview(
        self,
        app: App,
        field: str,
        value: str | None,
        target_locales: list[str],
        *,
        values_by_locale: dict[str, str | None] | None = None,
    ) -> list[BulkPreviewItem]:
        """Compute the per-locale diff for a bulk fan-out. NO ASC writes."""
        kind = self._validate_inputs(
            field, value, target_locales, values_by_locale,
        )
        existing = await self._load_existing(app, kind, target_locales)
        state = await self._load_state(app)
        return self._build_items(
            field, value, kind, target_locales, existing, state,
            values_by_locale=values_by_locale,
        )

    async def apply(
        self,
        app: App,
        field: str,
        value: str | None,
        target_locales: list[str],
        *,
        force: bool = False,
        values_by_locale: dict[str, str | None] | None = None,
    ) -> list[BulkApplyResult]:
        """Replay the bulk plan as ASC PATCH calls.

        ``force=True`` overrides ONLY the ``unchanged`` skip. It does NOT
        override the field-editability check (re-asserted via
        :func:`assert_fields_editable` against ``editable_fields`` regardless of
        ``force``), the ``char_overflow`` skip (an over-limit value would fail
        at ASC and waste a request), nor the "no existing version localization"
        skip (there is no row to PATCH).

        Each successfully applied locale is committed immediately so a later
        locale's failure cannot roll back already-applied edits; the returned
        per-locale result matrix is therefore accurate even on partial failure,
        and a retry is safe (PATCH is idempotent and ``unchanged`` re-skips).

        When ``values_by_locale`` is provided each locale is patched with its
        own proposed value; otherwise the single ``value`` is fanned out.
        """
        kind = self._validate_inputs(
            field, value, target_locales, values_by_locale,
        )
        existing = await self._load_existing(app, kind, target_locales)
        state = await self._load_state(app)
        plan = self._build_items(
            field, value, kind, target_locales, existing, state,
            values_by_locale=values_by_locale,
        )
        asc_attr = FIELD_TO_ASC_ATTR[field]
        version_state = state.editable_version_state if state else None

        results: list[BulkApplyResult] = []
        for item in plan:
            locale = item.locale

            # Field-editability is a HARD skip that ``force`` can NEVER
            # override — re-assert it against ``editable_fields`` before any
            # other decision.
            try:
                await assert_fields_editable(self.session, app.id, [field])
            except FieldsNotEditableError as exc:
                results.append(_skipped(locale, str(exc)))
                continue

            # ``force`` overrides only the soft ``unchanged`` skip; char-overflow
            # and missing-row skips stay hard.
            if item.would_skip and (not force or self._is_hard_skip(item)):
                results.append(_skipped(locale, item.reason))
                continue

            row = existing.get(locale)
            if row is None:
                # Defensive: the missing-version skip above should catch this,
                # but app_info with no snapshot row also has nothing to PATCH.
                results.append(
                    _skipped(locale, "no existing localization to update")
                )
                continue

            new_value = self._value_for(locale, value, values_by_locale)
            try:
                await self._patch_locale(
                    kind, row.asc_localization_id,
                    {asc_attr: new_value}, version_state,
                )
            except MetadataNotEditableError as exc:
                results.append(_skipped(locale, str(exc)))
                continue
            except ASCAPIError as exc:
                logger.warning(
                    "Bulk %s for app %s locale %s failed: %s",
                    field, app.id, locale, exc,
                )
                # ``exc.message`` is already sanitized (built from the ASC
                # ``errors[].detail`` titles); never leak str(exc).
                results.append(_failed(locale, exc.message))
                continue
            except Exception:  # noqa: BLE001
                # Unexpected — log full traceback so we can debug, but keep
                # going so one bad locale doesn't kill the whole batch. Never
                # surface the raw Python error to the API response.
                logger.exception(
                    "Unexpected error applying bulk %s to app %s locale %s",
                    field, app.id, locale,
                )
                results.append(_failed(locale, "Unexpected error"))
                continue

            # Mirror the change into the local snapshot and persist it
            # durably as produced. The ASC PATCH already happened, so we must
            # NOT let a later locale's failure roll this row back — commit per
            # applied locale (PATCH is idempotent + ``unchanged`` skips make a
            # retry safe). Drift on interruption is bounded to in-flight rows.
            setattr(row, field, new_value)
            await self.session.commit()
            results.append(
                BulkApplyResult(locale=locale, status="applied", error=None)
            )

        return results

    async def _patch_locale(
        self,
        kind: str,
        localization_id: str,
        attrs: dict[str, str | None],
        version_state: str | None,
    ) -> None:
        """Dispatch a single-locale PATCH to the right ASC tree."""
        if kind == "app_info":
            await self.asc.update_app_info_localization(localization_id, attrs)
        else:
            await self.asc.update_version_localization(
                localization_id, attrs, version_state=version_state,
            )
