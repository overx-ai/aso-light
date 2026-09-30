"""App Store screenshot-set helpers, plus the main product-page service.

The ``appScreenshotSets`` -> ``appScreenshots`` model is identical whether the
parent localization is a Custom Product Page localization, a live App Store
version localization, or a Product Page Optimization (App Store Version
Experiment) treatment localization. Both
:class:`app.services.asc.cpp.ASCCustomProductPageService` and
:class:`app.services.asc.experiment.ASCExperimentService` delegate here so the
3-step reserve -> PUT -> commit upload, the set resolution, and the CDN
``source_url`` shaping live in exactly one place.

The module has three parts:

* **Parent-agnostic helpers** (everything up to
  :func:`upload_screenshot_to_localization`). Every function takes the
  :class:`ASCClient` as its first argument (services hold a ``client``) and only
  differs by the parent localization's resource *type* and its
  ``appScreenshotSets`` relationship *key* — the two values that change between
  parents. CPP and PPO consume these.
* :class:`LocalizationScreenshotService` — those helpers bound to one parent
  type, plus the plan/apply of an export directory into it (spec 013). Its
  subclasses are the *localization sources*: :class:`ASCVersionScreenshotService`
  (the main product page, which resolves the app's **editable** App Store
  version first) and ``app.services.asc.cpp.CPPScreenshotService``.
* The export-directory scan: size table, root allowlist, per-locale steps.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.schemas.screenshots import (
    MAX_SCREENSHOT_BYTES,
    MAX_SCREENSHOT_FILES,
    SyncAction,
)
from app.services.asc.errors import ASCAPIError
from app.services.metadata.client import (
    EDITABLE_VERSION_STATES,
    ASCMetadataService,
)

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)

# Apple's ``assetDeliveryState.state`` values that matter to us. A committed
# screenshot lands in ``UPLOAD_COMPLETE`` and is promoted to ``COMPLETE``
# asynchronously; ``FAILED`` means Apple rejected the asset (wrong dimensions,
# alpha channel, ...) even though every HTTP call returned 2xx.
ASSET_STATE_COMPLETE = "COMPLETE"
ASSET_STATE_FAILED = "FAILED"


def build_source_url(image_asset: dict | None) -> str | None:
    """Build a downloadable CDN URL from an ``imageAsset`` block.

    Substitutes Apple's ``{w}``/``{h}``/``{f}`` placeholders in ``templateUrl``
    with the asset's own width/height (falling back to the iPhone 6.7"
    1290x2796 marketing resolution) and ``png``. Returns ``None`` when no
    template is present (source upload still pending).
    """
    if not image_asset:
        return None
    template = image_asset.get("templateUrl")
    if not template:
        return None
    width = image_asset.get("width") or 1290
    height = image_asset.get("height") or 2796
    return (
        template.replace("{w}", str(width))
        .replace("{h}", str(height))
        .replace("{f}", "png")
    )


def shape_screenshot(
    resource: dict,
    display_type: str | None = None,
    *,
    include_delivery_state: bool = False,
) -> dict:
    """Shape a raw ``appScreenshots`` resource into our flat dict.

    ``include_delivery_state`` additionally surfaces
    ``assetDeliveryState.state`` as ``state`` and its error descriptions as
    ``errors`` — the only way to tell a *committed* asset from an *accepted*
    one. It is opt-in so the CPP / PPO shapes (which never asked for the
    ``assetDeliveryState`` field) stay byte-for-byte identical.
    """
    attrs = resource.get("attributes", {}) or {}
    shaped = {
        "id": resource.get("id", ""),
        "file_name": attrs.get("fileName"),
        "display_type": display_type,
        "source_url": build_source_url(attrs.get("imageAsset")),
    }
    if include_delivery_state:
        delivery = attrs.get("assetDeliveryState") or {}
        shaped["state"] = delivery.get("state")
        shaped["checksum"] = attrs.get("sourceFileChecksum")
        shaped["errors"] = [
            err.get("description") or err.get("code") or "unknown error"
            for err in (delivery.get("errors") or [])
        ]
    return shaped


async def fetch_screenshot_sets(
    client: ASCClient,
    path: str,
    *,
    include_delivery_state: bool = False,
) -> list[dict]:
    """Fetch screenshot sets with their included screenshots and shape them.

    ``GET {path}?include=appScreenshots`` — resolves the included
    ``appScreenshots`` resources per set and builds each screenshot's CDN
    ``source_url`` from ``imageAsset.templateUrl``. ``path`` is the parent
    localization's ``appScreenshotSets`` collection, so the same shaping backs
    every parent type.

    Args:
        include_delivery_state: When true, request ``assetDeliveryState`` too
            and add ``state`` / ``errors`` to every shaped screenshot. Default
            false — CPP and PPO callers get the exact request and shape they
            always got.

    Returns:
        List of shaped dicts, one per set::

            {"id", "display_type", "screenshots": [{"id", "file_name",
             "display_type", "source_url"}, ...]}
    """
    screenshot_fields = "fileName,imageAsset"
    if include_delivery_state:
        screenshot_fields += ",assetDeliveryState"
    response = await client._get(
        path,
        params={
            "include": "appScreenshots",
            "fields[appScreenshotSets]": "screenshotDisplayType,appScreenshots",
            "fields[appScreenshots]": screenshot_fields,
            "limit": 200,
        },
    )

    # Build a lookup of included screenshot assets by id.
    included = response.get("included", [])
    screenshots_map: dict[str, dict] = {}
    for item in included:
        if item.get("type") == "appScreenshots":
            screenshots_map[item["id"]] = item

    sets: list[dict] = []
    for set_obj in response.get("data", []):
        set_attrs = set_obj.get("attributes", {})
        display_type = set_attrs.get("screenshotDisplayType")

        shot_refs = (
            set_obj.get("relationships", {}).get("appScreenshots", {}).get("data", [])
        )

        screenshots: list[dict] = []
        for ref in shot_refs:
            shot = screenshots_map.get(ref.get("id"))
            if shot is None:
                continue
            screenshots.append(
                shape_screenshot(
                    shot,
                    display_type,
                    include_delivery_state=include_delivery_state,
                )
            )

        sets.append(
            {
                "id": set_obj["id"],
                "display_type": display_type,
                "screenshots": screenshots,
            }
        )

    return sets


async def screenshot_set_ids(
    client: ASCClient, localization_type: str, localization_id: str
) -> dict[str, str]:
    """``display type -> set id`` for a localization, without reading any asset."""
    existing = await client._get_all_pages(
        f"/{localization_type}/{localization_id}/appScreenshotSets",
        params={
            "fields[appScreenshotSets]": "screenshotDisplayType",
            "limit": 200,
        },
    )
    ids: dict[str, str] = {}
    for set_obj in existing:
        display_type = (set_obj.get("attributes") or {}).get("screenshotDisplayType")
        if display_type:
            ids.setdefault(display_type, set_obj["id"])
    return ids


async def find_or_create_screenshot_set(
    client: ASCClient,
    localization_type: str,
    localization_id: str,
    relationship_key: str,
    display_type: str,
) -> str:
    """Find (or create) the ``appScreenshotSet`` for a display type.

    Screenshots hang off a set keyed by ``screenshotDisplayType`` under the
    parent localization. Reuses an existing set for the requested device family
    if present, else creates a new one linked to the localization.

    Args:
        localization_type: The parent localization's JSON:API resource type
            (e.g. ``appCustomProductPageLocalizations`` or
            ``appStoreVersionExperimentTreatmentLocalizations``).
        localization_id: The parent localization's id.
        relationship_key: The set's relationship name back to the localization
            (e.g. ``appCustomProductPageLocalization`` or
            ``appStoreVersionExperimentTreatmentLocalization``).
        display_type: Apple's ``screenshotDisplayType`` (device family).

    Returns:
        The ``appScreenshotSets`` id to attach the new screenshot to.
    """
    existing = await screenshot_set_ids(client, localization_type, localization_id)
    if display_type in existing:
        return existing[display_type]

    body = {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {
                relationship_key: {
                    "data": {"type": localization_type, "id": localization_id},
                },
            },
        }
    }
    response = await client._post("/appScreenshotSets", json=body)
    return response["data"]["id"]


def source_checksum(file_bytes: bytes) -> str:
    # Apple requires md5 for the appScreenshots sourceFileChecksum — this is a
    # content checksum for upload integrity, not a security primitive.
    return hashlib.md5(file_bytes).hexdigest()  # noqa: S324


def _missing_id_error(detail: str) -> ASCAPIError:
    """The shaped ASC error every caller already turns into a one-line message."""
    return ASCAPIError(502, {"errors": [{"detail": detail}]})


async def upload_screenshot(
    client: ASCClient,
    set_id: str,
    file_bytes: bytes,
    file_name: str,
) -> dict:
    """Upload a screenshot to a known set via the 3-step reserve/PUT/commit flow.

    1. ``POST /v1/appScreenshots`` to reserve the asset (returns
       ``uploadOperations`` pre-signed PUT URLs).
    2. ``PUT`` the source bytes to each upload operation URL (no auth headers —
       Apple rejects Bearer tokens on the pre-signed S3 URLs).
    3. ``PATCH`` ``uploaded=true`` with the source file's md5 checksum.

    This step is fully parent-agnostic — the set already resolves its own
    localization — so it is shared verbatim across CPP and PPO.

    Returns:
        The committed ``appScreenshots`` resource dict.
    """
    checksum = source_checksum(file_bytes)

    reserve_body = {
        "data": {
            "type": "appScreenshots",
            "attributes": {
                "fileName": file_name,
                "fileSize": len(file_bytes),
            },
            "relationships": {
                "appScreenshotSet": {
                    "data": {"type": "appScreenshotSets", "id": set_id},
                },
            },
        }
    }
    reservation = await client._post("/appScreenshots", json=reserve_body)

    reserved = (reservation or {}).get("data") or {}
    screenshot_id = str(reserved.get("id") or "")
    if not screenshot_id:
        # Without an id the commit below would PATCH ``/appScreenshots/`` — the
        # collection, not a resource — and the previous ``["id"]`` lookup would
        # have raised a bare KeyError (an unhandled 500).
        raise _missing_id_error(
            "App Store Connect returned no screenshot id when "
            f"reserving {file_name!r}; nothing was uploaded."
        )
    operations = (reserved.get("attributes") or {}).get("uploadOperations", [])

    for op in operations:
        content_type = "application/octet-stream"
        for hdr in op.get("requestHeaders", []):
            if hdr.get("name", "").lower() == "content-type":
                content_type = hdr["value"]
        offset = op.get("offset", 0)
        await client._put_binary(
            op["url"],
            file_bytes[offset : offset + op["length"]],
            content_type=content_type,
        )

    commit_body = {
        "data": {
            "type": "appScreenshots",
            "id": screenshot_id,
            "attributes": {
                "uploaded": True,
                "sourceFileChecksum": checksum,
            },
        }
    }
    response = await client._patch(f"/appScreenshots/{screenshot_id}", json=commit_body)
    return response.get("data", {})


async def upload_screenshot_to_localization(
    client: ASCClient,
    localization_type: str,
    localization_id: str,
    relationship_key: str,
    display_type: str,
    file_bytes: bytes,
    file_name: str,
) -> dict:
    """Resolve (or create) the set for ``display_type`` then upload the asset.

    Convenience wrapper combining :func:`find_or_create_screenshot_set` and
    :func:`upload_screenshot` — the whole "put this screenshot on this
    localization's device family" operation for any parent type.
    """
    set_id = await find_or_create_screenshot_set(
        client, localization_type, localization_id, relationship_key, display_type
    )
    return await upload_screenshot(client, set_id, file_bytes, file_name)


async def fetch_screenshot(client: ASCClient, screenshot_id: str) -> dict:
    """Read one ``appScreenshots`` resource back, delivery state included.

    A 2xx on the commit ``PATCH`` only says Apple accepted the bytes; the
    asset can still land in ``FAILED``. This is the read-back that turns an
    "upload succeeded" claim into something observed.
    """
    response = await client._get(
        f"/appScreenshots/{screenshot_id}",
        params={"fields[appScreenshots]": "fileName,imageAsset,assetDeliveryState"},
    )
    return shape_screenshot(response.get("data", {}) or {}, include_delivery_state=True)


async def list_set_screenshots(client: ASCClient, set_id: str) -> list[dict]:
    """List a set's screenshots **in set order**, delivery state included.

    ``GET /v1/appScreenshotSets/{set_id}/appScreenshots`` — Apple returns the
    assets in the set's own display order, which is what makes a positional
    (locale, display type, position) replace well-defined.
    """
    resources = await client._get_all_pages(
        f"/appScreenshotSets/{set_id}/appScreenshots",
        params={
            "fields[appScreenshots]": (
                "fileName,imageAsset,assetDeliveryState,sourceFileChecksum"
            ),
            "limit": 200,
        },
    )
    return [
        shape_screenshot(resource, include_delivery_state=True)
        for resource in resources
    ]


async def set_screenshot_order(
    client: ASCClient, set_id: str, screenshot_ids: list[str]
) -> None:
    """Replace a set's display order.

    ``PATCH /v1/appScreenshotSets/{set_id}/relationships/appScreenshots`` with
    the full ordered id list. Used after a positional upload so a replaced
    screenshot keeps its slot instead of being appended to the end.
    """
    body = {
        "data": [
            {"type": "appScreenshots", "id": shot_id} for shot_id in screenshot_ids
        ]
    }
    await client._patch(
        f"/appScreenshotSets/{set_id}/relationships/appScreenshots", json=body
    )


async def delete_screenshot(client: ASCClient, screenshot_id: str) -> None:
    """Delete one screenshot. ``DELETE /v1/appScreenshots/{screenshot_id}``."""
    await client._delete(f"/appScreenshots/{screenshot_id}")


async def delete_screenshot_set(client: ASCClient, set_id: str) -> None:
    """Delete a whole set. ``DELETE /v1/appScreenshotSets/{set_id}``.

    Apple keeps an emptied set around as a *configured* device family with zero
    assets, which is exactly the shape that fails review. Callers that empty a
    set should prune it.
    """
    await client._delete(f"/appScreenshotSets/{set_id}")


# ==================================================================
# Localization sources — the main listing here, a CPP in cpp.py
# ==================================================================


class NotEditableError(Exception):
    """No version of the parent accepts screenshot writes; ``message`` names why."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class VersionNotEditableError(NotEditableError):
    """Raised when an app has no App Store version accepting screenshot writes.

    Carries the offending ``state`` so the caller can name it — a live or
    locked version otherwise surfaces as an opaque 409 from Apple, several
    calls later.
    """

    def __init__(self, state: str | None, version_string: str | None = None) -> None:
        self.state = state
        self.version_string = version_string
        label = f" {version_string}" if version_string else ""
        if state is None:
            message = (
                "This app has no App Store version to attach screenshots to. "
                "Create a new version in App Store Connect first."
            )
        else:
            message = (
                f"App Store version{label} is in state {state}, which has no "
                "editable screenshot sets. Screenshots can only be changed on a "
                "version in one of: "
                f"{', '.join(sorted(EDITABLE_VERSION_STATES))}. Create a new "
                "version in App Store Connect first."
            )
        super().__init__(message)


