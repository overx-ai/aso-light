"""Service for managing Custom Product Pages (CPP) via the ASC API.

Wraps the JSON:API calls for the Custom Product Page resource tree:

    appCustomProductPages
      -> appCustomProductPageVersions
        -> appCustomProductPageLocalizations
          -> appScreenshotSets -> appScreenshots

Screenshots reuse the standard set/asset model, so the same
:meth:`ASCCustomProductPageService.get_cpp_screenshots` shaping logic also
backs :meth:`get_default_screenshots` for the live (default) product page —
the source for the old-vs-new visual comparison.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from app.services.asc.errors import ASCAPIError

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)


# CPP version states Apple uses for the editable/draft version. A freshly
# created Custom Product Page auto-creates a single version in one of these
# pre-publish states; that is the version localizations + screenshots attach
# to. Ordered by preference when more than one version exists.
_EDITABLE_VERSION_STATES = (
    "PREPARE_FOR_SUBMISSION",
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
)


class ASCCustomProductPageService:
    """Service for managing Custom Product Pages via the ASC API.

    Read methods return either the raw JSON:API ``data`` array (for the
    list/get helpers) or already-shaped dicts (for the screenshot helpers,
    which resolve ``included`` assets and build CDN source URLs).
    """

    def __init__(self, client: ASCClient):
        self.client = client

    # ------------------------------------------------------------------
    # Custom Product Pages — read
    # ------------------------------------------------------------------

    async def list_cpps(self, asc_app_id: str) -> list[dict]:
        """Fetch the Custom Product Pages for an app.

        ``GET /v1/apps/{asc_app_id}/appCustomProductPages``

        Returns:
            List of JSON:API resource objects with id and
            attributes.{name, visible}.
        """
        return await self.client._get_all_pages(
            f"/apps/{asc_app_id}/appCustomProductPages",
            params={
                "fields[appCustomProductPages]": "name,visible",
                "limit": 200,
            },
        )

    async def get_cpp(self, cpp_id: str) -> dict:
        """Fetch a single Custom Product Page.

        ``GET /v1/appCustomProductPages/{cpp_id}``

        Returns:
            The JSON:API resource dict with id and attributes.
        """
        response = await self.client._get(
            f"/appCustomProductPages/{cpp_id}",
            params={"fields[appCustomProductPages]": "name,visible"},
        )
        return response.get("data", {})

    # ------------------------------------------------------------------
    # Custom Product Pages — write
    # ------------------------------------------------------------------

    async def create_cpp(
        self,
        asc_app_id: str,
        name: str,
        locale: str = "en-US",
        visible: bool = True,
        deep_link: str | None = None,
    ) -> dict:
        """Create a Custom Product Page.

        ``POST /v1/appCustomProductPages``

        ASC requires a page to be created together with its first version and a
        localization, so this sends a **compound** create: the page, an inline
        ``appCustomProductPageVersions`` and an inline
        ``appCustomProductPageLocalizations`` for ``locale`` — linked by Apple's
        ``${...}`` inline-creation ids (the same idiom as the pricing schedules).
        Extra locales are added afterwards with
        :meth:`find_or_create_localization_id`.

        The inline version **must** carry an ``attributes`` object: Apple drops a
        relationships-only inline resource and then reports the misleading error
        "must provide a value for the relationship 'appCustomProductPageLocalizations'"
        (the localizations are nested under the dropped version). ``deepLink`` is
        the version's only writable attribute — ``None`` sends ``null`` (no deep
        link), which is enough to make the version a well-formed inline resource.

        ``visible`` is **not** accepted on CREATE, so the requested visibility is
        applied with a best-effort follow-up update — a brand-new page may refuse
        a visibility change until it has content, and that must not fail the
        create (screenshots still upload; visibility can be set later).

        Returns:
            The created appCustomProductPages resource dict.
        """
        version_lid = "${cpp-version-1}"
        localization_lid = "${cpp-localization-1}"
        body = {
            "data": {
                "type": "appCustomProductPages",
                "attributes": {
                    "name": name,
                },
                "relationships": {
                    "app": {
                        "data": {"type": "apps", "id": asc_app_id},
                    },
                    "appCustomProductPageVersions": {
                        "data": [{
                            "type": "appCustomProductPageVersions",
                            "id": version_lid,
                        }],
                    },
                },
            },
            "included": [
                {
                    "type": "appCustomProductPageVersions",
                    "id": version_lid,
                    "attributes": {"deepLink": deep_link},
                    "relationships": {
                        "appCustomProductPageLocalizations": {
                            "data": [{
                                "type": "appCustomProductPageLocalizations",
                                "id": localization_lid,
                            }],
                        },
                    },
                },
                {
                    "type": "appCustomProductPageLocalizations",
                    "id": localization_lid,
                    "attributes": {"locale": locale},
                },
            ],
        }
        response = await self.client._post(
            "/appCustomProductPages", json=body
        )
        created = response.get("data", {})
        cpp_id = created.get("id")
        if cpp_id and visible is not None:
            try:
                return await self.update_cpp(cpp_id, visible=visible) or created
            except ASCAPIError:
                logger.warning(
                    "CPP %s created but visible=%s not applied "
                    "(ASC rejected the update)", cpp_id, visible,
                )
        return created

    async def create_localization(
        self, version_id: str, locale: str
    ) -> dict:
        """Create a localization under a CPP version.

        ``POST /v1/appCustomProductPageLocalizations``

        Screenshot sets hang off the localization, so a localization for the
        target ``locale`` must exist before any screenshot can be uploaded.

        Returns:
            The created ``appCustomProductPageLocalizations`` resource dict.
        """
        body = {
            "data": {
                "type": "appCustomProductPageLocalizations",
                "attributes": {
                    "locale": locale,
                },
                "relationships": {
                    "appCustomProductPageVersion": {
                        "data": {
                            "type": "appCustomProductPageVersions",
                            "id": version_id,
                        },
                    },
                },
            }
        }
        response = await self.client._post(
            "/appCustomProductPageLocalizations", json=body
        )
        return response.get("data", {})

    async def update_cpp(
        self,
        cpp_id: str,
        name: str | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Update a Custom Product Page.

        ``PATCH /v1/appCustomProductPages/{cpp_id}``

        Only the provided (non-None) attributes are sent.

        Returns:
            The updated appCustomProductPages resource dict.
        """
        attributes: dict[str, object] = {}
        if name is not None:
            attributes["name"] = name
        if visible is not None:
            attributes["visible"] = visible
        if not attributes:
            raise ValueError(
                "update_cpp called with no fields to update"
            )
        body = {
            "data": {
                "type": "appCustomProductPages",
                "id": cpp_id,
                "attributes": attributes,
            }
        }
        response = await self.client._patch(
            f"/appCustomProductPages/{cpp_id}", json=body
        )
        return response.get("data", {})

    async def delete_cpp(self, cpp_id: str) -> None:
        """Delete a Custom Product Page.

        ``DELETE /v1/appCustomProductPages/{cpp_id}``
        """
        await self.client._delete(f"/appCustomProductPages/{cpp_id}")

    # ------------------------------------------------------------------
    # CPP Versions / Localizations — read
    # ------------------------------------------------------------------

    async def list_versions(self, cpp_id: str) -> list[dict]:
        """Fetch the versions of a Custom Product Page.

        ``GET /v1/appCustomProductPages/{cpp_id}/appCustomProductPageVersions``

        Returns:
            List of JSON:API resource objects with id and
            attributes.{version, state, deepLink}.
        """
        return await self.client._get_all_pages(
            f"/appCustomProductPages/{cpp_id}/appCustomProductPageVersions",
            params={
                "fields[appCustomProductPageVersions]": "version,state,deepLink",
                "limit": 200,
            },
        )

    async def get_editable_version_id(self, cpp_id: str) -> str | None:
        """Resolve a CPP's editable (draft) version id.

        A freshly-created Custom Product Page auto-creates one draft version
        in a pre-publish state (``PREPARE_FOR_SUBMISSION`` and friends). This
        walks :meth:`list_versions`, preferring a version in one of the known
        editable states, and falls back to the first version returned when
        none of them match.

        Returns:
            The ``appCustomProductPageVersions`` id to attach a localization
            to, or ``None`` when the CPP has no versions.
        """
        versions = await self.list_versions(cpp_id)
        if not versions:
            return None
        for state in _EDITABLE_VERSION_STATES:
            for version in versions:
                if version.get("attributes", {}).get("state") == state:
                    return version["id"]
        return versions[0]["id"]

    async def list_localizations(self, version_id: str) -> list[dict]:
        """Fetch the localizations of a CPP version.

        ``GET /v1/appCustomProductPageVersions/{version_id}``
        ``/appCustomProductPageLocalizations``

        Returns:
            List of JSON:API resource objects with id and
            attributes.{locale, promotionalText}.
        """
        return await self.client._get_all_pages(
            f"/appCustomProductPageVersions/{version_id}"
            "/appCustomProductPageLocalizations",
            params={
                "fields[appCustomProductPageLocalizations]":
                    "locale,promotionalText",
                "limit": 200,
            },
        )

    async def find_or_create_localization_id(
        self, version_id: str, locale: str
    ) -> str:
        """Resolve (or create) the localization id for a locale under a version.

        Reuses an existing ``appCustomProductPageLocalizations`` whose
        ``locale`` matches, otherwise creates one via
        :meth:`create_localization`.

        Returns:
            The ``appCustomProductPageLocalizations`` id.
        """
        localizations = await self.list_localizations(version_id)
        for loc in localizations:
            if loc.get("attributes", {}).get("locale") == locale:
                return loc["id"]
        created = await self.create_localization(version_id, locale)
        return created["id"]

    # ------------------------------------------------------------------
    # Custom Product Pages — create + populate from an uploaded set
    # ------------------------------------------------------------------

    async def create_cpp_with_screenshots(
        self,
        asc_app_id: str,
        name: str,
        locale: str,
        display_type: str,
        files: list[tuple[str, bytes]],
    ) -> dict:
        """Create a Custom Product Page and populate it from uploaded files.

        End-to-end flow that turns a freshly-uploaded "after" screenshot set
        into a ready-to-use Custom Product Page:

        1. :meth:`create_cpp` — Apple auto-creates a draft version.
        2. :meth:`get_editable_version_id` — resolve that draft version id.
        3. :meth:`find_or_create_localization_id` — ensure a localization for
           ``locale`` exists under the draft version.
        4. :meth:`upload_screenshot_to_cpp` per file — find-or-create the
           ``appScreenshotSet`` for ``display_type`` and run the 3-step
           reserve -> PUT -> commit upload.

        Args:
            asc_app_id: The App Store Connect app id.
            name: The Custom Product Page name (Apple's 100-char limit).
            locale: The App Store locale, e.g. ``en-US``.
            display_type: Apple's ``screenshotDisplayType`` (device family).
            files: Ordered ``(file_name, file_bytes)`` tuples to upload.

        Returns:
            ``{"cpp_id", "name", "uploaded_count"}``.
        """
        cpp = await self.create_cpp(asc_app_id, name, locale=locale, visible=True)
        cpp_id = cpp["id"]

        version_id = await self.get_editable_version_id(cpp_id)
        if version_id is None:
            raise RuntimeError(
                "Custom Product Page has no editable version to populate"
            )

        localization_id = await self.find_or_create_localization_id(
            version_id, locale
        )

        # If any screenshot upload fails the page is left half-populated (and
        # could still be attached to an ASA ad group), so best-effort delete the
        # freshly-created CPP before surfacing the error to the caller.
        uploaded_count = 0
        try:
            for file_name, file_bytes in files:
                await self.upload_screenshot_to_cpp(
                    localization_id, display_type, file_bytes, file_name
                )
                uploaded_count += 1
        except Exception:
            try:
                await self.delete_cpp(cpp_id)
            except Exception:
                logger.warning(
                    "Failed to clean up partial CPP %s after upload error",
                    cpp_id,
                )
            raise

        return {
            "cpp_id": cpp_id,
            "name": cpp.get("attributes", {}).get("name", name),
            "uploaded_count": uploaded_count,
        }

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------

    async def get_cpp_screenshots(self, localization_id: str) -> list[dict]:
        """Fetch the screenshot sets (+ assets) for a CPP localization.

        ``GET /v1/appCustomProductPageLocalizations/{localization_id}``
        ``/appScreenshotSets?include=appScreenshots``

        Returns:
            List of shaped dicts, one per set::

                {
                    "id": <set id>,
                    "display_type": <screenshotDisplayType>,
                    "screenshots": [
                        {"id", "file_name", "display_type", "source_url"},
                        ...
                    ],
                }
        """
        return await self._fetch_screenshot_sets(
            f"/appCustomProductPageLocalizations/{localization_id}"
            "/appScreenshotSets"
        )

    async def get_default_screenshots(
        self, version_localization_id: str
    ) -> list[dict]:
        """Fetch the screenshot sets (+ assets) for a DEFAULT page localization.

        ``GET /v1/appStoreVersionLocalizations/{version_localization_id}``
        ``/appScreenshotSets?include=appScreenshots``

        Same shape as :meth:`get_cpp_screenshots`; the default (live) page's
        screenshots are the "old" side of the old-vs-new comparison.
        """
        return await self._fetch_screenshot_sets(
            f"/appStoreVersionLocalizations/{version_localization_id}"
            "/appScreenshotSets"
        )

    async def _fetch_screenshot_sets(self, path: str) -> list[dict]:
        """Fetch screenshot sets with their included screenshots and shape them.

        ``GET {path}?include=appScreenshots`` — resolves the included
        ``appScreenshots`` resources per set and builds each screenshot's
        CDN ``source_url`` from ``imageAsset.templateUrl``.

        ``appScreenshotSets`` and the default-page localization both expose
        this relationship, so the path is parameterized.
        """
        response = await self.client._get(
            path,
            params={
                "include": "appScreenshots",
                "fields[appScreenshotSets]":
                    "screenshotDisplayType,appScreenshots",
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
                    "source_url": self._build_source_url(
                        shot_attrs.get("imageAsset")
                    ),
                })

            sets.append({
                "id": set_obj["id"],
                "display_type": display_type,
                "screenshots": screenshots,
            })

        return sets

    @staticmethod
    def _build_source_url(image_asset: dict | None) -> str | None:
        """Build a downloadable CDN URL from an ``imageAsset`` block.

        Substitutes Apple's ``{w}``/``{h}``/``{f}`` placeholders in
        ``templateUrl`` with the asset's own width/height (falling back to
        the iPhone 6.7" 1290x2796 marketing resolution) and ``png``.
        Returns ``None`` when no template is present (source upload still
        pending).
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

    # ------------------------------------------------------------------
    # Default (live) page resolution — for the old-vs-new comparison
    # ------------------------------------------------------------------

    async def get_default_version_localization_id(
        self, asc_app_id: str, locale: str
    ) -> str | None:
        """Resolve the DEFAULT (live) page's localization id for a locale.

        Walks ``apps/{id}/appStoreVersions`` (preferring a live
        ``READY_FOR_SALE`` version, falling back to the most recent
        version) then its ``appStoreVersionLocalizations`` to find the one
        whose ``locale`` matches. Returns the
        ``appStoreVersionLocalizations`` id consumed by
        :meth:`get_default_screenshots`, or ``None`` when no matching
        version/locale exists.
        """
        versions = await self.client._get_all_pages(
            f"/apps/{asc_app_id}/appStoreVersions",
            params={
                "fields[appStoreVersions]": "appStoreState,versionString",
                "limit": 200,
            },
        )
        if not versions:
            return None

        live = [
            v
            for v in versions
            if v.get("attributes", {}).get("appStoreState") == "READY_FOR_SALE"
        ]
        version = (live or versions)[0]
        version_id = version["id"]

        localizations = await self.client._get_all_pages(
            f"/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            params={
                "fields[appStoreVersionLocalizations]": "locale",
                "limit": 200,
            },
        )
        for loc in localizations:
            if loc.get("attributes", {}).get("locale") == locale:
                return loc["id"]
        return None

    async def get_cpp_localization_id(
        self, cpp_id: str, locale: str
    ) -> str | None:
        """Resolve a CPP's localization id for a locale.

        Walks the CPP's versions (newest first) then their localizations
        to find the one whose ``locale`` matches. Returns the
        ``appCustomProductPageLocalizations`` id consumed by
        :meth:`get_cpp_screenshots`, or ``None`` when no matching
        version/locale exists.
        """
        versions = await self.list_versions(cpp_id)
        for version in versions:
            localizations = await self.list_localizations(version["id"])
            for loc in localizations:
                if loc.get("attributes", {}).get("locale") == locale:
                    return loc["id"]
        return None

    # ------------------------------------------------------------------
    # Screenshot upload (3-step reserve -> PUT -> commit)
    # ------------------------------------------------------------------

    async def _find_or_create_screenshot_set(
        self, localization_id: str, display_type: str
    ) -> str:
        """Find (or create) the ``appScreenshotSet`` for a display type.

        Screenshots hang off a set keyed by ``screenshotDisplayType`` under
        the CPP localization. We reuse an existing set for the requested
        device family if present, else create a new one.

        ``GET /v1/appCustomProductPageLocalizations/{id}/appScreenshotSets``
        ``POST /v1/appScreenshotSets``

        Returns:
            The ``appScreenshotSets`` id to attach the new screenshot to.
        """
        existing = await self.client._get_all_pages(
            f"/appCustomProductPageLocalizations/{localization_id}"
            "/appScreenshotSets",
            params={
                "fields[appScreenshotSets]": "screenshotDisplayType",
                "limit": 200,
            },
        )
        for set_obj in existing:
            attrs = set_obj.get("attributes", {})
            if attrs.get("screenshotDisplayType") == display_type:
                return set_obj["id"]

        body = {
            "data": {
                "type": "appScreenshotSets",
                "attributes": {
                    "screenshotDisplayType": display_type,
                },
                "relationships": {
                    "appCustomProductPageLocalization": {
                        "data": {
                            "type": "appCustomProductPageLocalizations",
                            "id": localization_id,
                        },
                    },
                },
            }
        }
        response = await self.client._post("/appScreenshotSets", json=body)
        return response["data"]["id"]

    async def upload_screenshot_to_cpp(
        self,
        localization_id: str,
        display_type: str,
        file_bytes: bytes,
        file_name: str,
    ) -> dict:
        """Upload a marketing screenshot to a CPP localization (3-step flow).

        Mirrors the review-screenshot reserve -> PUT -> commit flow in
        :mod:`app.services.asc.pricing`, applied to the standard
        ``appScreenshotSets``/``appScreenshots`` model:

        1. Find (or create) the ``appScreenshotSet`` for ``display_type``
           under the CPP localization.
        2. ``POST /v1/appScreenshots`` to reserve the asset (returns
           ``uploadOperations`` pre-signed PUT URLs).
        3. ``PUT`` the source bytes to each upload operation URL.
        4. ``PATCH`` ``uploaded=true`` with the source file's md5 checksum.

        Args:
            localization_id: An ``appCustomProductPageLocalizations`` id.
            display_type: Apple's ``screenshotDisplayType`` (e.g.
                ``APP_IPHONE_67``) identifying the device family.
            file_bytes: The raw screenshot bytes.
            file_name: The file name to register with Apple.

        Returns:
            The committed ``appScreenshots`` resource dict.
        """
        set_id = await self._find_or_create_screenshot_set(
            localization_id, display_type
        )

        # Apple requires md5 for the appScreenshots sourceFileChecksum — this
        # is a content checksum for upload integrity, not a security primitive.
        checksum = hashlib.md5(file_bytes).hexdigest()  # noqa: S324

        # Step 1: Reserve the screenshot asset.
        reserve_body = {
            "data": {
                "type": "appScreenshots",
                "attributes": {
                    "fileName": file_name,
                    "fileSize": len(file_bytes),
                },
                "relationships": {
                    "appScreenshotSet": {
                        "data": {
                            "type": "appScreenshotSets",
                            "id": set_id,
                        },
                    },
                },
            }
        }
        reservation = await self.client._post(
            "/appScreenshots", json=reserve_body
        )

        screenshot_id = reservation["data"]["id"]
        operations = reservation["data"]["attributes"].get(
            "uploadOperations", []
        )

        # Step 2: Upload binary (use Apple's requested content type).
        for op in operations:
            content_type = "application/octet-stream"
            for hdr in op.get("requestHeaders", []):
                if hdr.get("name", "").lower() == "content-type":
                    content_type = hdr["value"]
            offset = op.get("offset", 0)
            await self.client._put_binary(
                op["url"],
                file_bytes[offset:offset + op["length"]],
                content_type=content_type,
            )

        # Step 3: Commit.
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
        response = await self.client._patch(
            f"/appScreenshots/{screenshot_id}",
            json=commit_body,
        )
        return response.get("data", {})
