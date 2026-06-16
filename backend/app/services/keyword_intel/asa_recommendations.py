"""Path A: harvest Apple-suggested keywords + their popularity from ASA.

Endpoint:
    GET /api/v5/campaigns/{campaign_id}/adgroups/{ad_group_id}/recommendations/keywords

This surface returns Apple's own suggestions for an ad group, each with a
``popularity`` score. It does **not** answer "is *meditation timer* popular?"
— it answers "what does Apple think we should target?" Useful for discovery,
not for arbitrary lookup. Pair with paid providers later for the latter.

Caveats:
* As of Oct 2025 a large share of US popularity values pin to the floor (5).
  Treat scores as directional only.
* The score's native scale varies by API version. We accept either the dot
  scale (1–5) or the integer scale (5–100) and normalize to 0–100.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import ASAAdGroup, ASACampaign, ASACredential, ASAOrg
from app.models.credential import ASCCredential
from app.services.asa.client import ASAClient
from app.services.asa.errors import ASAAPIError
from app.services.keyword_intel.base import KeywordIntel, KeywordIntelProvider

logger = logging.getLogger(__name__)

SOURCE_NAME = "asa_recommendations"


def _normalize_popularity(raw: int | float | None) -> int | None:
    """Map provider native scale to 0..100.

    Two known formats:
    * 1–5 dot scale → multiply by 20.
    * 5–100 integer scale → use directly.

    Anything outside [1, 100] is treated as missing.
    """
    if raw is None:
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v > 100:
        return None
    # 5 is ambiguous (top of the 1-5 dot scale vs floor of the 5-100 integer
    # scale). Per the module docstring, the floor-pinned integer value (5)
    # dominates in practice, so bias toward the integer interpretation:
    # treat <5 as the dot scale, and 5+ as already-integer.
    if v < 5:
        return v * 20
    return v


async def _select_ad_groups_for_app(
    session: AsyncSession, app_id: int, max_groups: int,
) -> list[tuple[int, int, list[str]]]:
    """Return up to ``max_groups`` ``(asa_campaign_id, asa_ad_group_id, storefronts)``
    triples for the given app, preferring ENABLED groups in ENABLED campaigns.
    """
    stmt = (
        select(
            ASACampaign.asa_campaign_id,
            ASAAdGroup.asa_ad_group_id,
            ASACampaign.storefronts,
            ASACampaign.status,
            ASAAdGroup.status,
        )
        .join(ASAAdGroup, ASAAdGroup.campaign_id == ASACampaign.id)
        .where(
            ASACampaign.app_id == app_id,
            ASACampaign.archived_at.is_(None),
            ASAAdGroup.archived_at.is_(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    # Sort: ENABLED + ENABLED first, then any status, then archived already filtered.
    rows.sort(
        key=lambda r: (
            0 if (r[3] or "").upper() == "ENABLED" else 1,
            0 if (r[4] or "").upper() == "ENABLED" else 1,
        )
    )
    return [(r[0], r[1], list(r[2] or [])) for r in rows[:max_groups]]


async def _resolve_org_id(
    session: AsyncSession, asa_credential_id: int,
) -> int | None:
    """Pick a usable orgId from ``asa_orgs``. Apple ACL returns one or more;
    we just need any owned one to seed the X-AP-Context header."""
    stmt = (
        select(ASAOrg.asa_org_id)
        .where(ASAOrg.credential_id == asa_credential_id)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return int(row[0]) if row else None


class ASARecommendationsProvider(KeywordIntelProvider):
    """Calls the ASA recommendations endpoint for each of the app's ad groups."""

    name = SOURCE_NAME

    async def fetch(
        self,
        *,
        app_id: int,
        session: AsyncSession,
        max_ad_groups: int = 5,
        **_: Any,
    ) -> list[KeywordIntel]:
        # Find a usable ASA credential for the app's owner.
        app = (
            await session.execute(select(App).where(App.id == app_id))
        ).scalar_one_or_none()
        if app is None:
            return []

        # Owner of the app is the user behind the ASC credential. ASA creds
        # are per-user, so find any ASA cred belonging to the same user.
        owner_id = (
            await session.execute(
                select(ASCCredential.user_id).where(
                    ASCCredential.id == app.credential_id,
                )
            )
        ).scalar_one_or_none()
        if owner_id is None:
            return []

        asa_cred = (
            await session.execute(
                select(ASACredential).where(ASACredential.user_id == owner_id)
            )
        ).scalar_one_or_none()
        if asa_cred is None:
            return []

        org_id = await _resolve_org_id(session, asa_cred.id)
        if org_id is None:
            return []

        ad_groups = await _select_ad_groups_for_app(session, app_id, max_ad_groups)
        if not ad_groups:
            return []

        out: list[KeywordIntel] = []
        async with await ASAClient.from_credential(asa_cred) as client:
            for asa_campaign_id, asa_ad_group_id, storefronts in ad_groups:
                try:
                    payload = await client.request(
                        "GET",
                        (
                            f"/campaigns/{asa_campaign_id}/adgroups/"
                            f"{asa_ad_group_id}/recommendations/keywords"
                        ),
                        org_id=org_id,
                    )
                except ASAAPIError as exc:
                    # Auth / permission errors are caller-actionable — let them
                    # bubble so the refresh endpoint can surface a real status.
                    # 4xx-other / 5xx are per-ad-group transient (closed group,
                    # not yet eligible for recs, etc.); log + continue.
                    if exc.status in (401, 403):
                        raise
                    logger.warning(
                        "ASA recommendations failed for app=%s ad_group=%s: %s",
                        app_id, asa_ad_group_id, exc,
                    )
                    continue
                except httpx.HTTPError as exc:
                    logger.warning(
                        "ASA recommendations transport error app=%s ad_group=%s: %s",
                        app_id, asa_ad_group_id, exc,
                    )
                    continue

                for entry in (payload.get("data") or []):
                    keyword = entry.get("keyword") or entry.get("text")
                    if not keyword:
                        continue
                    raw_popularity = entry.get("popularity")
                    # Fall back to searchScore for normalization when popularity
                    # is absent, but keep raw_popularity itself for raw_score.
                    score_input = (
                        raw_popularity
                        if raw_popularity is not None
                        else entry.get("searchScore")
                    )
                    popularity = _normalize_popularity(score_input)
                    bid = entry.get("bidAmount")
                    if not isinstance(bid, dict):
                        bid = {}
                    storefront = (
                        entry.get("countryOrRegion")
                        or (storefronts[0] if storefronts else None)
                    )
                    if not storefront:
                        continue
                    out.append(
                        KeywordIntel(
                            keyword=str(keyword).strip(),
                            locale=str(storefront),
                            source=SOURCE_NAME,
                            volume_score=popularity,
                            difficulty_score=None,  # not provided by this endpoint
                            raw_score=raw_popularity
                            if isinstance(raw_popularity, int)
                            else None,
                            extra={
                                "asa_campaign_id": asa_campaign_id,
                                "asa_ad_group_id": asa_ad_group_id,
                                "match_type": entry.get("matchType"),
                                "is_recommended_for_bid": entry.get(
                                    "isRecommendedForBid"
                                ),
                                "bid_amount": bid.get("amount"),
                                "bid_currency": bid.get("currency"),
                            },
                        )
                    )
        return out


__all__ = ["ASARecommendationsProvider", "SOURCE_NAME"]