@dataclass(frozen=True)
class EditableVersion:
    """The App Store version screenshot writes target."""

    id: str
    state: str | None
    version_string: str | None


def _version_state(resource: dict) -> str | None:
    """Read a version's lifecycle state across Apple's three attribute names.

    Apple has shipped ``appStoreState``, ``appVersionState`` and plain
    ``state`` for the same concept across API revisions/tenants.
    """
    attrs = resource.get("attributes", {}) or {}
    return (
        attrs.get("appStoreState") or attrs.get("appVersionState") or attrs.get("state")
    )


def _most_recent(versions: list[dict]) -> dict | None:
    """Newest version by ``createdDate`` (missing dates sort last)."""
    if not versions:
        return None
    return sorted(
        versions,
        key=lambda v: (v.get("attributes", {}) or {}).get("createdDate", "") or "",
        reverse=True,
    )[0]


def locales_of(localizations: list[dict]) -> dict[str, str]:
    """``locale -> localization id`` from raw localization resources."""
    result: dict[str, str] = {}
    for resource in localizations:
        locale = (resource.get("attributes", {}) or {}).get("locale")
        if locale:
            result[locale] = resource["id"]
    return result


class LocalizationScreenshotService:
    """Set/asset operations on one parent localization type, and the spec 013
    plan/apply of an export directory into it.

    A subclass is a *localization source*: it names the parent type and the
    set's relationship back to it, maps a version's locales to localization
    ids and, where the source allows it, creates a missing localization. The
    main listing and a Custom Product Page are the two sources, so the sync
    loop below exists once.
    """

    localization_type: ClassVar[str]
    set_relationship: ClassVar[str]

    def __init__(self, client: ASCClient) -> None:
        self.client = client

    async def localizations_by_locale(self, version_id: str) -> dict[str, str]:
        raise NotImplementedError

    async def ensure_localization(self, version_id: str, locale: str) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} does not create localizations ({locale})."
        )

    # ------------------------------------------------------------------
    # Sets + assets
    # ------------------------------------------------------------------

    async def get_screenshot_sets(self, localization_id: str) -> list[dict]:
        """Shaped screenshot sets for one localization (with states)."""
        return await fetch_screenshot_sets(
            self.client,
            f"/{self.localization_type}/{localization_id}/appScreenshotSets",
            include_delivery_state=True,
        )

    async def find_screenshot_set(
        self, localization_id: str, display_type: str
    ) -> dict | None:
        """The existing set for a display type, or ``None`` if not configured."""
        for shot_set in await self.get_screenshot_sets(localization_id):
            if shot_set.get("display_type") == display_type:
                return shot_set
        return None

    async def ensure_screenshot_set(
        self, localization_id: str, display_type: str
    ) -> str:
        return await find_or_create_screenshot_set(
            self.client,
            self.localization_type,
            localization_id,
            self.set_relationship,
            display_type,
        )

    async def list_set_screenshots(self, set_id: str) -> list[dict]:
        return await list_set_screenshots(self.client, set_id)

    async def upload_to_set(
        self, set_id: str, file_bytes: bytes, file_name: str
    ) -> dict:
        return await upload_screenshot(self.client, set_id, file_bytes, file_name)

    async def read_back(self, screenshot_id: str) -> dict:
        return await fetch_screenshot(self.client, screenshot_id)

    async def reorder_set(self, set_id: str, screenshot_ids: list[str]) -> None:
        await set_screenshot_order(self.client, set_id, screenshot_ids)

    async def delete_screenshot(self, screenshot_id: str) -> None:
        await delete_screenshot(self.client, screenshot_id)

    async def delete_set(self, set_id: str) -> None:
        """Delete a set (use after emptying it, so no orphan set is left)."""
        await delete_screenshot_set(self.client, set_id)

    async def screenshot_set_ids(self, localization_id: str) -> dict[str, str]:
        return await screenshot_set_ids(
            self.client, self.localization_type, localization_id
        )

    # ------------------------------------------------------------------
    # Sync (spec 013, generalised over the source by spec 015)
    # ------------------------------------------------------------------

    async def plan_sync(self, steps: list[SyncStep]) -> set[str]:
        """Attach each step's current set and assets; return the display types
        the synced locales hold that the export does not touch."""
        set_ids_by_locale: dict[str, dict[str, str]] = {}
        for step in steps:
            if step.error or step.localization_id is None:
                continue
            if step.locale not in set_ids_by_locale:
                set_ids_by_locale[step.locale] = await self.screenshot_set_ids(
                    step.localization_id
                )
            step.set_id = set_ids_by_locale[step.locale].get(step.display_type)
            if step.set_id:
                step.existing = await self.list_set_screenshots(step.set_id)
        synced = {step.display_type for step in steps if not step.error}
        return {
            display_type
            for set_ids in set_ids_by_locale.values()
            for display_type in set_ids
            if display_type not in synced
        }

    async def apply_sync(
        self,
        steps: list[SyncStep],
        version_id: str,
        on_step: Callable[[SyncStep], Awaitable[None]] | None = None,
    ) -> None:
        """Apply every non-``skip`` step, calling ``on_step`` after each step,
        skipped or not. A localization created for one step is handed to its
        locale's other steps rather than looked up again."""
        for step in steps:
            if step.action != "skip":
                await self._apply_or_record(step, version_id)
                for sibling in steps:
                    if sibling.locale == step.locale and sibling.localization_id is None:
                        sibling.localization_id = step.localization_id
            if on_step is not None:
                await on_step(step)

    async def _apply_or_record(self, step: SyncStep, version_id: str) -> None:
        """Apply one step; on an ASC failure re-plan it from the live set and
        apply once more, then record a second failure on the step instead of
        aborting the sync. The re-plan also sweeps what the first attempt left
        half done, such as a reserved asset whose upload never committed."""
        try:
            await self.apply_sync_step(step, version_id)
            return
        except ASCAPIError as exc:
            logger.warning(
                "Sync row %s/%s failed (%s), re-planning it once",
                step.locale,
                step.display_type,
                exc,
            )
        step.set_id = None
        step.existing = []
        try:
            await self.plan_sync([step])
            if step.action != "skip":
                await self.apply_sync_step(step, version_id)
        except ASCAPIError as exc:
            logger.warning(
                "Sync row %s/%s failed again (%s), recorded on the row",
                step.locale,
                step.display_type,
                exc,
            )
            step.failed = str(exc)

    async def apply_sync_step(self, step: SyncStep, version_id: str) -> None:
        """Make one set exactly the step's files, in order, slot by slot.

        Each changed slot's old asset is deleted before its replacement is
        uploaded, so a full set never passes Apple's cap mid-replace. Each
        file is re-read and must still hash to the planned MD5, so what is
        uploaded is exactly what the plan checked, and nothing in the slot (or
        a new set, or a new localization) is written until it is.
        """
        set_id = step.set_id
        order: list[str] = []
        for index, export_file in enumerate(step.files):
            current = step.existing[index] if index < len(step.existing) else None
            if current is not None and _slot_matches(current, export_file):
                order.append(current["id"])
                continue
            file_bytes = await asyncio.to_thread(read_planned_bytes, export_file)
            if current is not None:
                await self.delete_screenshot(current["id"])
            set_id = set_id or await self._ensure_step_set(step, version_id)
            uploaded = await self.upload_to_set(
                set_id, file_bytes, export_file.path.name
            )
            if not uploaded.get("id"):
                raise _missing_id_error(
                    "App Store Connect returned no screenshot id "
                    f"for {step.locale}/{export_file.path.name}."
                )
            order.append(uploaded["id"])
        for extra in step.existing[len(step.files) :]:
            await self.delete_screenshot(extra["id"])
        await self.reorder_set(set_id, order)

    async def _ensure_step_set(self, step: SyncStep, version_id: str) -> str:
        if step.localization_id is None:
            step.localization_id = await self.ensure_localization(
                version_id, step.locale
            )
        return await self.ensure_screenshot_set(
            step.localization_id, step.display_type
        )


