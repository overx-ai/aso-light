"""Service for managing Product Page Optimization (PPO) via the ASC API.

PPO is Apple's A/B testing of an app's product page (screenshots, app-preview
videos, and app-icon variants), modelled as **App Store Version Experiments**
with **Treatments**. This service wraps the JSON:API calls for the resource
tree:

    appStoreVersionExperiments (v2)
      -> appStoreVersionExperimentTreatments (v1)
        -> appStoreVersionExperimentTreatmentLocalizations (v1)
          -> appScreenshotSets -> appScreenshots

**Version split (important):** experiment CRUD lives on the app-level **v2**
resource, while treatments and treatment localizations are **v1**. The
:class:`ASCClient` base URL is ``.../v1``; v2 calls swap the prefix via
:attr:`base_v2` and pass the absolute URL (same idiom as the IAP code in
``app.services.asc.pricing``). httpx uses an absolute URL as-is, so the client's
throttling/auth/pagination all still apply.

**No results via API:** Apple exposes no endpoint for experiment results
(impressions, conversion, confidence) — those live only in the ASC Analytics
UI, so this service intentionally has no results-reading method.

Treatment screenshots reuse the standard set/asset model, so the upload +
shaping delegate to :mod:`app.services.asc.screenshots` (shared with CPP).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.asc import screenshots as shots
from app.services.asc.errors import ChildResourceNotFoundError

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)

# JSON:API resource types + relationship keys for the PPO resource tree. The
# v2 experiment keeps the ``appStoreVersionExperiments`` type string (the "V2"
# only appears in the URL path and request-wrapper name, not the ``type``).
_EXPERIMENT_TYPE = "appStoreVersionExperiments"
_TREATMENT_TYPE = "appStoreVersionExperimentTreatments"
_LOCALIZATION_TYPE = "appStoreVersionExperimentTreatmentLocalizations"
# A treatment links to its parent v2 experiment via this relationship.
_TREATMENT_EXPERIMENT_RELATIONSHIP = "appStoreVersionExperimentV2"
# A treatment localization links back to its treatment via this relationship.
_LOCALIZATION_TREATMENT_RELATIONSHIP = "appStoreVersionExperimentTreatment"
# A screenshot set links back to its treatment localization via this key.
_SET_LOCALIZATION_RELATIONSHIP = "appStoreVersionExperimentTreatmentLocalization"

# Apple allows at most this many treatments (variants) per experiment.
MAX_TREATMENTS = 3


class ExperimentLimitError(Exception):
    """Raised when an operation would exceed an Apple PPO limit (e.g. >3 treatments)."""


class ASCExperimentService:
    """Service for managing App Store Version Experiments (PPO) via the ASC API.

    Read methods return raw JSON:API ``data`` (list or dict); the screenshot
    helpers return already-shaped dicts (resolving ``included`` assets + CDN
    source URLs) via the shared screenshot module.
    """

    def __init__(self, client: ASCClient):
        self.client = client

    @property
    def base_v2(self) -> str:
        """The ASC API v2 base URL (experiment CRUD lives here, not v1)."""
        return self.client.BASE_URL.replace("/v1", "/v2")

    # ------------------------------------------------------------------
    # Child-membership guards (IDOR protection)
    #
    # Every REST/MCP handler verifies the parent App belongs to the caller,
    # then passes a raw experiment / treatment / localization id straight to
    # ASC. Within one Apple team that would let a caller owning app A read or
    # mutate app B's experiment by authorizing against A. Each guard re-lists
    # the parent's children and asserts membership before the operation,
    # raising ``ChildResourceNotFoundError`` (mapped to 404 / ToolError). This
    # mirrors ``ASCPricingService``'s ``_assert_member`` pattern; the extra ASC
    # GET is the accepted cost of closing the IDOR.
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_member(
        child_id: str, member_ids: set[str], not_found_message: str,
    ) -> None:
        """Raise ``ChildResourceNotFoundError`` unless ``child_id`` is a member."""
        if child_id not in member_ids:
            raise ChildResourceNotFoundError(not_found_message)

    async def assert_experiment_in_app(
        self, asc_app_id: str, experiment_id: str,
    ) -> None:
        """Assert ``experiment_id`` belongs to ``asc_app_id``."""
        existing = await self.list_experiments(asc_app_id)
        self._assert_member(
            experiment_id,
            {item["id"] for item in existing},
            "Experiment not found for this app",
        )

    async def assert_treatment_in_experiment(
        self, experiment_id: str, treatment_id: str,
    ) -> None:
        """Assert ``treatment_id`` belongs to ``experiment_id``."""
        existing = await self.list_treatments(experiment_id)
        self._assert_member(
            treatment_id,
            {item["id"] for item in existing},
            "Treatment not found for this experiment",
        )

    async def assert_localization_in_treatment(
        self, treatment_id: str, localization_id: str,
    ) -> None:
        """Assert ``localization_id`` belongs to ``treatment_id``."""
        existing = await self.list_treatment_localizations(treatment_id)
        self._assert_member(
            localization_id,
            {item["id"] for item in existing},
            "Localization not found for this treatment",
        )

    # ------------------------------------------------------------------
    # Experiments (v2)
    # ------------------------------------------------------------------

    async def list_experiments(self, asc_app_id: str) -> list[dict]:
        """Fetch the experiments for an app.

        ``GET /v1/apps/{asc_app_id}/appStoreVersionExperimentsV2``

        The app-level list entry point is on the v1 host even though the
        experiment resource itself is v2.

        Returns:
            List of JSON:API resource objects with id + attributes.
        """
        return await self.client._get_all_pages(
            f"/apps/{asc_app_id}/appStoreVersionExperimentsV2",
            params={
                "fields[appStoreVersionExperiments]": (
                    "name,platform,trafficProportion,startDate,endDate,"
                    "reviewRequired,state"
                ),
                "limit": 200,
            },
        )

    async def get_experiment(self, experiment_id: str) -> dict:
        """Fetch a single experiment.

        ``GET /v2/appStoreVersionExperiments/{experiment_id}``
        """
        response = await self.client._get(
            f"{self.base_v2}/appStoreVersionExperiments/{experiment_id}",
            params={
                "fields[appStoreVersionExperiments]": (
                    "name,platform,trafficProportion,startDate,endDate,"
                    "reviewRequired,state"
                ),
            },
        )
        return response.get("data", {})

    async def create_experiment(
        self,
        asc_app_id: str,
        name: str,
        traffic_proportion: int,
        platform: str = "IOS",
    ) -> dict:
        """Create a Product Page Optimization experiment.

        ``POST /v2/appStoreVersionExperiments``

        Apple returns **409** if another experiment for the app is already in a
        draft state (one draft at a time); that surfaces as an
        :class:`ASCAPIError` for the caller to translate.

        Returns:
            The created ``appStoreVersionExperiments`` resource dict.
        """
        body = {
            "data": {
                "type": _EXPERIMENT_TYPE,
                "attributes": {
                    "name": name,
                    "trafficProportion": traffic_proportion,
                    "platform": platform,
                },
                "relationships": {
                    "app": {"data": {"type": "apps", "id": asc_app_id}},
                },
            }
        }
        response = await self.client._post(
            f"{self.base_v2}/appStoreVersionExperiments", json=body
        )
        return response.get("data", {})

    async def update_experiment(
        self,
        experiment_id: str,
        name: str | None = None,
        traffic_proportion: int | None = None,
        state: str | None = None,
    ) -> dict:
        """Update an experiment (only provided attributes are sent).

        ``PATCH /v2/appStoreVersionExperiments/{experiment_id}``

        ``state`` drives lifecycle transitions — ``WAITING_FOR_REVIEW`` submits
        for review, ``STOPPED`` stops a running experiment.

        Returns:
            The updated ``appStoreVersionExperiments`` resource dict.
        """
        attributes: dict[str, object] = {}
        if name is not None:
            attributes["name"] = name
        if traffic_proportion is not None:
            attributes["trafficProportion"] = traffic_proportion
        if state is not None:
            attributes["state"] = state
        if not attributes:
            raise ValueError("update_experiment called with no fields to update")
        body = {
            "data": {
                "type": _EXPERIMENT_TYPE,
                "id": experiment_id,
                "attributes": attributes,
            }
        }
        response = await self.client._patch(
            f"{self.base_v2}/appStoreVersionExperiments/{experiment_id}",
            json=body,
        )
        return response.get("data", {})

    async def submit_experiment_for_review(self, experiment_id: str) -> dict:
        """Submit an experiment for App Review (``state = WAITING_FOR_REVIEW``)."""
        return await self.update_experiment(
            experiment_id, state="WAITING_FOR_REVIEW"
        )

    async def stop_experiment(self, experiment_id: str) -> dict:
        """Stop a running experiment (``state = STOPPED``)."""
        return await self.update_experiment(experiment_id, state="STOPPED")

    async def delete_experiment(self, experiment_id: str) -> None:
        """Delete an experiment.

        ``DELETE /v2/appStoreVersionExperiments/{experiment_id}``

        Apple only allows deletion **before** the experiment starts; a started
        experiment returns an error (surfaced as an :class:`ASCAPIError`).
        """
        await self.client._delete(
            f"{self.base_v2}/appStoreVersionExperiments/{experiment_id}"
        )

    # ------------------------------------------------------------------
    # Treatments (create/update/delete v1; list via the v2 experiment rel)
    # ------------------------------------------------------------------

    async def list_treatments(self, experiment_id: str) -> list[dict]:
        """Fetch the treatments (variants) of an experiment.

        Read through the **v2** experiment relationship (treatment
        create/update/delete are v1 top-level, but the list hangs off v2):
        ``GET /v2/appStoreVersionExperiments/{id}/appStoreVersionExperimentTreatments``
        """
        return await self.client._get_all_pages(
            f"{self.base_v2}/appStoreVersionExperiments/{experiment_id}"
            "/appStoreVersionExperimentTreatments",
            params={
                "fields[appStoreVersionExperimentTreatments]": (
                    "name,appIconName,promotedDate"
                ),
                "limit": 50,
            },
        )

    async def create_treatment(
        self,
        experiment_id: str,
        name: str,
        app_icon_name: str | None = None,
    ) -> dict:
        """Create a treatment under an experiment.

        ``POST /v1/appStoreVersionExperimentTreatments``

        Enforces Apple's ``MAX_TREATMENTS`` cap up-front (counts existing
        treatments first) so the caller gets a clean error rather than an opaque
        ASC rejection.

        Returns:
            The created ``appStoreVersionExperimentTreatments`` resource dict.
        """
        existing = await self.list_treatments(experiment_id)
        if len(existing) >= MAX_TREATMENTS:
            raise ExperimentLimitError(
                f"Experiment already has {len(existing)} treatments "
                f"(Apple allows at most {MAX_TREATMENTS})."
            )
        attributes: dict[str, object] = {"name": name}
        if app_icon_name is not None:
            attributes["appIconName"] = app_icon_name
        body = {
            "data": {
                "type": _TREATMENT_TYPE,
                "attributes": attributes,
                "relationships": {
                    _TREATMENT_EXPERIMENT_RELATIONSHIP: {
                        "data": {"type": _EXPERIMENT_TYPE, "id": experiment_id},
                    },
                },
            }
        }
        response = await self.client._post(
            "/appStoreVersionExperimentTreatments", json=body
        )
        return response.get("data", {})

    async def update_treatment(
        self,
        treatment_id: str,
        name: str | None = None,
        app_icon_name: str | None = None,
    ) -> dict:
        """Update a treatment (only provided attributes are sent).

        ``PATCH /v1/appStoreVersionExperimentTreatments/{treatment_id}``
        """
        attributes: dict[str, object] = {}
        if name is not None:
            attributes["name"] = name
        if app_icon_name is not None:
            attributes["appIconName"] = app_icon_name
        if not attributes:
            raise ValueError("update_treatment called with no fields to update")
        body = {
            "data": {
                "type": _TREATMENT_TYPE,
                "id": treatment_id,
                "attributes": attributes,
            }
        }
        response = await self.client._patch(
            f"/appStoreVersionExperimentTreatments/{treatment_id}", json=body
        )
        return response.get("data", {})

    async def delete_treatment(self, treatment_id: str) -> None:
        """Delete a treatment.

        ``DELETE /v1/appStoreVersionExperimentTreatments/{treatment_id}``
        """
        await self.client._delete(
            f"/appStoreVersionExperimentTreatments/{treatment_id}"
        )

    # ------------------------------------------------------------------
    # Treatment localizations (v1)
    # ------------------------------------------------------------------

    async def list_treatment_localizations(self, treatment_id: str) -> list[dict]:
        """Fetch the localizations of a treatment.

        ``GET /v1/appStoreVersionExperimentTreatments/{id}``
        ``/appStoreVersionExperimentTreatmentLocalizations``
        """
        return await self.client._get_all_pages(
            f"/appStoreVersionExperimentTreatments/{treatment_id}"
            "/appStoreVersionExperimentTreatmentLocalizations",
            params={
                "fields[appStoreVersionExperimentTreatmentLocalizations]": "locale",
                "limit": 200,
            },
        )

    async def create_treatment_localization(
        self, treatment_id: str, locale: str
    ) -> dict:
        """Create a localization under a treatment.

        ``POST /v1/appStoreVersionExperimentTreatmentLocalizations``

        Screenshot sets hang off the localization, so a localization for the
        target ``locale`` must exist before any screenshot can be uploaded.

        Returns:
            The created ``...TreatmentLocalizations`` resource dict.
        """
        body = {
            "data": {
                "type": _LOCALIZATION_TYPE,
                "attributes": {"locale": locale},
                "relationships": {
                    _LOCALIZATION_TREATMENT_RELATIONSHIP: {
                        "data": {"type": _TREATMENT_TYPE, "id": treatment_id},
                    },
                },
            }
        }
        response = await self.client._post(
            "/appStoreVersionExperimentTreatmentLocalizations", json=body
        )
        return response.get("data", {})

    async def _resolve_localization(
        self, treatment_id: str, locale: str
    ) -> tuple[str, bool]:
        """Resolve (or create) a treatment localization id for a locale.

        Returns ``(localization_id, created)`` — ``created`` is ``True`` only
        when a new localization was created (used to scope cleanup on the
        from-upload path so a *reused* localization is never deleted).
        """
        for loc in await self.list_treatment_localizations(treatment_id):
            if loc.get("attributes", {}).get("locale") == locale:
                return loc["id"], False
        created = await self.create_treatment_localization(treatment_id, locale)
        return created["id"], True

    async def find_or_create_localization_id(
        self, treatment_id: str, locale: str
    ) -> str:
        """Resolve (or create) the localization id for a treatment + locale.

        Idempotent: repeat calls for the same ``(treatment_id, locale)`` return
        the same id.
        """
        localization_id, _created = await self._resolve_localization(
            treatment_id, locale
        )
        return localization_id

    async def delete_treatment_localization(self, localization_id: str) -> None:
        """Delete a treatment localization.

        ``DELETE /v1/appStoreVersionExperimentTreatmentLocalizations/{id}``
        """
        await self.client._delete(
            f"/appStoreVersionExperimentTreatmentLocalizations/{localization_id}"
        )

    # ------------------------------------------------------------------
    # Screenshots (shared set/asset model)
    # ------------------------------------------------------------------

    async def get_treatment_screenshots(self, localization_id: str) -> list[dict]:
        """Fetch the screenshot sets (+ assets) for a treatment localization.

        ``GET /v1/appStoreVersionExperimentTreatmentLocalizations/{id}``
        ``/appScreenshotSets?include=appScreenshots``

        Same shape as the CPP/default-page screenshots (shared shaping).
        """
        return await shots.fetch_screenshot_sets(
            self.client,
            f"/{_LOCALIZATION_TYPE}/{localization_id}/appScreenshotSets",
        )

    async def upload_screenshot_to_treatment(
        self,
        localization_id: str,
        display_type: str,
        file_bytes: bytes,
        file_name: str,
    ) -> dict:
        """Upload a screenshot to a treatment localization (3-step flow).

        Delegates to the shared reserve -> PUT -> commit upload with the PPO
        treatment-localization type + set relationship.

        Returns:
            The committed ``appScreenshots`` resource dict.
        """
        return await shots.upload_screenshot_to_localization(
            self.client,
            _LOCALIZATION_TYPE,
            localization_id,
            _SET_LOCALIZATION_RELATIONSHIP,
            display_type,
            file_bytes,
            file_name,
        )

    async def populate_treatment_from_upload(
        self,
        treatment_id: str,
        locale: str,
        display_type: str,
        files: list[tuple[str, bytes]],
    ) -> dict:
        """Ensure a treatment localization and upload a screenshot set into it.

        End-to-end flow mirroring CPP's ``create_cpp_with_screenshots``:

        1. Resolve (or create) the ``locale`` localization under the treatment.
        2. Upload each file into the ``appScreenshotSet`` for ``display_type``
           via the 3-step reserve -> PUT -> commit flow.

        If any upload fails and this call *created* the localization, the
        freshly-created (now half-populated) localization is best-effort deleted
        before the error is surfaced. A *reused* localization is left untouched.

        Returns:
            ``{"treatment_id", "localization_id", "locale", "uploaded_count"}``.
        """
        localization_id, created = await self._resolve_localization(
            treatment_id, locale
        )
        uploaded_count = 0
        try:
            for file_name, file_bytes in files:
                await self.upload_screenshot_to_treatment(
                    localization_id, display_type, file_bytes, file_name
                )
                uploaded_count += 1
        except Exception:
            if created:
                try:
                    await self.delete_treatment_localization(localization_id)
                except Exception:
                    logger.warning(
                        "Failed to clean up partial treatment localization %s "
                        "after upload error", localization_id,
                    )
            raise

        return {
            "treatment_id": treatment_id,
            "localization_id": localization_id,
            "locale": locale,
            "uploaded_count": uploaded_count,
        }
