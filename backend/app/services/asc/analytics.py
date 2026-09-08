"""App Store Connect Analytics Reports API — the request/download chain.

Pure API surface, no DB access — the same split as
:mod:`app.services.asa.reports`. The service layer
(:mod:`app.services.analytics`) owns parsing and persistence.

Apple's flow is asynchronous and four hops deep::

    POST /v1/analyticsReportRequests           (enroll once per app+accessType)
     -> GET /v1/analyticsReportRequests/{id}/reports?filter[category]=…
     -> GET /v1/analyticsReports/{id}/instances
     -> GET /v1/analyticsReportInstances/{id}/segments
     -> GET <pre-signed segment url>           (gzipped, tab-delimited)

Notes that bite:

* The **first** request for a report type requires an **Admin** key. A
  Sales-and-Reports key can download afterwards but cannot enroll.
* An ``ONGOING`` enrollment produces nothing for 24–48h, and a given day is
  only complete two days later. ``ONE_TIME_SNAPSHOT`` is what returns history
  immediately.
* Segment URLs are pre-signed — download them with
  :meth:`ASCClient._get_binary`, never with the Bearer token attached.
"""
from __future__ import annotations

import logging
from typing import Any

from app.models.analytics import ACCESS_ONGOING
from app.services.asc.client import ASCClient
from app.services.asc.errors import ASCAPIError

logger = logging.getLogger(__name__)

# Apple report categories we ingest. The full accepted set, per Apple's own
# 400 response, is: APP_USAGE, APP_STORE_ENGAGEMENT, COMMERCE, FRAMEWORK_USAGE,
# PERFORMANCE — note COMMERCE is NOT prefixed the way ENGAGEMENT is.
CATEGORY_ENGAGEMENT = "APP_STORE_ENGAGEMENT"
CATEGORY_COMMERCE = "COMMERCE"

# Report names exactly as Apple labels them (confirmed against a live tenant,
# 2026-08-27). Every report ships in a Standard and a Detailed variant; only
# Detailed carries the Custom Product Page dimension, so CPP performance needs
# it. Standard is the fallback when Detailed is unavailable for an account.
REPORTS_ENGAGEMENT = (
    "App Store Discovery and Engagement Detailed",
    "App Store Discovery and Engagement Standard",
)
REPORTS_DOWNLOADS = (
    "App Downloads Detailed",
    "App Downloads Standard",
)


async def create_report_request(
    client: ASCClient,
    *,
    asc_app_id: str,
    access_type: str = ACCESS_ONGOING,
) -> dict[str, Any]:
    """Enroll an app for analytics reports.

    Returns the created ``analyticsReportRequests`` resource. Raises
    :class:`ASCAPIError` — a 403 here almost always means the key lacks the
    Admin role required for a first-time enrollment, which is worth surfacing
    verbatim rather than swallowing.
    """
    body = {
        "data": {
            "type": "analyticsReportRequests",
            "attributes": {"accessType": access_type},
            "relationships": {
                "app": {"data": {"type": "apps", "id": asc_app_id}},
            },
        }
    }
    payload = await client._post("/analyticsReportRequests", json=body)
    return payload.get("data") or {}


async def list_report_requests(
    client: ASCClient, *, asc_app_id: str,
) -> list[dict[str, Any]]:
    """Existing enrollments for an app (both access types)."""
    return await client._get_all_pages(
        f"/apps/{asc_app_id}/analyticsReportRequests",
        params={"filter[accessType]": "ONE_TIME_SNAPSHOT,ONGOING"},
    )


async def list_reports(
    client: ASCClient, *, request_id: str, category: str,
) -> list[dict[str, Any]]:
    """Reports available under one enrollment, filtered by category."""
    return await client._get_all_pages(
        f"/analyticsReportRequests/{request_id}/reports",
        params={"filter[category]": category},
    )


async def list_instances(
    client: ASCClient, *, report_id: str, granularity: str = "DAILY",
) -> list[dict[str, Any]]:
    """Generated instances of a report at one granularity."""
    return await client._get_all_pages(
        f"/analyticsReports/{report_id}/instances",
        params={"filter[granularity]": granularity},
    )


async def list_segments(
    client: ASCClient, *, instance_id: str,
) -> list[dict[str, Any]]:
    """Downloadable segments of a report instance (url + checksum + size)."""
    return await client._get_all_pages(
        f"/analyticsReportInstances/{instance_id}/segments",
        params={
            "fields[analyticsReportSegments]": "url,checksum,sizeInBytes",
        },
    )


async def download_segment(client: ASCClient, *, url: str) -> bytes:
    """Fetch one segment's gzipped payload from its pre-signed URL."""
    return await client._get_binary(url)


async def find_report(
    client: ASCClient,
    *,
    request_id: str,
    category: str,
    names: tuple[str, ...],
) -> dict[str, Any] | None:
    """Locate a report by name within a category, in preference order.

    ``names`` is tried in order and matched exactly (case-insensitive), so
    "… Detailed" wins over "… Standard" instead of a prefix match resolving to
    whichever Apple happens to list first. Returns None rather than raising:
    a report an account doesn't have is a skip, not a sync failure.
    """
    available = await list_reports(
        client, request_id=request_id, category=category
    )
    by_name = {
        ((r.get("attributes") or {}).get("name") or "").strip().lower(): r
        for r in available
    }
    for wanted in names:
        report = by_name.get(wanted.strip().lower())
        if report is not None:
            return report
    logger.info(
        "no analytics report matching %s in category %s (available: %s)",
        names,
        category,
        sorted(by_name),
    )
    return None


async def ensure_enrolled(
    client: ASCClient, *, asc_app_id: str, access_type: str,
) -> tuple[str, bool]:
    """Return ``(request_id, created)`` for an app's enrollment.

    Idempotent: reuses an existing enrollment of the same access type rather
    than POSTing a duplicate. Apple treats a repeat enrollment as a conflict,
    so the create path also tolerates 409 by re-reading the list.
    """
    for existing in await list_report_requests(client, asc_app_id=asc_app_id):
        attrs = existing.get("attributes") or {}
        if attrs.get("accessType") == access_type:
            return str(existing["id"]), False

    try:
        created = await create_report_request(
            client, asc_app_id=asc_app_id, access_type=access_type
        )
        return str(created["id"]), True
    except ASCAPIError as exc:
        if exc.status_code != 409:
            raise
        # Raced with another enrollment — re-read and use the winner.
        for existing in await list_report_requests(client, asc_app_id=asc_app_id):
            attrs = existing.get("attributes") or {}
            if attrs.get("accessType") == access_type:
                return str(existing["id"]), False
        raise
