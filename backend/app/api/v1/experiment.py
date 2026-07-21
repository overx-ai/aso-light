"""Per-app Product Page Optimization (PPO) REST endpoints.

Mounted under ``/apps`` so the auth chain runs through ``_get_verified_app``
identically to the other per-app routers. Thin wrappers over
:class:`app.services.asc.experiment.ASCExperimentService` — the same service
backing the ``experiment.*`` MCP tools — exposing the experiment / treatment /
treatment-localization + screenshot surface the React Experiments page needs.

PPO models Apple's **App Store Version Experiments** (A/B testing of
screenshots, app-preview videos, and app-icon variants). Note the API exposes
no experiment *results* (impressions, conversion, confidence) — those live only
in the ASC Analytics UI, so the frontend deep-links there instead.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.api.v1.cpp import _read_upload_payload
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.experiment import (
    SETTABLE_EXPERIMENT_STATES,
    EnsureTreatmentLocalizationResponse,
    ExperimentCreateIn,
    ExperimentListResponse,
    ExperimentResponse,
    ExperimentUpdateIn,
    ScreenshotSetListResponse,
    TreatmentCreateIn,
    TreatmentFromUploadResponse,
    TreatmentListResponse,
    TreatmentLocalizationCreateIn,
    TreatmentResponse,
    TreatmentUpdateIn,
    is_valid_display_type,
    shape_experiment,
    shape_treatment,
)
from app.schemas.screenshots import Screenshot, ScreenshotSet
from app.services.asc.errors import ASCAPIError, ChildResourceNotFoundError
from app.services.asc.experiment import ASCExperimentService, ExperimentLimitError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["experiment"])


@asynccontextmanager
async def _experiment_service(
    app_id: int,
    current_user: dict[str, Any],
    session: AsyncSession,
) -> AsyncIterator[tuple[ASCExperimentService, str]]:
    """Yield ``(service, asc_app_id)`` for a verified, owned app.

    Collapses the setup every experiment route otherwise repeats: verify the
    app belongs to the caller, open an :class:`ASCClient` for it, and build the
    service — while also keeping raw Apple/Python errors out of API responses.
    The ``asc_app_id`` is handed back alongside the service because every route
    needs it (to scope the ASC call or its IDOR membership assert).

    An ``ASCAPIError`` raised in the ``async with`` body surfaces as a
    ``502 Bad Gateway`` with Apple's message, and a child-membership violation
    (``ChildResourceNotFoundError`` from the IDOR guards) as a ``404 Not
    Found`` — never a traceback. The client is closed on exit either way.
    """
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    async with await _get_asc_client_for_app(app, session) as client:
        try:
            yield ASCExperimentService(client), app.asc_app_id
        except ChildResourceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            ) from exc
        except ASCAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ASC API error: {exc.message}",
            ) from exc


# ==================================================================
# Experiments
# ==================================================================


@router.get("/{app_id}/experiments", response_model=ExperimentListResponse)
async def list_experiments(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExperimentListResponse:
    """List the Product Page Optimization experiments for an app."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        resources = await service.list_experiments(asc_app_id)
    return ExperimentListResponse(
        items=[shape_experiment(r) for r in resources]
    )


@router.post(
    "/{app_id}/experiments",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    app_id: int,
    body: ExperimentCreateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    """Create an experiment (Apple allows one draft experiment per app)."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        resource = await service.create_experiment(
            asc_app_id,
            body.name,
            body.traffic_proportion,
            platform=body.platform,
        )
    return shape_experiment(resource)


@router.get(
    "/{app_id}/experiments/{experiment_id}",
    response_model=ExperimentResponse,
)
async def get_experiment(
    app_id: int,
    experiment_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    """Fetch a single experiment by its ASC id."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resource = await service.get_experiment(experiment_id)
    return shape_experiment(resource)


@router.patch(
    "/{app_id}/experiments/{experiment_id}",
    response_model=ExperimentResponse,
)
async def update_experiment(
    app_id: int,
    experiment_id: str,
    body: ExperimentUpdateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExperimentResponse:
    """Update an experiment's name, traffic proportion, and/or lifecycle state.

    ``state`` may only be ``WAITING_FOR_REVIEW`` (submit for review) or
    ``STOPPED`` (stop) — other states are server-assigned.
    """
    if body.state is not None and body.state not in SETTABLE_EXPERIMENT_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "state must be one of "
                f"{sorted(SETTABLE_EXPERIMENT_STATES)} (or omitted)."
            ),
        )
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        try:
            resource = await service.update_experiment(
                experiment_id,
                name=body.name,
                traffic_proportion=body.traffic_proportion,
                state=body.state,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
            ) from exc
    return shape_experiment(resource)


