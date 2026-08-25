"""Per-app ASA endpoints.

Mounted under /apps so the auth chain runs through _get_verified_app
identically to other per-app routers (pricing, metadata, reviews, etc.).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASANegativeKeyword,
    ASAOrg,
)
from app.schemas.asa import (
    AddNegativeKeywordsRequest,
    ASAAdGroupOut,
    ASACampaignOut,
    ASAKeywordOut,
    ASAMetricRow,
    ASANegativeKeywordOut,
    ASAPerformanceReportOut,
    ASASearchTermReportOut,
    PaidOrganicJoinRow,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa.analytics import performance_rows, search_term_report_rows
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_negative_candidates,
    suggest_organic_keywords_to_track,
)

router = APIRouter()


async def _verify_campaign_for_app(
    campaign_id: int, app: Any, session: AsyncSession,
) -> ASACampaign:
    """Resolve a campaign and verify it belongs to ``app`` (404 otherwise)."""
    camp = (await session.execute(
        select(ASACampaign).where(ASACampaign.id == campaign_id)
    )).scalar_one_or_none()
    if camp is None or camp.app_adam_id != app.asc_app_id:
        raise HTTPException(404, "Campaign not found for this app")
    return camp


async def _verify_ad_group_for_app(
    ad_group_id: int, app: Any, session: AsyncSession,
) -> ASAAdGroup:
    """Resolve an ad group and verify it belongs to ``app`` (404 otherwise).

    Mirrors the campaign/ad-group ownership pattern used elsewhere in this
    router: ad group -> campaign -> app_adam_id must equal the verified app's
    ``asc_app_id``.
    """
    ag = (await session.execute(
        select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
    )).scalar_one_or_none()
    if ag is None:
        raise HTTPException(404, "Ad group not found for this app")
    camp = (await session.execute(
        select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
    )).scalar_one_or_none()
    if camp is None or camp.app_adam_id != app.asc_app_id:
        raise HTTPException(404, "Ad group not found for this app")
    return ag


async def _owned_org_credential_for_campaign(
    camp: ASACampaign, user_id: int, session: AsyncSession,
) -> tuple[ASAOrg, ASACredential]:
    """Resolve the (org, credential) behind a campaign, owned by ``user_id``.

    Mirrors the mutation auth chain (campaign -> org -> credential) shared by
    the negative-keyword add/remove routes. Raises 403 if the org's credential
    is not owned by the caller.
    """
    org = (await session.execute(
        select(ASAOrg).where(ASAOrg.id == camp.org_id)
    )).scalar_one()
    cred = (await session.execute(
        select(ASACredential).where(
            ASACredential.id == org.credential_id,
            ASACredential.user_id == user_id,
        )
    )).scalar_one_or_none()
    if cred is None:
        raise HTTPException(403, "Org not owned by user")
    return org, cred


# ---------------------------------------------------------------------------
# Listings (read from cache)
# ---------------------------------------------------------------------------


@router.get(
    "/{app_id}/asa/campaigns",
    response_model=list[ASACampaignOut],
)
async def list_campaigns_for_app(
    app_id: int,
    status_filter: str | None = Query(None, alias="status"),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASACampaignOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    stmt = select(ASACampaign).where(ASACampaign.app_adam_id == app.asc_app_id)
    if status_filter:
        stmt = stmt.where(ASACampaign.status == status_filter)
    stmt = stmt.order_by(ASACampaign.name)
    rows = (await session.execute(stmt)).scalars().all()
    return [ASACampaignOut.model_validate(r) for r in rows]


@router.get(
    "/{app_id}/asa/campaigns/{campaign_id}/ad-groups",
    response_model=list[ASAAdGroupOut],
)
async def list_ad_groups_for_campaign(
    app_id: int,
    campaign_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASAAdGroupOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    await _verify_campaign_for_app(campaign_id, app, session)
    rows = (await session.execute(
        select(ASAAdGroup).where(ASAAdGroup.campaign_id == campaign_id)
        .order_by(ASAAdGroup.name)
    )).scalars().all()
    return [ASAAdGroupOut.model_validate(r) for r in rows]


@router.get(
    "/{app_id}/asa/ad-groups/{ad_group_id}/keywords",
    response_model=list[ASAKeywordOut],
)
async def list_keywords_for_ad_group(
    app_id: int,
    ad_group_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASAKeywordOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    ag = (await session.execute(
        select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
    )).scalar_one_or_none()
    if ag is None:
        raise HTTPException(404, "Ad group not found")
    camp = (await session.execute(
        select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
    )).scalar_one()
    if camp.app_adam_id != app.asc_app_id:
        raise HTTPException(403, "Ad group does not belong to this app")
    rows = (await session.execute(
        select(ASAKeyword).where(ASAKeyword.ad_group_id == ad_group_id)
        .order_by(ASAKeyword.text)
    )).scalars().all()
    return [ASAKeywordOut.model_validate(r) for r in rows]


@router.get(
    "/{app_id}/asa/negative-keywords",
    response_model=list[ASANegativeKeywordOut],
)
async def list_negative_keywords(
    app_id: int,
    campaign_id: int | None = None,
    ad_group_id: int | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASANegativeKeywordOut]:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if (campaign_id is None) == (ad_group_id is None):
        raise HTTPException(400, "Provide exactly one of campaign_id or ad_group_id")
    if campaign_id is not None:
        await _verify_campaign_for_app(campaign_id, app, session)
        stmt = select(ASANegativeKeyword).where(
            ASANegativeKeyword.campaign_id == campaign_id,
        )
    else:
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
        )).scalar_one_or_none()
        if ag is None:
            raise HTTPException(404, "Ad group not found")
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
        )).scalar_one()
        if camp.app_adam_id != app.asc_app_id:
            raise HTTPException(403, "Ad group does not belong to this app")
        stmt = select(ASANegativeKeyword).where(
            ASANegativeKeyword.ad_group_id == ad_group_id,
        )
    rows = (await session.execute(stmt.order_by(ASANegativeKeyword.text))).scalars().all()
    return [ASANegativeKeywordOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Reports + insights (read-only, served from local cache)
# ---------------------------------------------------------------------------


@router.get(
    "/{app_id}/asa/keywords/paid-organic-join",
    response_model=list[PaidOrganicJoinRow],
)
async def paid_organic_join_route(
    app_id: int,
    days: int = Query(30, ge=1, le=90),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PaidOrganicJoinRow]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    rows = await paid_organic_join(
        session=session, app_id=app_id, user_id=user_id, days=days,
    )
    return [PaidOrganicJoinRow(**r) for r in rows]


@router.get(
    "/{app_id}/asa/search-terms",
    response_model=ASASearchTermReportOut,
)
async def search_term_report_route(
    app_id: int,
    days: int = Query(30, ge=1, le=90),
    ad_group_id: int | None = None,
    min_impressions: int | None = Query(None, ge=0),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASASearchTermReportOut:
    """Search-term performance rollup (single-currency rows) over a window."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    if ad_group_id is not None:
        await _verify_ad_group_for_app(ad_group_id, app, session)
    cutoff, rows = await search_term_report_rows(
        session=session,
        user_id=user_id,
        app_id=app_id,
        days=days,
        ad_group_id=ad_group_id,
        min_impressions=min_impressions,
    )
    return ASASearchTermReportOut(
        time_range={
            "start": cutoff.isoformat(),
            "end": date.today().isoformat(),
        },
        rows=rows,
    )