# The parent localization type + set relationship key for the app's MAIN
# product page, mirroring the ``_CPP_*`` pair in ``app.services.asc.cpp`` and
# the treatment pair in ``app.services.asc.experiment``.
MAIN_LOCALIZATION_TYPE = "appStoreVersionLocalizations"
MAIN_SET_RELATIONSHIP = "appStoreVersionLocalization"


class ASCVersionScreenshotService(LocalizationScreenshotService):
    """Screenshot reads/writes for the app's MAIN product page.

    Every write is scoped to the app's *editable* App Store version: the
    localizations (and therefore the screenshot sets) of a live or in-review
    version are read-only, so :meth:`resolve_editable_version` runs first and
    raises :class:`VersionNotEditableError` naming the state.
    """

    localization_type = MAIN_LOCALIZATION_TYPE
    set_relationship = MAIN_SET_RELATIONSHIP

    def __init__(self, client: ASCClient) -> None:
        super().__init__(client)
        self.metadata = ASCMetadataService(client)

    async def resolve_editable_version(self, asc_app_id: str) -> EditableVersion:
        """Resolve the version whose screenshots may be edited.

        Raises:
            VersionNotEditableError: When no version is in an editable state.
                The message names the state of the most recent version found
                (or says there is none), so the operator knows to cut a new
                version rather than retry.
        """
        editable = _most_recent(
            await self.metadata.list_app_store_versions(
                asc_app_id,
                filter_states=sorted(EDITABLE_VERSION_STATES),
            )
        )
        if editable is not None:
            attrs = editable.get("attributes", {}) or {}
            return EditableVersion(
                id=editable["id"],
                state=_version_state(editable),
                version_string=attrs.get("versionString"),
            )

        newest = _most_recent(await self.metadata.list_app_store_versions(asc_app_id))
        if newest is None:
            raise VersionNotEditableError(None)
        raise VersionNotEditableError(
            _version_state(newest),
            (newest.get("attributes", {}) or {}).get("versionString"),
        )

    async def localizations_by_locale(self, version_id: str) -> dict[str, str]:
        return locales_of(await self.metadata.list_version_localizations(version_id))

    async def main_families(self, asc_app_id: str) -> dict[str, frozenset[str]]:
        """``locale -> display types`` the editable main listing holds; empty
        when no version is editable (nothing to compare a page against)."""
        try:
            version = await self.resolve_editable_version(asc_app_id)
        except VersionNotEditableError:
            return {}
        localizations = await self.localizations_by_locale(version.id)
        return {
            locale: frozenset(await self.screenshot_set_ids(localization_id))
            for locale, localization_id in localizations.items()
        }

    async def app_locales(self, asc_app_id: str) -> frozenset[str]:
        """The locales of the app's newest App Store version, whatever its state."""
        newest = _most_recent(await self.metadata.list_app_store_versions(asc_app_id))
        if newest is None:
            return frozenset()
        return frozenset(await self.localizations_by_locale(newest["id"]))


