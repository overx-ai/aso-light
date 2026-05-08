"""Per-app ASA endpoints.

Mounted under /apps so the auth chain runs through _get_verified_app
identically to other per-app routers (pricing, metadata, reviews, etc.).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASAMetricDaily,
    ASANegativeKeyword,
    ASAOrg,
    ASASearchTerm,
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
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_negative_candidates,
    suggest_organic_keywords_to_track,
)

router = APIRouter()


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
    camp = (await session.execute(
        select(ASACampaign).where(ASACampaign.id == campaign_id)
    )).scalar_one_or_none()
    if camp is None or camp.app_adam_id != app.asc_app_id:
        raise HTTPException(404, "Campaign not found for this app")
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
        camp = (await session.execute(
            select(ASACampaign).where(ASACampaign.id == campaign_id)
        )).scalar_one_or_none()
        if camp is None or camp.app_adam_id != app.asc_app_id:
            raise HTTPException(404, "Campaign not found for this app")
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
    rows = await paid_organic_join(session=session, app_id=app_id, days=days)
    return [PaidOrganicJoinRow(**r) for r in rows]


@router.get(
    "/{app_id}/asa/search-terms",
    response_model=ASASearchTermReportOut,
)
async def search_term_report_route(
    app_id: int,
    days: int = Query(30, ge=1, le=90),
    ad_group_id: int | None = None,
    min_impressions: int | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ASASearchTermReportOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(
            ASASearchTerm.id,
            ASASearchTerm.text,
            ASASearchTerm.match_type,
            ASASearchTerm.ad_group_id,
            func.sum(ASAMetricDaily.impressions).label("imp"),
            func.sum(ASAMetricDaily.taps).label("taps"),
            func.sum(ASAMetricDaily.installs).label("ins"),
            func.sum(ASAMetricDaily.spend_amount).label("spend"),
            func.max(ASAMetricDaily.spend_currency).label("currency"),
        )
        .join(
            ASAMetricDaily,
            (ASAMetricDaily.dim_kind == "SEARCH_TERM")
            & (ASAMetricDaily.dim_id == ASASearchTerm.id)
            & (ASAMetricDaily.date >= cutoff)
            & (ASAMetricDaily.app_adam_id == app.asc_app_id),
        )
        .group_by(
            ASASearchTerm.id, ASASearchTerm.text,
            ASASearchTerm.match_type, ASASearchTerm.ad_group_id,
        )
    )
    if ad_group_id is not None:
        stmt = stmt.where(ASASearchTerm.ad_group_id == ad_group_id)
    if min_impressions is not None:
        stmt = stmt.having(func.sum(ASAMetricDaily.impressions) >= min_impressions)
    rows = (await session.execute(stmt)).all()
    return ASASearchTermReportOut(
        time_range={
            "start": cutoff.isoformat(),
            "end": date.today().isoformat(),
        },
        rows=[
            {
                "search_term_id": r.id,
                "text": r.text,
                "match_type": r.match_type,
                "ad_group_id": r.ad_group_id,
                "impressions": int(r.imp or 0),
                "taps": int(r.taps or 0),
                "installs": int(r.ins or 0),
                "spend": float(r.spend or 0),
                "spend_currency": r.currency,
            }
            for r in rows
        ],
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
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(ASAMetricDaily)
        .where(
            ASAMetricDaily.app_adam_id == app.asc_app_id,
            ASAMetricDaily.dim_kind == grain,
            ASAMetricDaily.date >= cutoff,
        )
        .order_by(ASAMetricDaily.date.desc())
    )
    if storefront:
        stmt = stmt.where(ASAMetricDaily.storefront == storefront)
    rows = (await session.execute(stmt)).scalars().all()
    return ASAPerformanceReportOut(
        grain=grain,
        time_range={
            "start": cutoff.isoformat(),
            "end": date.today().isoformat(),
        },
        rows=[
            ASAMetricRow(
                dim_kind=r.dim_kind, dim_id=r.dim_id,
                app_adam_id=r.app_adam_id, date=r.date,
                storefront=r.storefront,
                impressions=r.impressions, taps=r.taps,
                installs=r.installs, new_downloads=r.new_downloads,
                redownloads=r.redownloads,
                spend_amount=r.spend_amount,
                spend_currency=r.spend_currency,
                avg_cpa_amount=r.avg_cpa_amount,
                avg_cpt_amount=r.avg_cpt_amount,
                ttr=r.ttr,
                conversion_rate=r.conversion_rate,
            )
            for r in rows
        ],
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
        session=session, app_id=app_id, days=days, min_taps=min_taps,
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
        session=session, app_id=app_id, days=days,
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
