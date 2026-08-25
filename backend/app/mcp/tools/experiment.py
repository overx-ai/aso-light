"""MCP tools for Product Page Optimization (PPO) — App Store Version Experiments.

Thin wrappers over :class:`app.services.asc.experiment.ASCExperimentService`.
Each tool resolves the local ``app_id`` to its owning credential via
``resolve_app`` (enforcing the ``app.credential_id -> credential.user_id``
chain), builds an :class:`ASCClient`, and converts ASC API failures into
``ToolError`` so MCP clients see a single-line message — identical to the CPP
tools.

Note: Apple exposes no experiment *results* via the API (impressions,
conversion, confidence live only in the ASC Analytics UI), so there is no
results-reading tool here — only configuration + lifecycle + treatment media.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp.exceptions import ToolError

from app.api.v1._deps import _get_asc_client_for_app
from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.schemas.experiment import (
    SETTABLE_EXPERIMENT_STATES,
    EnsureTreatmentLocalizationResponse,
    ExperimentListResponse,
    ExperimentResponse,
    Screenshot,
    ScreenshotSet,
    ScreenshotSetListResponse,
    TreatmentListResponse,
    TreatmentResponse,
    is_valid_display_type,
    shape_experiment,
    shape_treatment,
)
from app.schemas.screenshots import decode_screenshot_payload
from app.services.asc.errors import ASCAPIError, ChildResourceNotFoundError
from app.services.asc.experiment import (
    ASCExperimentService,
    ExperimentLimitError,
)
from app.services.asc.screenshots import build_source_url

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _experiment_service(
    app_id: int,
) -> AsyncIterator[tuple[ASCExperimentService, str]]:
    """Yield ``(service, asc_app_id)`` for the caller-owned app.

    Collapses the setup every experiment tool otherwise repeats: open a DB
    session, resolve ``app_id`` to its owning credential (enforcing the
    ``app.credential_id -> credential.user_id`` chain), open an
    :class:`ASCClient`, and build the service — while also funnelling ASC
    failures into a single-line ``ToolError``. Apple's failure (e.g. a 409 when
    another experiment is already in draft) surfaces as ``ASC API error: …`` and
    a child-membership violation (``ChildResourceNotFoundError`` from the IDOR
    guards) as its own message — never a traceback. The ``asc_app_id`` is handed
    back alongside the service because every tool needs it (to scope the ASC
    call or its IDOR membership assert).
    """
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        async with await _get_asc_client_for_app(app, session) as client:
            try:
                yield ASCExperimentService(client), app.asc_app_id
            except ChildResourceNotFoundError as exc:
                raise ToolError(str(exc))
            except ASCAPIError as exc:
                raise ToolError(f"ASC API error: {exc.message}")


# ==================================================================
# Experiments — CRUD + lifecycle
# ==================================================================


@mcp.tool(name="experiment_list")
async def list_experiments(app_id: int) -> ExperimentListResponse:
    """List the Product Page Optimization experiments for an app."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        resources = await service.list_experiments(asc_app_id)
    return ExperimentListResponse(
        items=[shape_experiment(r) for r in resources]
    )


@mcp.tool(name="experiment_get")
async def get_experiment(app_id: int, experiment_id: str) -> ExperimentResponse:
    """Fetch a single experiment by its ASC id."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resource = await service.get_experiment(experiment_id)
    return shape_experiment(resource)


@mcp.tool(name="experiment_create")
async def create_experiment(
    app_id: int,
    name: str,
    traffic_proportion: int = 50,
    platform: str = "IOS",
) -> ExperimentResponse:
    """Create a Product Page Optimization experiment.

    ``traffic_proportion`` is the percentage of the app's traffic entered into
    the test (1–100). Apple allows only one draft experiment per app at a time —
    a second draft is rejected with a clear ASC error.
    """
    if not 1 <= traffic_proportion <= 100:
        raise ToolError("traffic_proportion must be between 1 and 100.")
    async with _experiment_service(app_id) as (service, asc_app_id):
        resource = await service.create_experiment(
            asc_app_id, name, traffic_proportion, platform=platform,
        )
    return shape_experiment(resource)


@mcp.tool(name="experiment_update")
async def update_experiment(
    app_id: int,
    experiment_id: str,
    name: str | None = None,
    traffic_proportion: int | None = None,
    state: str | None = None,
) -> ExperimentResponse:
    """Update an experiment's name, traffic proportion, and/or lifecycle state.

    ``state`` may only be ``WAITING_FOR_REVIEW`` (submit for review) or
    ``STOPPED`` (stop a running experiment); other states are server-assigned.
    """
    if state is not None and state not in SETTABLE_EXPERIMENT_STATES:
        raise ToolError(
            "state must be one of "
            f"{sorted(SETTABLE_EXPERIMENT_STATES)} (or omitted)."
        )
    if traffic_proportion is not None and not 1 <= traffic_proportion <= 100:
        raise ToolError("traffic_proportion must be between 1 and 100.")
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        try:
            resource = await service.update_experiment(
                experiment_id,
                name=name,
                traffic_proportion=traffic_proportion,
                state=state,
            )
        except ValueError as exc:
            raise ToolError(str(exc))
    return shape_experiment(resource)


@mcp.tool(name="experiment_submit_for_review")
async def submit_experiment_for_review(
    app_id: int, experiment_id: str
) -> ExperimentResponse:
    """Submit an experiment for App Review (state -> WAITING_FOR_REVIEW)."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resource = await service.submit_experiment_for_review(experiment_id)
    return shape_experiment(resource)


