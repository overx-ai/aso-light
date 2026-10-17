"""App Store screenshot-set helpers, plus the main product-page service.

The ``appScreenshotSets`` -> ``appScreenshots`` model is identical whether the
parent localization is a Custom Product Page localization, a live App Store
version localization, or a Product Page Optimization (App Store Version
Experiment) treatment localization. Both
:class:`app.services.asc.cpp.ASCCustomProductPageService` and
:class:`app.services.asc.experiment.ASCExperimentService` delegate here so the
3-step reserve -> PUT -> commit upload, the set resolution, and the CDN
``source_url`` shaping live in exactly one place.

The set/asset calls, the export-directory scan and the plan/apply are the
asset-kind-agnostic engine in :mod:`app.services.asc.asset_sync`, shared with
app previews (spec 017). This module binds it to screenshots:

* **Parent-agnostic helpers** (everything up to
  :func:`upload_screenshot_to_localization`), the screenshot names callers
  have always used. Every function takes the :class:`ASCClient` first and
  only differs by the parent localization's resource *type* and its
  ``appScreenshotSets`` relationship *key*.
* :class:`LocalizationScreenshotService` — the engine bound to screenshots:
  how an export image is read and checked. Its subclasses add a
  *localization source*: :class:`ASCVersionScreenshotService` (the main
  product page, via :class:`MainListingSource`) and
  ``app.services.asc.cpp.CPPScreenshotService``.
* The export image reader: PNG/JPEG only, display type from pixel size.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from app.schemas.screenshots import MAX_SCREENSHOT_BYTES

# Re-exported: callers and tests import the engine's names from here.
from app.services.asc.asset_sync import (  # noqa: F401
    ASSET_STATE_AWAITING_UPLOAD,
    ASSET_STATE_COMPLETE,
    ASSET_STATE_FAILED,
    DISPLAY_TYPE_BY_SIZE,
    SCREENSHOT,
    SYNC_SKIPPED_DIRS,
    EditableVersion,
    ExportChangedError,
    ExportFile,
    ExportScan,
    FileRefused,
    LocalizationAssetService,
    NotEditableError,
    SyncPathError,
    SyncStep,
    SyncTarget,
    asset_set_ids,
    delete_asset,
    delete_asset_set,
    find_or_create_asset_set,
    list_set_resources,
    locales_of,
    missing_families,
    require_unchanged,
    resolve_sync_dir,
    scan_export_dir,
    set_asset_order,
    shape_asset,
    source_checksum,
    upload_asset,
    within_sync_roots,
)
from app.services.metadata.client import (
    EDITABLE_VERSION_STATES,
    ASCMetadataService,
)

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient


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
        shaped.update(shape_asset(resource, SCREENSHOT))
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
    return await asset_set_ids(client, SCREENSHOT, localization_type, localization_id)


async def find_or_create_screenshot_set(
    client: ASCClient,
    localization_type: str,
    localization_id: str,
    relationship_key: str,
    display_type: str,
) -> str:
    """Find (or create) the ``appScreenshotSet`` for a display type.

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
    return await find_or_create_asset_set(
        client, SCREENSHOT, localization_type, localization_id, relationship_key, display_type
    )


async def upload_screenshot(
    client: ASCClient,
    set_id: str,
    file_bytes: bytes,
    file_name: str,
) -> dict:
    """Upload a screenshot to a known set via the 3-step reserve/PUT/commit flow.

    Returns:
        The committed ``appScreenshots`` resource dict.
    """
    return await upload_asset(client, SCREENSHOT, set_id, file_bytes, file_name)


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
    """List a set's screenshots **in set order**, delivery state included."""
    return [
        shape_screenshot(resource, include_delivery_state=True)
        for resource in await list_set_resources(client, SCREENSHOT, set_id)
    ]


async def set_screenshot_order(
    client: ASCClient, set_id: str, screenshot_ids: list[str]
) -> None:
    """Replace a set's display order.

    ``PATCH /v1/appScreenshotSets/{set_id}/relationships/appScreenshots`` with
    the full ordered id list. Used after a positional upload so a replaced
    screenshot keeps its slot instead of being appended to the end.
    """
    await set_asset_order(client, SCREENSHOT, set_id, screenshot_ids)


async def delete_screenshot(client: ASCClient, screenshot_id: str) -> None:
    """Delete one screenshot. ``DELETE /v1/appScreenshots/{screenshot_id}``."""
    await delete_asset(client, SCREENSHOT, screenshot_id)


async def delete_screenshot_set(client: ASCClient, set_id: str) -> None:
    """Delete a whole set. ``DELETE /v1/appScreenshotSets/{set_id}``."""
    await delete_asset_set(client, SCREENSHOT, set_id)


# ==================================================================
# Localization sources — the main listing here, a CPP in cpp.py
# ==================================================================


class VersionNotEditableError(NotEditableError):
    """Raised when an app has no App Store version accepting asset writes.

    Carries the offending ``state`` so the caller can name it — a live or
    locked version otherwise surfaces as an opaque 409 from Apple, several
    calls later. ``noun`` is the asset kind the write was for.
    """

    def __init__(
        self,
        state: str | None,
        version_string: str | None = None,
        noun: str = SCREENSHOT.noun,
    ) -> None:
        self.state = state
        self.version_string = version_string
        label = f" {version_string}" if version_string else ""
        if state is None:
            message = (
                f"This app has no App Store version to attach {noun}s to. "
                "Create a new version in App Store Connect first."
            )
        else:
            message = (
                f"App Store version{label} is in state {state}, which has no "
                f"editable {noun} sets. {noun.capitalize()}s can only be changed "
                "on a version in one of: "
                f"{', '.join(sorted(EDITABLE_VERSION_STATES))}. Create a new "
                "version in App Store Connect first."
            )
        super().__init__(message)


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


