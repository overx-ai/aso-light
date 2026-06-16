"""MCP tools for the Apple Search Ads vertical.

Mirrors the REST surface in ``app.api.v1.asa[_app]`` but as 15 LLM-facing
MCP tools. Auth chain runs through :func:`resolve_app` for app-scoped
tools and a local :func:`_own_credential_for_user` for credential-scoped
tools. :func:`resolve_app` already converts the REST helpers'
:class:`HTTPException`s into :class:`ToolError`s; every :class:`ASAAPIError`
from network calls is likewise wrapped to a :class:`ToolError` carrying only
the human-readable message.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.context import (
    get_user_id,
    resolve_app,
    session_scope,
)
from app.mcp.server import mcp
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASANegativeKeyword,
    ASAOrg,
)
from app.schemas.asa import (
    ASAAdGroupOut,
    ASACampaignOut,
    ASACredentialOut,
    ASAKeywordOut,
    ASAMetricRow,
    ASANegativeKeywordOut,
    ASAOrgOut,
    ASAPerformanceReportOut,
    ASASearchTermReportOut,
    ASASyncOperationOut,
    ASATestResult,
    NegativeKeywordIn,
    PaidOrganicJoinRow,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa import cpp_ads as asa_cpp_ads
from app.services.asa.analytics import performance_rows, search_term_report_rows
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.asa.joins import (
    paid_organic_join,
    suggest_negative_candidates,
    suggest_organic_keywords_to_track,
)
from app.services.asa.sync import run_sync


# ---------------------------------------------------------------------------
# Local validation + auth helpers
# ---------------------------------------------------------------------------


def _validate_days(days: int) -> None:
    """Reject out-of-range report windows (mirrors the REST ``Query`` bounds)."""
    if days < 1 or days > 90:
        raise ToolError("days must be between 1 and 90")


async def _own_credential_for_user(
    credential_id: int, session: AsyncSession,
) -> ASACredential:
    """Resolve an :class:`ASACredential` and verify the current user owns it."""
    user_id = get_user_id()
    cred = (await session.execute(
        select(ASACredential).where(
            ASACredential.id == credential_id,
            ASACredential.user_id == user_id,
        )
    )).scalar_one_or_none()
    if cred is None:
        raise ToolError("ASA credential not found or not owned by user")
    return cred


async def _campaign_owned_by_user(
    campaign_id: int, session: AsyncSession,
) -> tuple[ASACampaign, ASAOrg, ASACredential]:
    """Resolve a campaign and verify the auth chain.

    Returns the (campaign, org, credential) triple. Raises :class:`ToolError`
    if the campaign does not exist or is not reachable by the current user.
    """
    camp = (await session.execute(
        select(ASACampaign).where(ASACampaign.id == campaign_id)
    )).scalar_one_or_none()
    if camp is None:
        raise ToolError("Campaign not found")
    org = (await session.execute(
        select(ASAOrg).where(ASAOrg.id == camp.org_id)
    )).scalar_one()
    cred = await _own_credential_for_user(org.credential_id, session)
    return camp, org, cred


async def _ad_group_owned_by_user(
    ad_group_id: int, session: AsyncSession,
) -> tuple[ASAAdGroup, ASACampaign, ASAOrg, ASACredential]:
    """Resolve an ad group and verify the full auth chain.

    Returns the (ad_group, campaign, org, credential) tuple. Raises
    :class:`ToolError` if the ad group does not exist or is not reachable by
    the current user.
    """
    ag = (await session.execute(
        select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
    )).scalar_one_or_none()
    if ag is None:
        raise ToolError("Ad group not found")
    camp, org, cred = await _campaign_owned_by_user(ag.campaign_id, session)
    return ag, camp, org, cred


# ---------------------------------------------------------------------------
# Credentials & connectivity
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.list_credentials")
async def list_credentials() -> list[ASACredentialOut]:
    """List the current user's stored ASA credentials (no secrets)."""
    async with session_scope() as session:
        user_id = get_user_id()
        rows = (await session.execute(
            select(ASACredential)
            .where(ASACredential.user_id == user_id)
            .order_by(ASACredential.created_at.desc())
        )).scalars().all()
        return [ASACredentialOut.model_validate(r) for r in rows]


