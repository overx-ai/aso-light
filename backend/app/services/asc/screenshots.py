"""Shared App Store screenshot-set helpers (parent-agnostic).

The ``appScreenshotSets`` -> ``appScreenshots`` model is identical whether the
parent localization is a Custom Product Page localization, a live App Store
version localization, or a Product Page Optimization (App Store Version
Experiment) treatment localization. Both
:class:`app.services.asc.cpp.ASCCustomProductPageService` and
:class:`app.services.asc.experiment.ASCExperimentService` delegate here so the
3-step reserve -> PUT -> commit upload, the set resolution, and the CDN
``source_url`` shaping live in exactly one place.

Every function takes the :class:`ASCClient` as its first argument (services hold
a ``client``) and only differs by the parent localization's resource *type* and
its ``appScreenshotSets`` relationship *key* — the two values that change
between parents.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)


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


async def fetch_screenshot_sets(client: ASCClient, path: str) -> list[dict]:
    """Fetch screenshot sets with their included screenshots and shape them.

    ``GET {path}?include=appScreenshots`` — resolves the included
    ``appScreenshots`` resources per set and builds each screenshot's CDN
    ``source_url`` from ``imageAsset.templateUrl``. ``path`` is the parent
    localization's ``appScreenshotSets`` collection, so the same shaping backs
    every parent type.

    Returns:
        List of shaped dicts, one per set::

            {"id", "display_type", "screenshots": [{"id", "file_name",
             "display_type", "source_url"}, ...]}
    """
    response = await client._get(
        path,
        params={
            "include": "appScreenshots",
            "fields[appScreenshotSets]": "screenshotDisplayType,appScreenshots",
            "fields[appScreenshots]": "fileName,imageAsset",
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
            set_obj.get("relationships", {})
            .get("appScreenshots", {})
            .get("data", [])
        )

        screenshots: list[dict] = []
        for ref in shot_refs:
            shot = screenshots_map.get(ref.get("id"))
            if shot is None:
                continue
            shot_attrs = shot.get("attributes", {})
            screenshots.append({
                "id": shot["id"],
                "file_name": shot_attrs.get("fileName"),
                "display_type": display_type,
                "source_url": build_source_url(shot_attrs.get("imageAsset")),
            })

        sets.append({
            "id": set_obj["id"],
            "display_type": display_type,
            "screenshots": screenshots,
        })

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

    screenshot_id = reservation["data"]["id"]
    operations = reservation["data"]["attributes"].get("uploadOperations", [])

    for op in operations:
        content_type = "application/octet-stream"
        for hdr in op.get("requestHeaders", []):
            if hdr.get("name", "").lower() == "content-type":
                content_type = hdr["value"]
        offset = op.get("offset", 0)
        await client._put_binary(
            op["url"],
            file_bytes[offset:offset + op["length"]],
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
    response = await client._patch(
        f"/appScreenshots/{screenshot_id}", json=commit_body
    )
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