class LocalizationScreenshotService(LocalizationAssetService):
    """The asset-sync engine bound to screenshots, plus the screenshot-named
    set/asset operations the upload and delete tools use."""

    kind = SCREENSHOT

    def read_export_file(self, path: Path) -> tuple[str, ExportFile]:
        display_type, data = _read_export_file(path)
        return display_type, ExportFile(path, source_checksum(data), len(data))

    def read_planned_bytes(self, export_file: ExportFile) -> bytes:
        return read_planned_bytes(export_file)

    async def read_back_sets(self, localization_id: str) -> list[dict]:
        return await self.get_screenshot_sets(localization_id)

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
        return await self.ensure_asset_set(localization_id, display_type)

    async def list_set_screenshots(self, set_id: str) -> list[dict]:
        return await list_set_screenshots(self.client, set_id)

    async def read_back(self, screenshot_id: str) -> dict:
        return await fetch_screenshot(self.client, screenshot_id)

    async def delete_screenshot(self, screenshot_id: str) -> None:
        await self.delete_asset(screenshot_id)

    async def screenshot_set_ids(self, localization_id: str) -> dict[str, str]:
        return await self.asset_set_ids(localization_id)


# The parent localization type + set relationship key for the app's MAIN
# product page, mirroring the ``_CPP_*`` pair in ``app.services.asc.cpp`` and
# the treatment pair in ``app.services.asc.experiment``.
MAIN_LOCALIZATION_TYPE = "appStoreVersionLocalizations"
MAIN_SET_RELATIONSHIP = "appStoreVersionLocalization"


class MainListingSource(LocalizationAssetService):
    """The app's MAIN product page as a localization source, for any kind.

    Every write is scoped to the app's *editable* App Store version: the
    localizations (and therefore the sets) of a live or in-review version are
    read-only, so :meth:`resolve_editable_version` runs first and raises
    :class:`VersionNotEditableError` naming the state.
    """

    localization_type = MAIN_LOCALIZATION_TYPE
    set_relationship = MAIN_SET_RELATIONSHIP

    def __init__(self, client: ASCClient) -> None:
        super().__init__(client)
        self.metadata = ASCMetadataService(client)

    async def resolve_editable_version(self, asc_app_id: str) -> EditableVersion:
        """Resolve the version whose screenshots and previews may be edited.

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
            raise VersionNotEditableError(None, noun=self.kind.noun)
        raise VersionNotEditableError(
            _version_state(newest),
            (newest.get("attributes", {}) or {}).get("versionString"),
            noun=self.kind.noun,
        )

    async def localizations_by_locale(self, version_id: str) -> dict[str, str]:
        return locales_of(await self.metadata.list_version_localizations(version_id))

    async def app_locales(self, asc_app_id: str) -> frozenset[str]:
        """The locales of the app's newest App Store version, whatever its state."""
        newest = _most_recent(await self.metadata.list_app_store_versions(asc_app_id))
        if newest is None:
            return frozenset()
        return frozenset(await self.localizations_by_locale(newest["id"]))


class ASCVersionScreenshotService(MainListingSource, LocalizationScreenshotService):
    """Screenshot reads/writes for the app's MAIN product page."""

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


# ==================================================================
# Export image reader (spec 013)
# ==================================================================

SYNC_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
SYNC_IMAGE_FORMATS = frozenset({"PNG", "JPEG"})


def display_type_for_size(width: int, height: int) -> str | None:
    return SCREENSHOT.type_for_size(width, height)


def _read_export_file(path: Path) -> tuple[str, bytes]:
    """``(display type, bytes)``; size, type and checksum all come from one read."""
    if not within_sync_roots(path):
        raise FileRefused("resolves outside SCREENSHOT_SYNC_ROOTS")
    if not path.is_file():
        raise FileRefused("not a file")
    if path.suffix.lower() not in SYNC_IMAGE_SUFFIXES:
        raise FileRefused("not a PNG or JPEG file")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SCREENSHOT_BYTES + 1)
    except OSError as exc:
        raise FileRefused(f"unreadable ({exc.strerror})") from exc
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise FileRefused(f"over the {MAX_SCREENSHOT_BYTES}-byte cap")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise FileRefused("not a readable image") from exc
    if image_format not in SYNC_IMAGE_FORMATS:
        raise FileRefused(f"a {image_format} image, not a PNG or JPEG")
    display_type = display_type_for_size(width, height)
    if display_type is None:
        raise FileRefused(f"{width}x{height} is not an App Store screenshot size")
    return display_type, data


def read_planned_bytes(export_file: ExportFile) -> bytes:
    """The file's bytes, refused unless they still hash to the planned MD5."""
    try:
        _, data = _read_export_file(export_file.path)
    except FileRefused as exc:
        raise ExportChangedError(f"{export_file.path}: {exc}.") from exc
    return require_unchanged(export_file, data)
