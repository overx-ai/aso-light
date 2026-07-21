"""MCP tools for the keyword visibility tracker.

Mirrors the REST surface in ``app/api/v1/visibility.py``:
watch CRUD, on-demand polling, snapshot history, anomaly detection,
and share-of-voice computation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastmcp.exceptions import ToolError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.mcp.context import resolve_app, session_scope
from app.mcp.server import mcp
from app.models.visibility import (
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.schemas.visibility import (
    AnomaliesOut,
    AnomalyOut,
    CompetitorSiteOut,
    CompetitorSitesOut,
    FullSovOut,
    SnapshotListOut,
    SovOut,
    VisibilitySnapshotOut,
    WatchAnomaliesOut,
    WatchCreate,
    WatchListOut,
    WatchOut,
    is_known_storefront,
)
from app.services.visibility.anomaly import detect_anomalies
from app.services.visibility.competitors import collect_competitor_sites
from app.services.visibility.poller import poll_watch as poll_watch_service
from app.services.visibility.queries import (
    compute_sov_for_watch,
    latest_snapshot,
    load_watch,
    serialize_snapshot,
    snapshots_by_watch_in_window,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — the shared query/serialization logic lives in
# ``app.services.visibility.queries``; this wrapper just frames the
# not-found case as a ``ToolError`` for MCP clients.
# ---------------------------------------------------------------------------


async def _load_watch(
    session: AsyncSession, app_id: int, watch_id: int,
) -> KeywordVisibilityWatch:
    watch = await load_watch(session, app_id, watch_id)
    if watch is None:
        raise ToolError(f"Watch {watch_id} not found for this app")
    return watch


# ---------------------------------------------------------------------------
# Watch CRUD
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility_list_watches")
async def list_watches(app_id: int) -> WatchListOut:
    """List every watch (keyword + country) registered against an app, with
    each watch's latest snapshot inlined."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        stmt = (
            select(KeywordVisibilityWatch)
            .where(KeywordVisibilityWatch.app_id == app_id)
            .order_by(KeywordVisibilityWatch.text)
        )
        watches = (await session.execute(stmt)).scalars().all()
        if not watches:
            return WatchListOut(items=[])

        watch_ids = [w.id for w in watches]
        snaps_stmt = (
            select(KeywordVisibilitySnapshot)
            .where(KeywordVisibilitySnapshot.watch_id.in_(watch_ids))
            .order_by(desc(KeywordVisibilitySnapshot.polled_at))
            .options(selectinload(KeywordVisibilitySnapshot.results))
        )
        # Snapshots are ordered newest-first, so the first hit per watch is the latest.
        latest_by_watch: dict[int, KeywordVisibilitySnapshot] = {}
        for snap in (await session.execute(snaps_stmt)).scalars().all():
            latest_by_watch.setdefault(snap.watch_id, snap)

        items: list[WatchOut] = []
        for w in watches:
            snap = latest_by_watch.get(w.id)
            items.append(
                WatchOut(
                    id=w.id,
                    text=w.text,
                    country=w.country,
                    last_polled_at=w.last_polled_at,
                    latest_snapshot=serialize_snapshot(snap) if snap else None,
                )
            )
        return WatchListOut(items=items)