@mcp.tool(name="asa.test_credential")
async def test_credential(credential_id: int) -> ASATestResult:
    """Hit Apple's ``/me/acl`` with the credential and report status."""
    async with session_scope() as session:
        cred = await _own_credential_for_user(credential_id, session)
        try:
            client = await ASAClient.from_credential(cred)
            try:
                payload = await client.request("GET", "/me/acl")
            finally:
                await client.aclose()
        except ASAAPIError as exc:
            return ASATestResult(ok=False, orgs_visible=0, detail=exc.message)
        return ASATestResult(
            ok=True,
            orgs_visible=len(payload.get("data") or []),
        )


@mcp.tool(name="asa.delete_credential")
async def delete_credential(credential_id: int) -> dict:
    """Revoke an ASA credential and delete it from the project."""
    async with session_scope() as session:
        cred = await _own_credential_for_user(credential_id, session)
        await session.delete(cred)
        return {"deleted": True, "id": credential_id}


@mcp.tool(name="asa.list_orgs")
async def list_orgs(credential_id: int) -> list[ASAOrgOut]:
    """List ASA orgs visible to a credential (cached from the last sync)."""
    async with session_scope() as session:
        await _own_credential_for_user(credential_id, session)
        rows = (await session.execute(
            select(ASAOrg)
            .where(ASAOrg.credential_id == credential_id)
            .order_by(ASAOrg.name)
        )).scalars().all()
        return [ASAOrgOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.list_campaigns")
async def list_campaigns(
    app_id: int | None = None,
    org_id: int | None = None,
    status: str | None = None,
) -> list[ASACampaignOut]:
    """List campaigns, optionally filtered to a local app, an org, or a status.

    When ``app_id`` is provided, the auth chain runs through
    :func:`resolve_app` and only campaigns matching ``app.asc_app_id`` are
    returned. When ``org_id`` is used without ``app_id``, the user must own
    the org's credential. Campaigns whose org is owned by another user are
    filtered out post-query as a defense-in-depth check.
    """
    async with session_scope() as session:
        user_id = get_user_id()
        stmt = select(ASACampaign)
        if app_id is not None:
            app = await resolve_app(app_id, session)
            stmt = stmt.where(ASACampaign.app_adam_id == app.asc_app_id)
        if org_id is not None:
            stmt = stmt.where(ASACampaign.org_id == org_id)
        if status is not None:
            stmt = stmt.where(ASACampaign.status == status)
        stmt = stmt.order_by(ASACampaign.name)
        rows = (await session.execute(stmt)).scalars().all()

        owned_creds = {
            c.id for c in (await session.execute(
                select(ASACredential).where(ASACredential.user_id == user_id)
            )).scalars().all()
        }
        owned_orgs = {
            o.id for o in (await session.execute(
                select(ASAOrg).where(ASAOrg.credential_id.in_(owned_creds))
            )).scalars().all()
        } if owned_creds else set()
        return [
            ASACampaignOut.model_validate(c)
            for c in rows
            if c.org_id in owned_orgs
        ]


@mcp.tool(name="asa.get_campaign")
async def get_campaign(campaign_id: int) -> ASACampaignOut:
    """Get a single campaign; auth chain enforced."""
    async with session_scope() as session:
        camp, _org, _cred = await _campaign_owned_by_user(campaign_id, session)
        return ASACampaignOut.model_validate(camp)


@mcp.tool(name="asa.list_ad_groups")
async def list_ad_groups(campaign_id: int) -> list[ASAAdGroupOut]:
    """List ad groups under a campaign."""
    async with session_scope() as session:
        await _campaign_owned_by_user(campaign_id, session)
        rows = (await session.execute(
            select(ASAAdGroup)
            .where(ASAAdGroup.campaign_id == campaign_id)
            .order_by(ASAAdGroup.name)
        )).scalars().all()
        return [ASAAdGroupOut.model_validate(r) for r in rows]


@mcp.tool(name="asa.list_keywords")
async def list_keywords(ad_group_id: int) -> list[ASAKeywordOut]:
    """List targeted keywords inside an ad group."""
    async with session_scope() as session:
        ag = (await session.execute(
            select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
        )).scalar_one_or_none()
        if ag is None:
            raise ToolError("Ad group not found")
        await _campaign_owned_by_user(ag.campaign_id, session)
        rows = (await session.execute(
            select(ASAKeyword)
            .where(ASAKeyword.ad_group_id == ad_group_id)
            .order_by(ASAKeyword.text)
        )).scalars().all()
        return [ASAKeywordOut.model_validate(r) for r in rows]


@mcp.tool(name="asa.list_negative_keywords")
async def list_negative_keywords(
    campaign_id: int | None = None,
    ad_group_id: int | None = None,
) -> list[ASANegativeKeywordOut]:
    """List negative keywords. Provide exactly one of ``campaign_id`` or ``ad_group_id``."""
    if (campaign_id is None) == (ad_group_id is None):
        raise ToolError("Provide exactly one of campaign_id or ad_group_id")
    async with session_scope() as session:
        if campaign_id is not None:
            await _campaign_owned_by_user(campaign_id, session)
            stmt = select(ASANegativeKeyword).where(
                ASANegativeKeyword.campaign_id == campaign_id,
            )
        else:
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == ad_group_id)
            )).scalar_one_or_none()
            if ag is None:
                raise ToolError("Ad group not found")
            await _campaign_owned_by_user(ag.campaign_id, session)
            stmt = select(ASANegativeKeyword).where(
                ASANegativeKeyword.ad_group_id == ad_group_id,
            )
        rows = (await session.execute(
            stmt.order_by(ASANegativeKeyword.text)
        )).scalars().all()
        return [ASANegativeKeywordOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.performance_report")
async def performance_report(
    app_id: int,
    grain: Literal["CAMPAIGN", "AD_GROUP", "KEYWORD"] = "CAMPAIGN",
    days: int = 30,
    storefront: str | None = None,
) -> ASAPerformanceReportOut:
    """Raw daily performance rows over a window at one grain (the client rolls these up)."""
    _validate_days(days)
    async with session_scope() as session:
        user_id = get_user_id()
        app = await resolve_app(app_id, session)
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


@mcp.tool(name="asa.search_term_report")
async def search_term_report(
    app_id: int,
    days: int = 30,
    ad_group_id: int | None = None,
    min_impressions: int | None = None,
) -> ASASearchTermReportOut:
    """Search-term performance — what users actually typed, joined with metrics.

    Use this to discover terms to track organically
    (:tool:`asa.suggest_organic_keywords_to_track`) or to add as negatives
    (:tool:`asa.suggest_negative_candidates`).
    """
    _validate_days(days)
    async with session_scope() as session:
        user_id = get_user_id()
        await resolve_app(app_id, session)
        cutoff, rows = await search_term_report_rows(
            session=session,
            user_id=user_id,
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


@mcp.tool(name="asa.paid_organic_join")
async def paid_organic_join_tool(
    app_id: int, days: int = 30,
) -> list[PaidOrganicJoinRow]:
    """Per tracked-organic keyword, attach the 30-day ASA paid metrics.

    Tracked terms with no matching ASA keyword return zeros in the
    ``paid_*_30d`` columns. Useful for diagnosing paid+organic coverage gaps.
    """
    _validate_days(days)
    async with session_scope() as session:
        user_id = get_user_id()
        await resolve_app(app_id, session)
        rows = await paid_organic_join(
            session=session, app_id=app_id, user_id=user_id, days=days,
        )
        return [PaidOrganicJoinRow(**r) for r in rows]


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.suggest_organic_keywords_to_track")
async def suggest_organic(
    app_id: int, days: int = 30, min_taps: int = 20,
) -> list[dict[str, Any]]:
    """Search terms above ``min_taps`` that aren't in tracked organic keywords.

    Surfaces ASA-driven discovery: terms users actually typed and tapped on
    in paid that you should consider tracking as organic.
    """
    _validate_days(days)
    if min_taps < 1:
        raise ToolError("min_taps must be >= 1")
    async with session_scope() as session:
        user_id = get_user_id()
        await resolve_app(app_id, session)
        return await suggest_organic_keywords_to_track(
            session=session, app_id=app_id, user_id=user_id, days=days,
            min_taps=min_taps,
        )


@mcp.tool(name="asa.suggest_negative_candidates")
async def suggest_negatives(
    app_id: int,
    days: int = 30,
    min_spend: float = 10.0,
    max_conv_rate: float = 0.005,
) -> list[dict[str, Any]]:
    """Search terms with high spend and low conversion — negative-keyword candidates."""
    _validate_days(days)
    if min_spend < 0:
        raise ToolError("min_spend must be >= 0")
    if not 0 <= max_conv_rate <= 1:
        raise ToolError("max_conv_rate must be between 0 and 1")
    async with session_scope() as session:
        user_id = get_user_id()
        await resolve_app(app_id, session)
        return await suggest_negative_candidates(
            session=session,
            app_id=app_id,
            user_id=user_id,
            days=days,
            min_spend=min_spend,
            max_conv_rate=max_conv_rate,
        )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.add_negative_keywords")
async def add_negative_keywords(
    scope: Literal["CAMPAIGN", "AD_GROUP"],
    scope_id: int,
    keywords: list[NegativeKeywordIn],
) -> list[ASANegativeKeywordOut]:
    """Bulk-add negatives at the given scope. Hits ASA first, then mirrors locally.

    ``keywords``: list of ``{text, match_type}`` where
    ``match_type ∈ {BROAD, EXACT}``.
    """
    if not keywords:
        raise ToolError("keywords must be non-empty")
    if len(keywords) > 200:
        raise ToolError("at most 200 keywords per call")

    async with session_scope() as session:
        if scope == "AD_GROUP":
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == scope_id)
            )).scalar_one_or_none()
            if ag is None:
                raise ToolError("Ad group not found")
            camp, org, cred = await _campaign_owned_by_user(
                ag.campaign_id, session,
            )
        else:
            camp, org, cred = await _campaign_owned_by_user(scope_id, session)
            ag = None

        client = await ASAClient.from_credential(cred)
        try:
            if scope == "AD_GROUP" and ag is not None:
                payload = await asa_campaigns.add_negative_keywords_ad_group(
                    client,
                    org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    ad_group_id=ag.asa_ad_group_id,
                    keywords=[k.model_dump() for k in keywords],
                )
            else:
                payload = await asa_campaigns.add_negative_keywords_campaign(
                    client,
                    org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    keywords=[k.model_dump() for k in keywords],
                )
        except ASAAPIError as exc:
            raise ToolError(f"ASA rejected the bulk add: {exc.message}") from exc
        finally:
            await client.aclose()

        out: list[ASANegativeKeyword] = []
        for n in payload:
            rec = ASANegativeKeyword(
                asa_negative_keyword_id=n["id"],
                text=n.get("text", ""),
                match_type=n.get("matchType", "EXACT"),
                campaign_id=camp.id if scope == "CAMPAIGN" else None,
                ad_group_id=(
                    ag.id if (scope == "AD_GROUP" and ag is not None) else None
                ),
            )
            session.add(rec)
            out.append(rec)
        await session.flush()
        return [ASANegativeKeywordOut.model_validate(r) for r in out]