# ==================================================================
# Sync from an export directory (spec 013)
# ==================================================================

# Portrait (width, height). The 6.9" iPhone has no display type of its own:
# App Store Connect files its 1320x2868 screenshots under APP_IPHONE_67.
DISPLAY_TYPE_BY_SIZE: dict[tuple[int, int], str] = {
    (1320, 2868): "APP_IPHONE_67",
    (1290, 2796): "APP_IPHONE_67",
    (1284, 2778): "APP_IPHONE_65",
    (1242, 2688): "APP_IPHONE_65",
    (1242, 2208): "APP_IPHONE_55",
    (2064, 2752): "APP_IPAD_PRO_3GEN_129",
    (2048, 2732): "APP_IPAD_PRO_3GEN_129",
    (1668, 2420): "APP_IPAD_PRO_3GEN_11",
    (1668, 2388): "APP_IPAD_PRO_3GEN_11",
    (422, 514): "APP_WATCH_ULTRA",
    (410, 502): "APP_WATCH_ULTRA",
    (416, 496): "APP_WATCH_SERIES_10",
    (396, 484): "APP_WATCH_SERIES_7",
    (368, 448): "APP_WATCH_SERIES_4",
    (312, 390): "APP_WATCH_SERIES_3",
}
SYNC_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
SYNC_IMAGE_FORMATS = frozenset({"PNG", "JPEG"})
# The studio's Custom Product Page exports live beside the locales (spec 015).
SYNC_SKIPPED_DIRS = frozenset({"variants"})


