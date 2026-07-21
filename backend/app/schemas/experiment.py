"""Schemas for Product Page Optimization (PPO) — App Store Version Experiments.

Mirrors the request/response shapes consumed by ``app/mcp/tools/experiment.py``
and ``app/api/v1/experiment.py`` and produced by
``app/services/asc/experiment.py``. PPO resources follow the App Store Connect
resource hierarchy (note the v2/v1 split — experiments are v2, treatments and
treatment localizations are v1):

    appStoreVersionExperiments (v2)
      -> appStoreVersionExperimentTreatments (v1)
        -> appStoreVersionExperimentTreatmentLocalizations (v1)
          -> appScreenshotSets -> appScreenshots

Treatment screenshots reuse the standard set/asset model, so the shared
:class:`Screenshot` / :class:`ScreenshotSet` models (and the
``screenshotDisplayType`` validation) come from ``app.schemas.screenshots`` —
identical to CPP.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Shared screenshot models + upload guard-rails (single source of truth). Re-used
# verbatim from CPP; a PPO treatment screenshot is the same appScreenshots asset.
from app.schemas.screenshots import (  # noqa: F401
    MAX_SCREENSHOT_BYTES,
    MAX_SCREENSHOT_FILES,
    Screenshot,
    ScreenshotSet,
    ScreenshotSetListResponse,
    is_valid_display_type,
)

# The only experiment states a client may PATCH the resource into (lifecycle
# transitions): submit-for-review (``WAITING_FOR_REVIEW``) and stop
# (``STOPPED``). Every other state Apple exposes is server-assigned and
# read-only. Apple's per-experiment treatment cap lives on the service
# (``app.services.asc.experiment.MAX_TREATMENTS``) where it is enforced.
SETTABLE_EXPERIMENT_STATES: frozenset[str] = frozenset(
    {"WAITING_FOR_REVIEW", "STOPPED"}
)


# ------------------------------------------------------------------
# Experiment
# ------------------------------------------------------------------


class ExperimentCreateIn(BaseModel):
    """Request body for creating a Product Page Optimization experiment."""

    name: str = Field(..., min_length=1, max_length=64)
    # Percentage of the app's traffic entered into the test (1–100).
    traffic_proportion: int = Field(50, ge=1, le=100)
    # Apple ``Platform`` enum; PPO is iOS/tvOS/visionOS — default to IOS.
    platform: str = "IOS"


class ExperimentUpdateIn(BaseModel):
    """Request body for updating an experiment (only provided fields are sent).

    ``state`` drives lifecycle transitions: ``WAITING_FOR_REVIEW`` submits the
    experiment for review, ``STOPPED`` stops a running experiment.
    """

    name: str | None = Field(None, min_length=1, max_length=64)
    traffic_proportion: int | None = Field(None, ge=1, le=100)
    state: str | None = None


class ExperimentResponse(BaseModel):
    """A single experiment (``appStoreVersionExperiments`` v2 resource).

    ``start_date`` / ``end_date`` / ``state`` / ``review_required`` are
    server-assigned; the API exposes no results/metrics (impressions,
    conversion, confidence) — those live only in the ASC Analytics UI.
    """

    id: str
    name: str | None = None
    platform: str | None = None
    traffic_proportion: int | None = None
    state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    review_required: bool | None = None


class ExperimentListResponse(BaseModel):
    """Response wrapper for a list of experiments."""

    items: list[ExperimentResponse] = Field(default_factory=list)


# ------------------------------------------------------------------
# Treatment
# ------------------------------------------------------------------


class TreatmentCreateIn(BaseModel):
    """Request body for creating an experiment treatment (a test variant).

    ``app_icon_name`` is the name of an alternate app icon shipped in the
    published build; omit it to keep the default icon and test only screenshots.
    """

    name: str = Field(..., min_length=1, max_length=64)
    app_icon_name: str | None = None


class TreatmentUpdateIn(BaseModel):
    """Request body for updating a treatment (only provided fields are sent)."""

    name: str | None = Field(None, min_length=1, max_length=64)
    app_icon_name: str | None = None


class TreatmentResponse(BaseModel):
    """A single treatment (``appStoreVersionExperimentTreatments`` resource)."""

    id: str
    name: str | None = None
    app_icon_name: str | None = None
    promoted_date: str | None = None


class TreatmentListResponse(BaseModel):
    """Response wrapper for a list of treatments."""

    items: list[TreatmentResponse] = Field(default_factory=list)


# ------------------------------------------------------------------
# Treatment localization
# ------------------------------------------------------------------


class TreatmentLocalizationCreateIn(BaseModel):
    """Request body for creating a treatment localization.

    ``locale`` is a standard App Store locale (``en-US``, ``de-DE``) — NOT an
    alpha-2 territory code; PPO varies presentation by locale, not territory.
    """

    locale: str = Field(..., min_length=2, max_length=10)


class TreatmentLocalizationResponse(BaseModel):
    """A treatment localization (``...TreatmentLocalizations`` resource)."""

    id: str
    locale: str | None = None


class TreatmentLocalizationListResponse(BaseModel):
    """Response wrapper for a list of treatment localizations."""

    items: list[TreatmentLocalizationResponse] = Field(default_factory=list)


class EnsureTreatmentLocalizationResponse(BaseModel):
    """Resolved (found-or-created) localization for a treatment + locale.

    ``localization_id`` is the ``...TreatmentLocalizations`` id that the
    screenshot-upload path requires.
    """

    treatment_id: str
    localization_id: str
    locale: str


class TreatmentFromUploadResponse(BaseModel):
    """Result of populating a treatment localization from an uploaded set."""

    treatment_id: str
    localization_id: str
    locale: str
    uploaded_count: int


# ------------------------------------------------------------------
# Shaping helpers (raw JSON:API resource -> response model)
# ------------------------------------------------------------------
#
# Shared by both the MCP tools (``app/mcp/tools/experiment.py``) and the REST
# router (``app/api/v1/experiment.py``) so the attribute mapping lives once.


def shape_experiment(resource: dict) -> ExperimentResponse:
    """Shape a raw ``appStoreVersionExperiments`` resource into a response."""
    attrs = resource.get("attributes", {})
    return ExperimentResponse(
        id=resource.get("id", ""),
        name=attrs.get("name"),
        platform=attrs.get("platform"),
        traffic_proportion=attrs.get("trafficProportion"),
        state=attrs.get("state"),
        start_date=attrs.get("startDate"),
        end_date=attrs.get("endDate"),
        review_required=attrs.get("reviewRequired"),
    )


def shape_treatment(resource: dict) -> TreatmentResponse:
    """Shape a raw ``appStoreVersionExperimentTreatments`` resource."""
    attrs = resource.get("attributes", {})
    return TreatmentResponse(
        id=resource.get("id", ""),
        name=attrs.get("name"),
        app_icon_name=attrs.get("appIconName"),
        promoted_date=attrs.get("promotedDate"),
    )