@mcp.tool(name="experiment_stop")
async def stop_experiment(app_id: int, experiment_id: str) -> ExperimentResponse:
    """Stop a running experiment (state -> STOPPED)."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resource = await service.stop_experiment(experiment_id)
    return shape_experiment(resource)


@mcp.tool(name="experiment_delete")
async def delete_experiment(app_id: int, experiment_id: str) -> dict[str, bool]:
    """Delete an experiment (only allowed before it starts)."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.delete_experiment(experiment_id)
    return {"deleted": True}


# ==================================================================
# Treatments
# ==================================================================


@mcp.tool(name="experiment_list_treatments")
async def list_treatments(
    app_id: int, experiment_id: str
) -> TreatmentListResponse:
    """List the treatments (variants) of an experiment."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resources = await service.list_treatments(experiment_id)
    return TreatmentListResponse(
        items=[shape_treatment(r) for r in resources]
    )


@mcp.tool(name="experiment_create_treatment")
async def create_treatment(
    app_id: int,
    experiment_id: str,
    name: str,
    app_icon_name: str | None = None,
) -> TreatmentResponse:
    """Create a treatment (variant) under an experiment.

    Apple allows at most 3 treatments per experiment; a 4th is rejected with a
    clear error. ``app_icon_name`` (optional) references an alternate app icon
    already shipped in the published build.
    """
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        try:
            resource = await service.create_treatment(
                experiment_id, name, app_icon_name=app_icon_name,
            )
        except ExperimentLimitError as exc:
            raise ToolError(str(exc))
    return shape_treatment(resource)


@mcp.tool(name="experiment_update_treatment")
async def update_treatment(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    name: str | None = None,
    app_icon_name: str | None = None,
) -> TreatmentResponse:
    """Update a treatment's name and/or alternate app-icon name."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        try:
            resource = await service.update_treatment(
                treatment_id, name=name, app_icon_name=app_icon_name,
            )
        except ValueError as exc:
            raise ToolError(str(exc))
    return shape_treatment(resource)


@mcp.tool(name="experiment_delete_treatment")
async def delete_treatment(
    app_id: int, experiment_id: str, treatment_id: str
) -> dict[str, bool]:
    """Delete a treatment from an experiment."""
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        await service.delete_treatment(treatment_id)
    return {"deleted": True}


# ==================================================================
# Treatment localizations + screenshots
# ==================================================================


@mcp.tool(name="experiment_ensure_treatment_localization")
async def ensure_treatment_localization(
    app_id: int, experiment_id: str, treatment_id: str, locale: str,
) -> EnsureTreatmentLocalizationResponse:
    """Resolve (or create) a treatment localization, returning its id.

    ``experiment_upload_treatment_screenshot`` needs a treatment-localization
    id; this find-or-creates the localization for ``locale`` under the
    treatment. Idempotent for the same ``(treatment_id, locale)``. ``locale`` is
    a standard App Store locale (``en-US``), not a territory code.
    """
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        localization_id = await service.find_or_create_localization_id(
            treatment_id, locale,
        )
    return EnsureTreatmentLocalizationResponse(
        treatment_id=treatment_id,
        localization_id=localization_id,
        locale=locale,
    )


@mcp.tool(name="experiment_list_treatment_screenshots")
async def list_treatment_screenshots(
    app_id: int, experiment_id: str, treatment_id: str, localization_id: str,
) -> ScreenshotSetListResponse:
    """List the screenshot sets (+ assets) for a treatment localization.

    ``localization_id`` is an ``...TreatmentLocalizations`` id — obtain it with
    ``experiment_ensure_treatment_localization``. ``experiment_id`` /
    ``treatment_id`` are its parents (used to membership-check the whole chain
    against the app before reading).
    """
    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        await service.assert_localization_in_treatment(
            treatment_id, localization_id,
        )
        sets = await service.get_treatment_screenshots(localization_id)
    return ScreenshotSetListResponse(
        items=[
            ScreenshotSet(
                id=s["id"],
                display_type=s.get("display_type"),
                screenshots=[
                    Screenshot(**shot) for shot in s.get("screenshots", [])
                ],
            )
            for s in sets
        ]
    )


@mcp.tool(name="experiment_upload_treatment_screenshot")
async def upload_treatment_screenshot(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    localization_id: str,
    display_type: str,
    file_base64: str,
    file_name: str,
) -> Screenshot:
    """Upload a screenshot to a treatment localization.

    Base64-decodes ``file_base64`` and runs the 3-step reserve -> PUT -> commit
    upload against the standard ``appScreenshotSets`` / ``appScreenshots`` model
    (a set for ``display_type`` is created on demand).

    Args:
        app_id: The local app id.
        experiment_id: The parent experiment (membership-checked against the app).
        treatment_id: The parent treatment (membership-checked against the experiment).
        localization_id: An ``...TreatmentLocalizations`` id (from
            ``experiment_ensure_treatment_localization``; membership-checked
            against the treatment).
        display_type: Apple's ``screenshotDisplayType`` (e.g. ``APP_IPHONE_67``).
        file_base64: The screenshot bytes, base64-encoded.
        file_name: The file name to register with Apple.
    """
    if not is_valid_display_type(display_type):
        raise ToolError(f"Unknown display_type '{display_type}'.")
    try:
        file_bytes = decode_screenshot_payload(file_base64)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    async with _experiment_service(app_id) as (service, asc_app_id):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        await service.assert_localization_in_treatment(
            treatment_id, localization_id,
        )
        resource = await service.upload_screenshot_to_treatment(
            localization_id, display_type, file_bytes, file_name,
        )

    attrs = resource.get("attributes", {})
    return Screenshot(
        id=resource.get("id", ""),
        file_name=attrs.get("fileName") or file_name,
        display_type=display_type,
        source_url=build_source_url(attrs.get("imageAsset")),
    )
