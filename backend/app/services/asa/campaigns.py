"""Apple Search Ads Campaign Management API helpers — read + negatives.

Each function takes an ASAClient instance and returns the parsed `data`
array from Apple. No DB access here; the sync orchestrator owns
persistence.
"""
from __future__ import annotations

from typing import Any

from app.services.asa.client import ASAClient


async def list_orgs_for_credential(client: ASAClient) -> list[dict[str, Any]]:
    """GET /me/acl — orgs visible to this credential."""
    payload = await client.request("GET", "/me/acl")
    return payload.get("data") or []


async def list_campaigns(client: ASAClient, *, org_id: int) -> list[dict[str, Any]]:
    return await client.get_all_pages("POST", "/campaigns/find", org_id=org_id)


async def list_ad_groups(
    client: ASAClient, *, org_id: int, campaign_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST", f"/campaigns/{campaign_id}/adgroups/find", org_id=org_id,
    )


async def list_targeting_keywords(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/targetingkeywords/find",
        org_id=org_id,
    )


async def list_negative_keywords_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST", f"/campaigns/{campaign_id}/negativekeywords/find", org_id=org_id,
    )


async def list_negative_keywords_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
) -> list[dict[str, Any]]:
    return await client.get_all_pages(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/find",
        org_id=org_id,
    )


async def add_negative_keywords_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int,
    keywords: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """keywords: [{"text": "...", "match_type": "BROAD|EXACT"}, ...]"""
    payload = await client.request(
        "POST", f"/campaigns/{campaign_id}/negativekeywords/bulk",
        org_id=org_id,
        json=[
            {"text": k["text"], "matchType": k["match_type"]}
            for k in keywords
        ],
    )
    return payload.get("data") or []


async def add_negative_keywords_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    keywords: list[dict[str, str]],
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/bulk",
        org_id=org_id,
        json=[
            {"text": k["text"], "matchType": k["match_type"]}
            for k in keywords
        ],
    )
    return payload.get("data") or []


async def remove_negative_keyword_campaign(
    client: ASAClient, *, org_id: int, campaign_id: int, negative_id: int,
) -> None:
    await client.request(
        "DELETE",
        f"/campaigns/{campaign_id}/negativekeywords/{negative_id}",
        org_id=org_id,
    )


async def remove_negative_keyword_ad_group(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    negative_id: int,
) -> None:
    await client.request(
        "DELETE",
        f"/campaigns/{campaign_id}/adgroups/{ad_group_id}/negativekeywords/{negative_id}",
        org_id=org_id,
    )
