"""Read + write ASC service for AppInfo and AppStoreVersion metadata trees."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient


# ----------------------------------------------------------------------
# State machine constants (exported so route layer can reuse)
# ----------------------------------------------------------------------

# Fully-editable version states: all version-scoped fields may be written.
#
# NOTE: ``appStoreState`` / ``appVersionState`` is API-version sensitive and
# differs between tenants and ASC API versions. The strings below must be
# verified against the live tenant. We deliberately FAIL CLOSED for any state
# not listed in either set (treated as not editable) — see
# ``_guard_version_localization_update`` and the snapshot's editable_fields
# computation, which share these constants so they can never disagree.
#
# ``WAITING_FOR_REVIEW`` is intentionally NOT here: once a version is in review
# the safe assumption is that only the live/promo path applies, so it falls
# through to "not editable" rather than being treated as fully editable.
EDITABLE_VERSION_STATES: frozenset[str] = frozenset({
    "PREPARE_FOR_SUBMISSION",
    "READY_FOR_REVIEW",
    "DEVELOPER_REJECTED",
    "REJECTED",
    "METADATA_REJECTED",
})

# Live / locked states where ONLY ``promotional_text`` is mutable. Apple has
# used several names for the live/post-approval lifecycle over time; we treat
# all of them as promo-only so the snapshot's fallback fetch (which queries
# these states) yields ``editable_fields == ["promotional_text"]``.
READ_ONLY_VERSION_STATES_PROMO_ONLY: frozenset[str] = frozenset({
    "READY_FOR_SALE",
    "READY_FOR_DISTRIBUTION",
    "PENDING_DEVELOPER_RELEASE",
})

PROMO_ONLY_FIELDS_ON_LIVE: frozenset[str] = frozenset({"promotionalText"})


class MetadataNotEditableError(Exception):
    """Raised when a metadata write is attempted in a non-editable state.

    The router translates this into a 409 CONFLICT response.
    """


class ASCMetadataService:
    """Read + write service for AppInfo / AppStoreVersion trees.

    Read methods return the raw JSON:API ``data`` array (list of resource
    objects with ``{id, type, attributes, relationships}``) so the
    snapshot service can consume Apple's structure unchanged.

    Write methods return the created/updated resource dict (or ``None``
    for deletes), and enforce the ASC version-state machine before
    making the network call.
    """

    def __init__(self, client: ASCClient) -> None:
        self.client = client

    # ------------------------------------------------------------------
    # AppInfo tree — read
    # ------------------------------------------------------------------

    async def list_app_infos(self, asc_app_id: str) -> list[dict]:
        """Fetch all AppInfo records for an app.

        ``GET /v1/apps/{asc_app_id}/appInfos``

        An app typically has multiple AppInfos: one per app-store state
        (e.g. ``READY_FOR_DISTRIBUTION``, ``PREPARE_FOR_SUBMISSION``).
        Returns the raw JSON:API resource list.
        """
        return await self.client._get_all_pages(
            f"/apps/{asc_app_id}/appInfos",
            params={"limit": 200},
        )

    async def list_app_info_localizations(
        self, app_info_id: str
    ) -> list[dict]:
        """Fetch all locale entries under a single AppInfo.

        ``GET /v1/appInfos/{app_info_id}/appInfoLocalizations``

        Each localization carries the locale-scoped fields (name,
        subtitle, privacyPolicyUrl, etc.). Paginated.
        """
        return await self.client._get_all_pages(
            f"/appInfos/{app_info_id}/appInfoLocalizations",
            params={"limit": 200},
        )

    # ------------------------------------------------------------------
    # AppStoreVersion tree — read
    # ------------------------------------------------------------------

    async def list_app_store_versions(
        self,
        asc_app_id: str,
        filter_states: list[str] | None = None,
    ) -> list[dict]:
        """Fetch app store versions for an app, optionally filtered by state.

        ``GET /v1/apps/{asc_app_id}/appStoreVersions``

        Args:
            asc_app_id: The App Store Connect numeric app identifier.
            filter_states: Optional list of ``appStoreState`` values
                (e.g. ``["PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW"]``)
                applied as ``filter[appStoreState]=A,B,C``.

        Returns the raw JSON:API resource list.
        """
        params: dict[str, str | int] = {"limit": 200}
        if filter_states:
            params["filter[appStoreState]"] = ",".join(filter_states)
        return await self.client._get_all_pages(
            f"/apps/{asc_app_id}/appStoreVersions",
            params=params,
        )

    async def list_version_localizations(
        self, version_id: str
    ) -> list[dict]:
        """Fetch all locale entries under a single AppStoreVersion.

        ``GET /v1/appStoreVersions/{version_id}/appStoreVersionLocalizations``

        Each localization carries version-scoped fields (description,
        keywords, promotionalText, whatsNew, marketingUrl,
        supportUrl). Paginated.
        """
        return await self.client._get_all_pages(
            f"/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            params={"limit": 200},
        )

    # ------------------------------------------------------------------
    # AppInfo Localizations — write
    # ------------------------------------------------------------------

    async def create_app_info_localization(
        self,
        app_info_id: str,
        locale: str,
        attributes: dict,
    ) -> dict:
        """Create a new locale row under an AppInfo.

        ``POST /v1/appInfoLocalizations``

        Args:
            app_info_id: Parent AppInfo id.
            locale: BCP-47 locale tag (e.g. ``"en-US"``).
            attributes: Locale-scoped fields (``name``, ``subtitle``,
                ``privacyPolicyUrl``, ``privacyPolicyText``, ...). The
                ``locale`` key is injected by this method, so callers
                may omit it.

        Returns:
            The created appInfoLocalization resource dict.
        """
        merged: dict = {**attributes, "locale": locale}
        body = {
            "data": {
                "type": "appInfoLocalizations",
                "attributes": merged,
                "relationships": {
                    "appInfo": {
                        "data": {"type": "appInfos", "id": app_info_id},
                    },
                },
            }
        }
        return await self.client._post("/appInfoLocalizations", json=body)

    async def update_app_info_localization(
        self,
        localization_id: str,
        attributes: dict,
    ) -> dict:
        """Patch a single AppInfo localization.

        ``PATCH /v1/appInfoLocalizations/{localization_id}``

        Only the keys present in ``attributes`` are sent; ``locale`` is
        immutable and should not appear here.

        Returns:
            The updated appInfoLocalization resource dict.
        """
        body = {
            "data": {
                "type": "appInfoLocalizations",
                "id": localization_id,
                "attributes": attributes,
            }
        }
        return await self.client._patch(
            f"/appInfoLocalizations/{localization_id}", json=body
        )

    async def delete_app_info_localization(
        self, localization_id: str
    ) -> None:
        """Delete an AppInfo localization.

        ``DELETE /v1/appInfoLocalizations/{localization_id}``
        """
        await self.client._delete(
            f"/appInfoLocalizations/{localization_id}"
        )

    # ------------------------------------------------------------------
    # AppStoreVersion Localizations — write
    # ------------------------------------------------------------------

    async def create_version_localization(
        self,
        version_id: str,
        locale: str,
        attributes: dict,
    ) -> dict:
        """Create a new locale row under an AppStoreVersion.

        ``POST /v1/appStoreVersionLocalizations``

        Args:
            version_id: Parent AppStoreVersion id.
            locale: BCP-47 locale tag (e.g. ``"en-US"``).
            attributes: Version-scoped fields (``description``,
                ``keywords``, ``promotionalText``, ``whatsNew``,
                ``marketingUrl``, ``supportUrl``). The ``locale`` key
                is injected by this method, so callers may omit it.

        Returns:
            The created appStoreVersionLocalization resource dict.
        """
        merged: dict = {**attributes, "locale": locale}
        body = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "attributes": merged,
                "relationships": {
                    "appStoreVersion": {
                        "data": {
                            "type": "appStoreVersions",
                            "id": version_id,
                        },
                    },
                },
            }
        }
        return await self.client._post(
            "/appStoreVersionLocalizations", json=body
        )

    async def update_version_localization(
        self,
        localization_id: str,
        attributes: dict,
        version_state: str | None = None,
    ) -> dict:
        """Patch a single AppStoreVersion localization (state-guarded).

        ``PATCH /v1/appStoreVersionLocalizations/{localization_id}``

        State-machine guard (runs BEFORE the network call):

        * ``version_state is None`` — caller does not know the state;
          delegate enforcement to ASC.
        * ``version_state in EDITABLE_VERSION_STATES`` — pass through.
        * ``version_state in READ_ONLY_VERSION_STATES_PROMO_ONLY`` —
          only ``promotionalText`` may appear in ``attributes``;
          otherwise raise :class:`MetadataNotEditableError`.
        * any other value — raise :class:`MetadataNotEditableError`.

        Args:
            localization_id: Target localization id.
            attributes: Fields to patch (``locale`` is immutable).
            version_state: Optional ``appStoreState`` of the parent
                version, used to enforce client-side editability.

        Returns:
            The updated appStoreVersionLocalization resource dict.

        Raises:
            MetadataNotEditableError: When the guard rejects the write.
        """
        self._guard_version_localization_update(version_state, attributes)

        body = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "id": localization_id,
                "attributes": attributes,
            }
        }
        return await self.client._patch(
            f"/appStoreVersionLocalizations/{localization_id}", json=body
        )

    async def delete_version_localization(
        self, localization_id: str
    ) -> None:
        """Delete an AppStoreVersion localization.

        ``DELETE /v1/appStoreVersionLocalizations/{localization_id}``
        """
        await self.client._delete(
            f"/appStoreVersionLocalizations/{localization_id}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _guard_version_localization_update(
        version_state: str | None,
        attributes: dict,
    ) -> None:
        """Enforce the ASC version-state machine for localization PATCH.

        See :meth:`update_version_localization` for the rules. Pulled
        out so route handlers and future tests can call it directly
        without a network round-trip.
        """
        if version_state is None:
            return
        if version_state in EDITABLE_VERSION_STATES:
            return
        if version_state in READ_ONLY_VERSION_STATES_PROMO_ONLY:
            extra_keys = sorted(
                set(attributes.keys()) - PROMO_ONLY_FIELDS_ON_LIVE
            )
            if extra_keys:
                raise MetadataNotEditableError(
                    "Only promotionalText is editable when version is in "
                    f"{version_state}; got fields: {extra_keys}"
                )
            return
        raise MetadataNotEditableError(
            f"Version state {version_state} is not editable"
        )