def display_type_for_size(width: int, height: int) -> str | None:
    return DISPLAY_TYPE_BY_SIZE.get((min(width, height), max(width, height)))


class SyncPathError(Exception):
    """The export directory itself is refused; nothing was read or written."""


class ExportChangedError(Exception):
    """A planned file no longer holds the bytes the plan checked."""


class _FileRefused(Exception):
    pass


@dataclass(frozen=True)
class ExportFile:
    path: Path
    md5: str


@dataclass
class SyncStep:
    """One locale x display type of a sync, or one ``error`` row."""

    locale: str
    display_type: str | None = None
    files: list[ExportFile] = field(default_factory=list)
    error: str | None = None
    localization_id: str | None = None
    set_id: str | None = None
    existing: list[dict] = field(default_factory=list)
    failed: str | None = None

    def _changed(self) -> list[int]:
        return [
            index
            for index, export_file in enumerate(self.files)
            if index >= len(self.existing)
            or not _slot_matches(self.existing[index], export_file)
        ]

    @property
    def uploads(self) -> int:
        return len(self._changed())

    @property
    def deletes(self) -> int:
        replaced = sum(1 for index in self._changed() if index < len(self.existing))
        return replaced + max(len(self.existing) - len(self.files), 0)

    @property
    def action(self) -> SyncAction:
        if self.error:
            return "error"
        if self.localization_id is None:
            return "create_localization"
        if not self.existing:
            return "upload"
        if not self.uploads and not self.deletes:
            return "skip"
        return "replace"