@mcp.tool(name="asa.remove_negative_keyword")
async def remove_negative_keyword(negative_keyword_id: int) -> dict:
    """Delete a single negative keyword from ASA and our local cache."""
    async with session_scope() as session:
        n = (await session.execute(
            select(ASANegativeKeyword).where(
                ASANegativeKeyword.id == negative_keyword_id,
            )
        )).scalar_one_or_none()
        if n is None:
            raise ToolError("Negative keyword not found")

        if n.campaign_id is not None:
            camp, org, cred = await _campaign_owned_by_user(
                n.campaign_id, session,
            )
            ag = None
        else:
            ag = (await session.execute(
                select(ASAAdGroup).where(ASAAdGroup.id == n.ad_group_id)
            )).scalar_one()
            camp, org, cred = await _campaign_owned_by_user(
                ag.campaign_id, session,
            )

        client = await ASAClient.from_credential(cred)
        try:
            if ag is not None:
                await asa_campaigns.remove_negative_keyword_ad_group(
                    client,
                    org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    ad_group_id=ag.asa_ad_group_id,
                    negative_id=n.asa_negative_keyword_id,
                )
            else:
                await asa_campaigns.remove_negative_keyword_campaign(
                    client,
                    org_id=org.asa_org_id,
                    campaign_id=camp.asa_campaign_id,
                    negative_id=n.asa_negative_keyword_id,
                )
        except ASAAPIError as exc:
            raise ToolError(f"ASA rejected the delete: {exc.message}") from exc
        finally:
            await client.aclose()
        await session.delete(n)
        return {"deleted": True, "id": negative_keyword_id}


