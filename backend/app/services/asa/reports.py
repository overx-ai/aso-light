"""Apple Search Ads reporting API helpers.

ASA reports are POST endpoints with a `selector` body. Returns rows
of {metadata: {...}, granularity: [{date, impressions, taps, ...}]}.
We pass through; the sync orchestrator parses.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any, Literal

from app.services.asa.client import ASAClient

Granularity = Literal["DAILY"]


def _selector(start: _date, end: _date, granularity: Granularity = "DAILY") -> dict[str, Any]:
    return {
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
        "selector": {
            "orderBy": [{"field": "spend", "sortOrder": "DESCENDING"}],
            "pagination": {"offset": 0, "limit": 1000},
        },
        "granularity": granularity,
        "timeZone": "UTC",
        "returnRowTotals": False,
        "returnGrandTotals": False,
    }


def _rows(payload: Any) -> list[dict[str, Any]]:
    return ((payload or {}).get("data") or {}).get(
        "reportingDataResponse", {}
    ).get("row") or []


async def campaign_report(
    client: ASAClient, *, org_id: int, start: _date, end: _date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST", "/reports/campaigns",
        org_id=org_id, json=_selector(start, end),
    )
    return _rows(payload)


async def ad_group_report(
    client: ASAClient, *, org_id: int, campaign_id: int,
    start: _date, end: _date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST", f"/reports/campaigns/{campaign_id}/adgroups",
        org_id=org_id, json=_selector(start, end),
    )
    return _rows(payload)


async def keyword_report(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    start: _date, end: _date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/reports/campaigns/{campaign_id}/adgroups/{ad_group_id}/keywords",
        org_id=org_id, json=_selector(start, end),
    )
    return _rows(payload)


async def search_term_report(
    client: ASAClient, *, org_id: int, campaign_id: int, ad_group_id: int,
    start: _date, end: _date,
) -> list[dict[str, Any]]:
    payload = await client.request(
        "POST",
        f"/reports/campaigns/{campaign_id}/adgroups/{ad_group_id}/searchterms",
        org_id=org_id, json=_selector(start, end),
    )
    return _rows(payload)