@router.delete(
    "/{app_id}/experiments/{experiment_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_experiment(
    app_id: int,
    experiment_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Delete an experiment (only allowed before it starts)."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.delete_experiment(experiment_id)
    return {"deleted": True}


# ==================================================================
# Treatments
# ==================================================================


@router.get(
    "/{app_id}/experiments/{experiment_id}/treatments",
    response_model=TreatmentListResponse,
)
async def list_treatments(
    app_id: int,
    experiment_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TreatmentListResponse:
    """List the treatments (variants) of an experiment."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        resources = await service.list_treatments(experiment_id)
    return TreatmentListResponse(
        items=[shape_treatment(r) for r in resources]
    )


@router.post(
    "/{app_id}/experiments/{experiment_id}/treatments",
    response_model=TreatmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_treatment(
    app_id: int,
    experiment_id: str,
    body: TreatmentCreateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TreatmentResponse:
    """Create a treatment (variant); Apple allows at most 3 per experiment."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        try:
            resource = await service.create_treatment(
                experiment_id, body.name, app_icon_name=body.app_icon_name,
            )
        except ExperimentLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
            ) from exc
    return shape_treatment(resource)


@router.patch(
    "/{app_id}/experiments/{experiment_id}/treatments/{treatment_id}",
    response_model=TreatmentResponse,
)
async def update_treatment(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    body: TreatmentUpdateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TreatmentResponse:
    """Update a treatment's name and/or alternate app-icon name."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        try:
            resource = await service.update_treatment(
                treatment_id, name=body.name, app_icon_name=body.app_icon_name,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
            ) from exc
    return shape_treatment(resource)


@router.delete(
    "/{app_id}/experiments/{experiment_id}/treatments/{treatment_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_treatment(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Delete a treatment from an experiment."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        await service.delete_treatment(treatment_id)
    return {"deleted": True}


# ==================================================================
# Treatment localizations + screenshots
# ==================================================================


@router.post(
    "/{app_id}/experiments/{experiment_id}/treatments/{treatment_id}/localizations",
    response_model=EnsureTreatmentLocalizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ensure_treatment_localization(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    body: TreatmentLocalizationCreateIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EnsureTreatmentLocalizationResponse:
    """Resolve (or create) a treatment localization for a locale (idempotent)."""
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        localization_id = await service.find_or_create_localization_id(
            treatment_id, body.locale,
        )
    return EnsureTreatmentLocalizationResponse(
        treatment_id=treatment_id,
        localization_id=localization_id,
        locale=body.locale,
    )


@router.get(
    "/{app_id}/experiments/{experiment_id}/treatments/{treatment_id}"
    "/localizations/{localization_id}/screenshots",
    response_model=ScreenshotSetListResponse,
)
async def list_treatment_screenshots(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    localization_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScreenshotSetListResponse:
    """List the screenshot sets (+ assets) for a treatment localization.

    Nested under experiment/treatment so the full parent chain can be
    membership-checked (IDOR guard) before the localization is read.
    """
    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
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
                screenshots=[Screenshot(**shot) for shot in s.get("screenshots", [])],
            )
            for s in sets
        ]
    )


@router.post(
    "/{app_id}/experiments/{experiment_id}/treatments/{treatment_id}/from-upload",
    response_model=TreatmentFromUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def populate_treatment_from_upload(
    app_id: int,
    experiment_id: str,
    treatment_id: str,
    locale: str = Form(...),
    display_type: str = Form(...),
    files: list[UploadFile] = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TreatmentFromUploadResponse:
    """Ensure a treatment localization and upload a screenshot set into it.

    Multipart/form-data: ``locale``, ``display_type``, and repeated ``files``.
    The service resolves (or creates) the localization for ``locale`` under the
    treatment, then uploads each file into the ``appScreenshotSet`` for
    ``display_type`` via the 3-step reserve -> PUT -> commit flow.
    """
    if not is_valid_display_type(display_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown display_type '{display_type}'.",
        )
    payload = await _read_upload_payload(files)

    async with _experiment_service(app_id, current_user, session) as (
        service, asc_app_id,
    ):
        await service.assert_experiment_in_app(asc_app_id, experiment_id)
        await service.assert_treatment_in_experiment(experiment_id, treatment_id)
        result = await service.populate_treatment_from_upload(
            treatment_id, locale, display_type, payload,
        )

    return TreatmentFromUploadResponse(
        treatment_id=result["treatment_id"],
        localization_id=result["localization_id"],
        locale=result["locale"],
        uploaded_count=result["uploaded_count"],
    )