# ---------------------------------------------------------------------------
# Custom Product Page ads (CPP ↔ ad group wiring)
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.list_cpp_ads")
async def list_cpp_ads(ad_group_id: int) -> list[dict[str, Any]]:
    """List the Ads in an ad group (each carries its Custom Product Page link).

    Inspect the returned Ads' creative reference to see which Custom Product
    Page (if any) the ad group currently serves. Returns the raw ASA ``Ad``
    resources — there is no local Ad model.
    """
    async with session_scope() as session:
        ag, camp, org, cred = await _ad_group_owned_by_user(
            ad_group_id, session,
        )
        client = await ASAClient.from_credential(cred)
        try:
            return await asa_cpp_ads.list_ads(
                client,
                org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                adgroup_id=ag.asa_ad_group_id,
            )
        except ASAAPIError as exc:
            raise ToolError(f"ASA rejected listing ads: {exc.message}") from exc
        finally:
            await client.aclose()


@mcp.tool(name="asa.assign_cpp")
async def assign_cpp(
    ad_group_id: int, cpp_id: str, name: str,
) -> dict[str, Any]:
    """Attach a Custom Product Page to an ad group by creating an Ad.

    Creates an ASA Ad in the ad group whose creative references the ASC
    ``appCustomProductPage`` id, so the ad group serves that tailored page.

    Args:
        ad_group_id: The local ASA ad group id.
        cpp_id: The ASC ``appCustomProductPage`` id to serve.
        name: A human-readable name for the new Ad.

    Returns the created ASA ``Ad`` resource.
    """
    if not cpp_id.strip():
        raise ToolError("cpp_id must be non-empty")
    if not name.strip():
        raise ToolError("name must be non-empty")
    async with session_scope() as session:
        ag, camp, org, cred = await _ad_group_owned_by_user(
            ad_group_id, session,
        )
        client = await ASAClient.from_credential(cred)
        try:
            return await asa_cpp_ads.assign_cpp(
                client,
                org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                adgroup_id=ag.asa_ad_group_id,
                cpp_id=cpp_id,
                name=name,
            )
        except ASAAPIError as exc:
            raise ToolError(f"ASA rejected the CPP assign: {exc.message}") from exc
        finally:
            await client.aclose()