def _slot_matches(existing: dict, export_file: ExportFile) -> bool:
    return (
        existing.get("state") != ASSET_STATE_FAILED
        and existing.get("checksum") == export_file.md5
    )


def _sync_roots() -> list[Path]:
    return [
        Path(root).expanduser().resolve() for root in settings.SCREENSHOT_SYNC_ROOTS
    ]


def _within_roots(path: Path) -> bool:
    real = path.resolve()
    return any(real.is_relative_to(root) for root in _sync_roots())


def resolve_sync_dir(directory: str) -> Path:
    """The export directory, refused unless its realpath is under an allowed root."""
    root = Path(directory)
    if not root.is_absolute():
        raise SyncPathError(f"dir must be an absolute path, got {directory!r}.")
    if not _within_roots(root):
        allowed = ", ".join(str(r) for r in _sync_roots()) or "none"
        raise SyncPathError(
            f"{directory} resolves outside SCREENSHOT_SYNC_ROOTS ({allowed})."
        )
    if not root.is_dir():
        raise SyncPathError(f"{directory} is not a directory.")
    return root.resolve()


def _read_export_file(path: Path) -> tuple[str, bytes]:
    """``(display type, bytes)``; size, type and checksum all come from one read."""
    if not _within_roots(path):
        raise _FileRefused("resolves outside SCREENSHOT_SYNC_ROOTS")
    if not path.is_file():
        raise _FileRefused("not a file")
    if path.suffix.lower() not in SYNC_IMAGE_SUFFIXES:
        raise _FileRefused("not a PNG or JPEG file")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SCREENSHOT_BYTES + 1)
    except OSError as exc:
        raise _FileRefused(f"unreadable ({exc.strerror})") from exc
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise _FileRefused(f"over the {MAX_SCREENSHOT_BYTES}-byte cap")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise _FileRefused("not a readable image") from exc
    if image_format not in SYNC_IMAGE_FORMATS:
        raise _FileRefused(f"a {image_format} image, not a PNG or JPEG")
    display_type = display_type_for_size(width, height)
    if display_type is None:
        raise _FileRefused(f"{width}x{height} is not an App Store screenshot size")
    return display_type, data