@router.get(
    "/{app_id}/asa/performance",
    response_model=ASAPerformanceReportOut,
)
async def performance_report(
    app_id: int,
    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"] = "CAMPAIGN",
    days: int = Query(30, ge=1, le=90),
    storefront: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASAPerformanceReportOut:
    """Raw daily metric rows for one app at one grain (the client rolls these up)."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff, rows = await performance_rows(
        session=session,
        user_id=user_id,
        app_adam_id=app.asc_app_id,
        grain=grain,
        days=days,
        storefront=storefront,
    )
    return ASAPerformanceReportOut(
        grain=grain,
        time_range={
            "start": cutoff.isoformat(),
            "end": date.today().isoformat(),
        },
        rows=[ASAMetricRow.model_validate(r) for r in rows],
    )


@router.get(
    "/{app_id}/asa/insights/organic-candidates",
    response_model=list[dict],
)
async def insights_organic_candidates(
    app_id: int,
    days: int = Query(30, ge=1, le=90),
    min_taps: int = Query(20, ge=1),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    return await suggest_organic_keywords_to_track(
        session=session, app_id=app_id, user_id=user_id, days=days,
        min_taps=min_taps,
    )


@router.get(
    "/{app_id}/asa/insights/negative-candidates",
    response_model=list[dict],
)
async def insights_negative_candidates(
    app_id: int,
    days: int = Query(30, ge=1, le=90),
    min_spend: float = Query(10.0, ge=0),
    max_conv_rate: float = Query(0.005, ge=0, le=1),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    return await suggest_negative_candidates(
        session=session, app_id=app_id, user_id=user_id, days=days,
        min_spend=min_spend, max_conv_rate=max_conv_rate,
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@router.post(
    "/{app_id}/asa/negative-keywords",
    response_model=list[ASANegativeKeywordOut],
)
async def add_negative_keywords(
    app_id: int,
    body: AddNegativeKeywordsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ASANegativeKeywordOut]:
    """Add negatives to a campaign or ad group; the underlying ASA call is
    POST .../negativekeywords/bulk. Persisted locally on success."""
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    if body.scope == "AD_GROUP":
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == body.scope_id)
        )).scalar_one_or_none()
        if ag is None:
            raise HTTPException(404, "Ad group not found")
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
        )).scalar_one()
    else:
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == body.scope_id)
        )).scalar_one_or_none()
        if camp is None:
            raise HTTPException(404, "Campaign not found")
        ag = None
    if camp.app_adam_id != app.asc_app_id:
        raise HTTPException(403, "Campaign does not belong to this app")

    org, cred = await _owned_org_credential_for_campaign(camp, user_id, session)

    client = await ASAClient.from_credential(cred)
    try:
        if body.scope == "AD_GROUP" and ag is not None:
            payload = await asa_campaigns.add_negative_keywords_ad_group(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                ad_group_id=ag.asa_ad_group_id,
                keywords=[k.model_dump() for k in body.keywords],
            )
        else:
            payload = await asa_campaigns.add_negative_keywords_campaign(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                keywords=[k.model_dump() for k in body.keywords],
            )
    except ASAAPIError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"ASA API rejected the bulk add: {exc.message}",
        )
    finally:
        await client.aclose()

    out: list[ASANegativeKeyword] = []
    for n in payload:
        rec = ASANegativeKeyword(
            asa_negative_keyword_id=n["id"],
            text=n.get("text", ""),
            match_type=n.get("matchType", "EXACT"),
            campaign_id=camp.id if body.scope == "CAMPAIGN" else None,
            ad_group_id=ag.id if (body.scope == "AD_GROUP" and ag is not None) else None,
        )
        session.add(rec)
        out.append(rec)
    await session.flush()
    return [ASANegativeKeywordOut.model_validate(r) for r in out]


@router.delete(
    "/{app_id}/asa/negative-keywords/{negative_keyword_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_negative_keyword(
    app_id: int,
    negative_keyword_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    n = (await session.execute(
        select(ASANegativeKeyword).where(ASANegativeKeyword.id == negative_keyword_id)
    )).scalar_one_or_none()
    if n is None:
        raise HTTPException(404, "Negative keyword not found")

    if n.campaign_id is not None:
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == n.campaign_id)
        )).scalar_one()
        ag = None
    else:
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == n.ad_group_id)
        )).scalar_one()
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == ag.campaign_id)
        )).scalar_one()
    if camp.app_adam_id != app.asc_app_id:
        raise HTTPException(403, "Negative does not belong to this app")

    org, cred = await _owned_org_credential_for_campaign(camp, user_id, session)

    client = await ASAClient.from_credential(cred)
    try:
        if ag is not None:
            await asa_campaigns.remove_negative_keyword_ad_group(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                ad_group_id=ag.asa_ad_group_id,
                negative_id=n.asa_negative_keyword_id,
            )
        else:
            await asa_campaigns.remove_negative_keyword_campaign(
                client, org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                negative_id=n.asa_negative_keyword_id,
            )
    except ASAAPIError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"ASA API rejected the delete: {exc.message}",
        )
    finally:
        await client.aclose()
    await session.delete(n)