@mcp.tool(name="asa.unassign_cpp")
async def unassign_cpp(ad_group_id: int, ad_id: int) -> dict:
    """Detach a Custom Product Page from an ad group by deleting its Ad.

    Args:
        ad_group_id: The local ASA ad group id.
        ad_id: The ASA (Apple-side) Ad id to delete (from
            :tool:`asa.list_cpp_ads`).

    The ad group falls back to the default product page (or a remaining Ad).
    """
    async with session_scope() as session:
        ag, camp, org, cred = await _ad_group_owned_by_user(
            ad_group_id, session,
        )
        client = await ASAClient.from_credential(cred)
        try:
            await asa_cpp_ads.unassign_cpp(
                client,
                org_id=org.asa_org_id,
                campaign_id=camp.asa_campaign_id,
                adgroup_id=ag.asa_ad_group_id,
                ad_id=ad_id,
            )
        except ASAAPIError as exc:
            raise ToolError(f"ASA rejected the CPP unassign: {exc.message}") from exc
        finally:
            await client.aclose()
        return {"deleted": True, "ad_id": ad_id}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@mcp.tool(name="asa.sync")
async def sync_tool(
    credential_id: int, full: bool = False,
) -> ASASyncOperationOut:
    """Run an ASA sync (entities + reports). ``full=True`` triggers a 90-day backfill."""
    async with session_scope() as session:
        cred = await _own_credential_for_user(credential_id, session)
        op = await run_sync(
            session=session,
            credential_id=cred.id,
            user_id=cred.user_id,
            full_backfill=full,
        )
        return ASASyncOperationOut(
            id=op.id,
            credential_id=op.credential_id,
            status=op.status,
            full_backfill=op.full_backfill,
            steps=op.steps or [],
            error_log=op.error_log or [],
            started_at=op.started_at,
            completed_at=op.completed_at,
        )