def read_planned_bytes(export_file: ExportFile) -> bytes:
    """The file's bytes, refused unless they still hash to the planned MD5."""
    try:
        _, data = _read_export_file(export_file.path)
    except _FileRefused as exc:
        raise ExportChangedError(f"{export_file.path}: {exc}.") from exc
    if source_checksum(data) != export_file.md5:
        raise ExportChangedError(
            f"{export_file.path} changed after it was planned and was not "
            "uploaded; rerun the sync."
        )
    return data


def _scan_locale(
    locale_dir: Path, localization_id: str | None, display_types: list[str] | None
) -> list[SyncStep]:
    locale = locale_dir.name
    if not _within_roots(locale_dir):
        return [SyncStep(locale, error=f"{locale}/ resolves outside SCREENSHOT_SYNC_ROOTS.")]

    steps: list[SyncStep] = []
    by_type: dict[str, list[ExportFile]] = {}
    for path in sorted(locale_dir.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            display_type, data = _read_export_file(path)
        except _FileRefused as exc:
            steps.append(SyncStep(locale, error=f"{locale}/{path.name}: {exc}."))
            continue
        if display_types and display_type not in display_types:
            continue
        by_type.setdefault(display_type, []).append(
            ExportFile(path, source_checksum(data))
        )

    if len(by_type) > 1 and not display_types:
        listing = "; ".join(
            f"{display_type}: {', '.join(f.path.name for f in files)}"
            for display_type, files in sorted(by_type.items())
        )
        mixed = (
            f"{locale}/ mixes display types ({listing}). Pass "
            "display_types, or export one directory per device family."
        )
        steps.append(SyncStep(locale, error=mixed))
        return steps

    for display_type, files in sorted(by_type.items()):
        if len(files) > MAX_SCREENSHOT_FILES:
            over_cap = (
                f"{locale}/ holds {len(files)} {display_type} files; "
                f"Apple's cap is {MAX_SCREENSHOT_FILES}."
            )
            steps.append(SyncStep(locale, display_type, error=over_cap))
        else:
            steps.append(
                SyncStep(locale, display_type, files, localization_id=localization_id)
            )
    if not steps:
        steps.append(SyncStep(locale, error=f"{locale}/ holds no screenshots."))
    return steps


@dataclass
class ExportScan:
    steps: list[SyncStep]
    locales: set[str]
    skipped: list[str]


@dataclass(frozen=True)
class SyncTarget:
    """Where a sync writes: a source's editable version and its localizations.

    ``creatable`` are locales the source may add a localization for during
    apply (a CPP takes any of the app's own locales). ``label`` names the
    locale owner in the error for a directory that is none of them.
    ``reference`` is per locale the display types the source must also hold
    (the main listing's families, for a CPP); see :func:`missing_families`.
    """

    service: LocalizationScreenshotService
    version: EditableVersion
    localizations: dict[str, str]
    label: str
    creatable: frozenset[str] = frozenset()
    reference: dict[str, frozenset[str]] = field(default_factory=dict)


async def missing_families(
    target: SyncTarget, steps: list[SyncStep]
) -> dict[str, list[str]]:
    """Per locale, the display types ``target.reference`` holds that the page
    lacks once ``steps`` are done: what it holds now plus what the plan adds."""
    planned: dict[str, set[str]] = {}
    for step in steps:
        if not step.error and step.display_type:
            planned.setdefault(step.locale, set()).add(step.display_type)
    missing: dict[str, list[str]] = {}
    for locale, wanted in target.reference.items():
        localization_id = target.localizations.get(locale)
        if localization_id is None and locale not in planned:
            continue
        lacking = wanted - planned.get(locale, set())
        if lacking and localization_id is not None:
            lacking -= set(await target.service.screenshot_set_ids(localization_id))
        if lacking:
            missing[locale] = sorted(lacking)
    return missing


def scan_export_dir(
    root: Path,
    target: SyncTarget,
    *,
    locales: list[str] | None = None,
    display_types: list[str] | None = None,
) -> ExportScan:
    """Group ``<root>/<locale>/*`` into sync steps against the target's locales.

    A directory that is not one of the target's locales (nor creatable) is an
    error, never a skip: a silently skipped ``nl/`` once shipped a
    "successful" push of nothing. Dot-entries, ``variants/`` and top-level
    files are not locales.
    """
    steps: list[SyncStep] = []
    seen: set[str] = set()
    skipped: list[str] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if (
            name.startswith(".")
            or name in SYNC_SKIPPED_DIRS
            or not entry.is_dir()
            or (locales and name not in locales)
        ):
            skipped.append(name)
            continue
        seen.add(name)
        localization_id = target.localizations.get(name)
        if localization_id is None and name not in target.creatable:
            steps.append(
                SyncStep(
                    name,
                    error=(
                        f"{name}/ is not a locale on {target.label}. Locale "
                        "directories are App Store Connect codes (nl-NL, not nl) "
                        "it already has; add one with metadata_create_locale."
                    ),
                )
            )
            continue
        steps.extend(_scan_locale(entry, localization_id, display_types))

    for locale in locales or []:
        if locale not in seen:
            steps.append(SyncStep(locale, error=f"{locale}/ is not in {root}."))
    if not steps:
        raise SyncPathError(f"{root} holds no locale directories.")
    return ExportScan(steps=steps, locales=seen, skipped=skipped)
