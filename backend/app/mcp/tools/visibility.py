"""MCP tools for the keyword visibility tracker.

Mirrors the REST surface in ``app/api/v1/visibility.py``:
watch CRUD, on-demand polling, snapshot history, anomaly detection,
and share-of-voice computation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

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
    FullSovOut,
    SnapshotListOut,
    SovEntry,
    SovOut,
    VisibilityResultOut,
    VisibilitySnapshotOut,
    WatchAnomaliesOut,
    WatchCreate,
    WatchListOut,
    WatchOut,
)
from app.services.visibility.anomaly import detect_anomalies
from app.services.visibility.poller import poll_watch as poll_watch_service

logger = logging.getLogger(__name__)

SOV_TOP_N = 3
SOV_MAX_ENTRIES = 10


# ---------------------------------------------------------------------------
# Helpers — kept aligned with the REST router so behaviour matches exactly.
# ---------------------------------------------------------------------------


async def _load_watch(
    session: AsyncSession, app_id: int, watch_id: int,
) -> KeywordVisibilityWatch:
    stmt = select(KeywordVisibilityWatch).where(
        KeywordVisibilityWatch.id == watch_id,
        KeywordVisibilityWatch.app_id == app_id,
    )
    watch = (await session.execute(stmt)).scalar_one_or_none()
    if watch is None:
        raise ToolError(f"Watch {watch_id} not found for this app")
    return watch


async def _latest_snapshot(
    session: AsyncSession, watch_id: int,
) -> KeywordVisibilitySnapshot | None:
    stmt = (
        select(KeywordVisibilitySnapshot)
        .where(KeywordVisibilitySnapshot.watch_id == watch_id)
        .order_by(desc(KeywordVisibilitySnapshot.polled_at))
        .options(selectinload(KeywordVisibilitySnapshot.results))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _snapshots_by_watch_in_window(
    session: AsyncSession,
    watch_ids: list[int],
    since: datetime,
) -> dict[int, list[KeywordVisibilitySnapshot]]:
    stmt = (
        select(KeywordVisibilitySnapshot)
        .where(
            KeywordVisibilitySnapshot.watch_id.in_(watch_ids),
            KeywordVisibilitySnapshot.polled_at >= since,
        )
        .order_by(desc(KeywordVisibilitySnapshot.polled_at))
        .options(selectinload(KeywordVisibilitySnapshot.results))
    )
    grouped: dict[int, list[KeywordVisibilitySnapshot]] = {wid: [] for wid in watch_ids}
    for snap in (await session.execute(stmt)).scalars().all():
        grouped[snap.watch_id].append(snap)
    return grouped


def _serialize_snapshot(snap: KeywordVisibilitySnapshot) -> VisibilitySnapshotOut:
    return VisibilitySnapshotOut(
        id=snap.id,
        polled_at=snap.polled_at,
        results_count=snap.results_count,
        results=[
            VisibilityResultOut.model_validate(r)
            for r in sorted(snap.results, key=lambda r: r.position)
        ],
    )


def _compute_sov_for_watch(
    snapshots: list[KeywordVisibilitySnapshot],
) -> tuple[int, list[SovEntry]]:
    polls = len(snapshots)
    if polls == 0:
        return 0, []

    counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"appearances": 0, "name": "", "icon_url": ""},
    )
    for snap in snapshots:
        for result in sorted(snap.results, key=lambda x: x.position)[:SOV_TOP_N]:
            entry = counts[result.track_id]
            entry["appearances"] += 1
            entry["name"] = result.name
            entry["icon_url"] = result.icon_url

    entries = [
        SovEntry(
            track_id=track_id,
            name=info["name"],
            icon_url=info["icon_url"],
            appearances=info["appearances"],
            polls=polls,
            sov_pct=round(info["appearances"] / polls * 100, 2),
        )
        for track_id, info in counts.items()
    ]
    entries.sort(key=lambda e: e.appearances, reverse=True)
    return polls, entries[:SOV_MAX_ENTRIES]


# ---------------------------------------------------------------------------
# Watch CRUD
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility.list_watches")
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
                    latest_snapshot=_serialize_snapshot(snap) if snap else None,
                )
            )
        return WatchListOut(items=items)


@mcp.tool(name="visibility.create_watch")
async def create_watch(app_id: int, text: str, country: str) -> WatchOut:
    """Register a new (keyword, country) watch for the app."""
    body = WatchCreate(text=text, country=country)
    async with session_scope() as session:
        await resolve_app(app_id, session)

        text = body.text.strip()
        country = body.country.strip().lower()
        if not text:
            raise ToolError("text cannot be empty")

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


@mcp.tool(name="visibility.delete_watch")
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


@mcp.tool(name="visibility.poll_watch")
async def poll_watch(app_id: int, watch_id: int) -> VisibilitySnapshotOut:
    """Poll the iTunes SERP for a watch right now and persist a fresh snapshot."""
    async with session_scope() as session:
        await resolve_app(app_id, session)
        watch = await _load_watch(session, app_id, watch_id)

        snapshot = await poll_watch_service(watch, session)
        await session.flush()

        # Re-load with results eagerly populated for serialization; fall back
        # to the freshly-polled snapshot if the reload comes up empty.
        full = await _latest_snapshot(session, watch.id)
        return _serialize_snapshot(full or snapshot)


@mcp.tool(name="visibility.list_snapshots")
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
        return SnapshotListOut(items=[_serialize_snapshot(s) for s in snaps])


# ---------------------------------------------------------------------------
# Anomalies + share of voice
# ---------------------------------------------------------------------------


@mcp.tool(name="visibility.list_anomalies")
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
        by_watch = await _snapshots_by_watch_in_window(
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


@mcp.tool(name="visibility.get_sov")
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
        by_watch = await _snapshots_by_watch_in_window(
            session, [w.id for w in watches], since,
        )

        items: list[SovOut] = []
        for w in watches:
            polls, entries = _compute_sov_for_watch(by_watch.get(w.id, []))
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
