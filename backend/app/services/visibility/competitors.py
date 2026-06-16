"""Collect competitor developer websites from visibility watches.

Shared by the REST router (``app/api/v1/visibility.py``) and the MCP tool
surface (``app/mcp/tools/visibility.py``). Keeping the iTunes-enrichment and
de-dup logic here is the single source of truth, so the two layers can never
drift apart — each layer only handles ownership checks and error framing at
its edge.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visibility import KeywordVisibilityWatch
from app.services.keywords.itunes_search import (
    ITunesSearchService,
    is_valid_track_id,
)
from app.services.visibility.queries import latest_snapshot

# iTunes /lookup accepts a comma-separated list of ids; keep batches well under
# the URL-length ceiling. 180 mirrors the cap used elsewhere for SERP lookups.
_LOOKUP_BATCH_SIZE = 180


async def collect_competitor_sites(
    session: AsyncSession,
    *,
    watches: list[KeywordVisibilityWatch],
    itunes: ITunesSearchService | None = None,
) -> list[dict[str, Any]]:
    """Gather distinct competitor apps from each watch's LATEST snapshot and
    enrich them with the developer website + App Store URL.

    For every watch, the newest snapshot's results are collected and de-duped
    by ``track_id`` across all watches; each app records the watch text(s)
    (with country) it appears under. The distinct track ids are then enriched
    via one batched iTunes ``/lookup`` (single shared client), and the rows are
    returned as dicts::

        {track_id, name, seller, website, app_store_url, keywords: [str]}

    Apps with no ``sellerUrl`` get ``website=""`` (``app_store_url`` is the
    usable fallback). The result is sorted by ``name`` (case-insensitive). No
    external call is made when there are no track ids — an empty list is
    returned.
    """
    itunes = itunes or ITunesSearchService()

    # track_id -> aggregated competitor record. ``keywords`` is a set so the
    # same app surfacing under several watches collapses to distinct labels.
    apps: dict[str, dict[str, Any]] = {}
    # Remember a representative storefront per id for the iTunes lookup; default
    # to "us" but prefer the watch's own country where one exists.
    country_by_track: dict[str, str] = {}

    for watch in watches:
        snapshot = await latest_snapshot(session, watch.id)
        if snapshot is None:
            continue
        label = f"{watch.text} ({watch.country.upper()})"
        for result in snapshot.results:
            track_id = result.track_id
            if not is_valid_track_id(track_id):
                continue
            record = apps.get(track_id)
            if record is None:
                record = {"name": result.name, "keywords": set()}
                apps[track_id] = record
                country_by_track[track_id] = watch.country or "us"
            record["keywords"].add(label)

    if not apps:
        return []

    lookup_by_track = await _lookup_records(
        itunes, list(apps.keys()), country_by_track,
    )

    rows: list[dict[str, Any]] = []
    for track_id, record in apps.items():
        raw = lookup_by_track.get(track_id, {})
        rows.append(
            {
                "track_id": track_id,
                "name": raw.get("trackName") or record["name"],
                "seller": raw.get("sellerName") or None,
                "website": raw.get("sellerUrl") or "",
                "app_store_url": raw.get("trackViewUrl") or None,
                "keywords": sorted(record["keywords"]),
            }
        )

    rows.sort(key=lambda r: r["name"].lower())
    return rows


async def _lookup_records(
    itunes: ITunesSearchService,
    track_ids: list[str],
    country_by_track: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Batch the distinct track ids through iTunes ``/lookup`` over one shared
    client and return a ``{trackId: raw_record}`` map.

    Ids are grouped by their representative storefront so each batch uses a
    coherent ``country`` param; the iTunes service already fails soft (returns
    ``[]``) on transport errors, so a flaky storefront just yields empty
    enrichment for those ids rather than raising.
    """
    by_country: dict[str, list[str]] = {}
    for track_id in track_ids:
        by_country.setdefault(country_by_track.get(track_id, "us"), []).append(
            track_id,
        )

    out: dict[str, dict[str, Any]] = {}
    client = httpx.AsyncClient(timeout=15.0)
    try:
        for country, ids in by_country.items():
            for start in range(0, len(ids), _LOOKUP_BATCH_SIZE):
                batch = ids[start : start + _LOOKUP_BATCH_SIZE]
                records = await itunes.lookup_apps(
                    batch, country=country, client=client,
                )
                for rec in records:
                    rec_id = str(rec.get("trackId", ""))
                    if rec_id:
                        out[rec_id] = rec
    finally:
        await client.aclose()
    return out