@mcp.tool(name="visibility_create_watch")
async def create_watch(app_id: int, text: str, country: str) -> WatchOut:
    """Register a new (keyword, country) watch for the app."""
    body = WatchCreate(text=text, country=country)
    async with session_scope() as session:
        await resolve_app(app_id, session)

        text = body.text.strip()
        country = body.country.strip().lower()
        if not text:
            raise ToolError("text cannot be empty")
        if not is_known_storefront(country):
            raise ToolError(f"Unknown territory/storefront code: {country!r}")

        existing = await session.execute(
            select(KeywordVisibilityWatch).where(
                KeywordVisibilityWatch.app_id == app_id,
                KeywordVisibilityWatch.text == text,
                KeywordVisibilityWatch.country == country,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ToolError("This (keyword, country) is already being watched.")

        watch = KeywordVisibilityWatch(
            app_id=app_id, text=text, country=country,
        )
        session.add(watch)
        await session.flush()
        await session.refresh(watch)
        return WatchOut(
            id=watch.id,
            text=watch.text,
            country=watch.country,
            last_polled_at=watch.last_polled_at,
            latest_snapshot=None,
        )


@mcp.tool(name="visibility_delete_watch")
async def delete_watch(app_id: int, watch_id: int) -> dict[str, str]:
    """Delete a watch and all its historical snapshots."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        watch = await _load_watch(session, app_id, watch_id)
        await session.delete(watch)
        await session.flush()
        return {"detail": "Watch deleted"}


# ---------------------------------------------------------------------------
# Polling + snapshots
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility_poll_watch")
async def poll_watch(app_id: int, watch_id: int) -> VisibilitySnapshotOut:
    """Poll the iTunes SERP for a watch right now and persist a fresh snapshot."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        watch = await _load_watch(session, app_id, watch_id)

        snapshot = await poll_watch_service(watch, session)
        await session.flush()

        # Re-load with results eagerly populated for serialization; fall back
        # to the freshly-polled snapshot if the reload comes up empty.
        full = await latest_snapshot(session, watch.id)
        return serialize_snapshot(full or snapshot)


@mcp.tool(name="visibility_list_snapshots")
async def list_snapshots(
    app_id: int,
    watch_id: int,
    days: int = 30,
) -> SnapshotListOut:
    """List historical snapshots for a watch over the last ``days``-day window."""
    if not 1 <= days <= 365:
        raise ToolError("days must be between 1 and 365")
    async with session_scope() as session:
        await resolve_app(app_id, session)
        await _load_watch(session, app_id, watch_id)

        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(KeywordVisibilitySnapshot)
            .where(
                KeywordVisibilitySnapshot.watch_id == watch_id,
                KeywordVisibilitySnapshot.polled_at >= since,
            )
            .order_by(desc(KeywordVisibilitySnapshot.polled_at))
            .options(selectinload(KeywordVisibilitySnapshot.results))
        )
        snaps = (await session.execute(stmt)).scalars().all()
        return SnapshotListOut(items=[serialize_snapshot(s) for s in snaps])


# ---------------------------------------------------------------------------
# Anomalies + share of voice
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility_list_anomalies")
async def list_anomalies(
    app_id: int,
    days: int = 14,
    min_delta: int = 5,
) -> AnomaliesOut:
    """Surface position surges/drops/new/gone tracks per watch, comparing the
    latest snapshot to a ``days``-day rolling median.

    ``min_delta`` is the minimum position change required to flag a surge or
    drop.
    """
    if not 1 <= days <= 180:
        raise ToolError("days must be between 1 and 180")
    if not 1 <= min_delta <= 200:
        raise ToolError("min_delta must be between 1 and 200")

    async with session_scope() as session:
        await resolve_app(app_id, session)
        watches_stmt = select(KeywordVisibilityWatch).where(
            KeywordVisibilityWatch.app_id == app_id,
        )
        watches = (await session.execute(watches_stmt)).scalars().all()
        if not watches:
            return AnomaliesOut(items=[])

        since = datetime.now(timezone.utc) - timedelta(days=days)
        by_watch = await snapshots_by_watch_in_window(
            session, [w.id for w in watches], since,
        )

        items: list[WatchAnomaliesOut] = []
        for w in watches:
            snaps = by_watch.get(w.id, [])
            anomalies = detect_anomalies(snaps, min_delta=min_delta)
            items.append(
                WatchAnomaliesOut(
                    watch_id=w.id,
                    text=w.text,
                    country=w.country,
                    polls=len(snaps),
                    anomalies=[
                        AnomalyOut(
                            kind=a.kind,
                            track_id=a.track_id,
                            name=a.name,
                            icon_url=a.icon_url,
                            prev_median_position=a.prev_median_position,
                            latest_position=a.latest_position,
                            delta=a.delta,
                        )
                        for a in anomalies
                    ],
                )
            )
        return AnomaliesOut(items=items)


@mcp.tool(name="visibility_get_sov")
async def get_sov(app_id: int, days: int = 30) -> FullSovOut:
    """Compute share-of-voice across every watch — for each watch, lists the
    top-N tracks and the % of polls in the window where they landed in the
    top 3 positions."""
    if not 1 <= days <= 365:
        raise ToolError("days must be between 1 and 365")
    async with session_scope() as session:
        await resolve_app(app_id, session)
        watch_stmt = select(KeywordVisibilityWatch).where(
            KeywordVisibilityWatch.app_id == app_id,
        )
        watches = (await session.execute(watch_stmt)).scalars().all()
        if not watches:
            return FullSovOut(items=[])

        since = datetime.now(timezone.utc) - timedelta(days=days)
        by_watch = await snapshots_by_watch_in_window(
            session, [w.id for w in watches], since,
        )

        items: list[SovOut] = []
        for w in watches:
            polls, entries = compute_sov_for_watch(by_watch.get(w.id, []))
            items.append(
                SovOut(
                    watch_id=w.id,
                    text=w.text,
                    country=w.country,
                    polls=polls,
                    days=days,
                    entries=entries,
                )
            )
        return FullSovOut(items=items)


# ---------------------------------------------------------------------------
# Competitor developer sites
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility_competitor_sites")
async def competitor_sites(
    app_id: int,
    watch_id: int | None = None,
) -> CompetitorSitesOut:
    """Export the developer websites of competitor apps appearing in an app's
    visibility watches.

    Collects the distinct competitor apps from each watch's latest snapshot and
    enriches them with developer website + App Store URL via a single batched
    iTunes lookup. Pass ``watch_id`` to scope the export to one watch.
    """
    async with session_scope() as session:
        await resolve_app(app_id, session)

        if watch_id is not None:
            watches = [await _load_watch(session, app_id, watch_id)]
        else:
            stmt = select(KeywordVisibilityWatch).where(
                KeywordVisibilityWatch.app_id == app_id,
            )
            watches = list((await session.execute(stmt)).scalars().all())

        try:
            sites = await collect_competitor_sites(session, watches=watches)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — never leak raw tracebacks
            logger.exception("competitor_sites failed for app_id=%s", app_id)
            raise ToolError("Could not collect competitor sites.") from exc

        return CompetitorSitesOut(
            items=[CompetitorSiteOut(**s) for s in sites],
        )
