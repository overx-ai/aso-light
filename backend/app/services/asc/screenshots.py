"""App Store screenshot-set helpers, plus the main product-page service.

The ``appScreenshotSets`` -> ``appScreenshots`` model is identical whether the
parent localization is a Custom Product Page localization, a live App Store
version localization, or a Product Page Optimization (App Store Version
Experiment) treatment localization. Both
:class:`app.services.asc.cpp.ASCCustomProductPageService` and
:class:`app.services.asc.experiment.ASCExperimentService` delegate here so the
3-step reserve -> PUT -> commit upload, the set resolution, and the CDN
``source_url`` shaping live in exactly one place.

The module has two halves:

* **Parent-agnostic helpers** (everything up to
  :func:`upload_screenshot_to_localization`). Every function takes the
  :class:`ASCClient` as its first argument (services hold a ``client``) and only
  differs by the parent localization's resource *type* and its
  ``appScreenshotSets`` relationship *key* — the two values that change between
  parents. CPP and PPO consume these.
* :class:`ASCVersionScreenshotService` — the *main* product page
  (``appStoreVersionLocalizations``) bound to those helpers, adding the one
  thing only the main listing needs: resolving the app's **editable**
  App Store version before anything may be written.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    existing = await client._get_all_pages(
        f"/{localization_type}/{localization_id}/appScreenshotSets",
        params={
            "fields[appScreenshotSets]": "screenshotDisplayType",
            "limit": 200,
        },
    )
    for set_obj in existing:
        if set_obj.get("attributes", {}).get("screenshotDisplayType") == display_type:
            return set_obj["id"]

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
    # Apple requires md5 for the appScreenshots sourceFileChecksum — this is a
    # content checksum for upload integrity, not a security primitive.
    checksum = hashlib.md5(file_bytes).hexdigest()  # noqa: S324

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
        # have raised a bare KeyError (an unhandled 500). Fail with the shaped
        # ASC error every caller already translates to a single-line message.
        raise ASCAPIError(
            502,
            {
                "errors": [
                    {
                        "detail": (
                            "App Store Connect returned no screenshot id when "
                            f"reserving {file_name!r}; nothing was uploaded."
                        ),
                    },
                ],
            },
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
            "fields[appScreenshots]": "fileName,imageAsset,assetDeliveryState",
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
# Main product page — appStoreVersionLocalizations
# ==================================================================

# The parent localization type + set relationship key for the app's MAIN
# product page, mirroring the ``_CPP_*`` pair in ``app.services.asc.cpp`` and
# the treatment pair in ``app.services.asc.experiment``.
MAIN_LOCALIZATION_TYPE = "appStoreVersionLocalizations"
MAIN_SET_RELATIONSHIP = "appStoreVersionLocalization"


class VersionNotEditableError(Exception):
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
            self.message = (
                "This app has no App Store version to attach screenshots to. "
                "Create a new version in App Store Connect first."
            )
        else:
            self.message = (
                f"App Store version{label} is in state {state}, which has no "
                "editable screenshot sets. Screenshots can only be changed on a "
                "version in one of: "
                f"{', '.join(sorted(EDITABLE_VERSION_STATES))}. Create a new "
                "version in App Store Connect first."
            )
        super().__init__(self.message)


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


class ASCVersionScreenshotService:
    """Screenshot reads/writes for the app's MAIN product page.

    Every write is scoped to the app's *editable* App Store version: the
    localizations (and therefore the screenshot sets) of a live or in-review
    version are read-only, so :meth:`resolve_editable_version` runs first and
    raises :class:`VersionNotEditableError` naming the state.

    Version + localization reads reuse :class:`ASCMetadataService`; the set /
    asset operations reuse the parent-agnostic helpers above — this class adds
    no third copy of either.
    """

    def __init__(self, client: ASCClient) -> None:
        self.client = client
        self.metadata = ASCMetadataService(client)

    # ------------------------------------------------------------------
    # Version + localization resolution
    # ------------------------------------------------------------------

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
        """Map ``locale -> appStoreVersionLocalizations id`` for a version."""
        result: dict[str, str] = {}
        for resource in await self.metadata.list_version_localizations(version_id):
            locale = (resource.get("attributes", {}) or {}).get("locale")
            if locale:
                result[locale] = resource["id"]
        return result

    # ------------------------------------------------------------------
    # Sets + assets
    # ------------------------------------------------------------------

    async def get_screenshot_sets(self, localization_id: str) -> list[dict]:
        """Shaped screenshot sets for one version localization (with states)."""
        return await fetch_screenshot_sets(
            self.client,
            f"/{MAIN_LOCALIZATION_TYPE}/{localization_id}/appScreenshotSets",
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
        """Find (or create) the set for a display type on this localization."""
        return await find_or_create_screenshot_set(
            self.client,
            MAIN_LOCALIZATION_TYPE,
            localization_id,
            MAIN_SET_RELATIONSHIP,
            display_type,
        )

    async def list_set_screenshots(self, set_id: str) -> list[dict]:
        """The set's screenshots in display order."""
        return await list_set_screenshots(self.client, set_id)

    async def upload_to_set(
        self, set_id: str, file_bytes: bytes, file_name: str
    ) -> dict:
        """Reserve -> PUT -> commit one asset into an existing set."""
        return await upload_screenshot(self.client, set_id, file_bytes, file_name)

    async def read_back(self, screenshot_id: str) -> dict:
        """Re-read a committed asset, delivery state included."""
        return await fetch_screenshot(self.client, screenshot_id)

    async def reorder_set(self, set_id: str, screenshot_ids: list[str]) -> None:
        """Set the display order of a set's assets."""
        await set_screenshot_order(self.client, set_id, screenshot_ids)

    async def delete_screenshot(self, screenshot_id: str) -> None:
        """Delete one asset."""
        await delete_screenshot(self.client, screenshot_id)

    async def delete_set(self, set_id: str) -> None:
        """Delete a set (use after emptying it, so no orphan set is left)."""
        await delete_screenshot_set(self.client, set_id)
