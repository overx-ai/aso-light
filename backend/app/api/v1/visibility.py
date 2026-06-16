"""Keyword visibility tracker endpoints (organic SERP snapshots)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1._deps import _get_verified_app
from app.core.security import get_current_user
from app.db.session import get_session
from app.models.visibility import (
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.schemas.visibility import (
    AnomaliesOut,
    AnomalyOut,
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
from app.services.visibility.poller import poll_watch
from app.services.visibility.queries import (
    compute_sov_for_watch,
    latest_snapshot,
    load_watch,
    serialize_snapshot,
    snapshots_by_watch_in_window,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ----------------------------------------------------------------------
# Watch CRUD
# ----------------------------------------------------------------------


async def _load_watch(
    session: AsyncSession, app_id: int, watch_id: int,
) -> KeywordVisibilityWatch:
    """Load a watch or raise 404. Wraps the shared loader with the REST error."""
    watch = await load_watch(session, app_id, watch_id)
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watch {watch_id} not found for this app",
        )
    return watch


@router.get("/{app_id}/visibility/watches", response_model=WatchListOut)
async def list_watches(
    app_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WatchListOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    stmt = (
        select(KeywordVisibilityWatch)
        .where(KeywordVisibilityWatch.app_id == app_id)
        .order_by(KeywordVisibilityWatch.text)
    )
    watches = (await session.execute(stmt)).scalars().all()
    if not watches:
        return WatchListOut(items=[])

    # Batch: fetch all snapshots for these watches in one query (with results
    # eager-loaded), then pick the newest per watch in Python. Beats N+1.
    watch_ids = [w.id for w in watches]
    snaps_stmt = (
        select(KeywordVisibilitySnapshot)
        .where(KeywordVisibilitySnapshot.watch_id.in_(watch_ids))
        .order_by(desc(KeywordVisibilitySnapshot.polled_at))
        .options(selectinload(KeywordVisibilitySnapshot.results))
    )
    all_snaps = (await session.execute(snaps_stmt)).scalars().all()
    latest_by_watch: dict[int, KeywordVisibilitySnapshot] = {}
    for snap in all_snaps:
        # Sorted desc above, so the first one we see per watch_id is the newest.
        if snap.watch_id not in latest_by_watch:
            latest_by_watch[snap.watch_id] = snap

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


@router.post(
    "/{app_id}/visibility/watches",
    response_model=WatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_watch(
    app_id: int,
    body: WatchCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WatchOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

    text = body.text.strip()
    country = body.country.strip().lower()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text cannot be empty",
        )
    if not is_known_storefront(country):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown territory/storefront code: {body.country!r}",
        )

    existing = await session.execute(
        select(KeywordVisibilityWatch).where(
            KeywordVisibilityWatch.app_id == app_id,
            KeywordVisibilityWatch.text == text,
            KeywordVisibilityWatch.country == country,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This (keyword, country) is already being watched.",
        )

    watch = KeywordVisibilityWatch(
        app_id=app_id, text=text, country=country,
    )
    session.add(watch)
    await session.commit()

    return WatchOut(
        id=watch.id,
        text=watch.text,
        country=watch.country,
        last_polled_at=watch.last_polled_at,
        latest_snapshot=None,
    )


@router.delete(
    "/{app_id}/visibility/watches/{watch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_watch(
    app_id: int,
    watch_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    watch = await _load_watch(session, app_id, watch_id)
    await session.delete(watch)
    await session.commit()


# ----------------------------------------------------------------------
# Polling + snapshots
# ----------------------------------------------------------------------


@router.post(
    "/{app_id}/visibility/watches/{watch_id}/poll",
    response_model=VisibilitySnapshotOut,
)
async def poll_now(
    app_id: int,
    watch_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> VisibilitySnapshotOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
    watch = await _load_watch(session, app_id, watch_id)

    snapshot = await poll_watch(watch, session)
    await session.commit()

    # Reload with results eagerly for the response.
    full = await latest_snapshot(session, watch.id)
    if full is None:
        # Defensive — the poll just inserted one.
        return serialize_snapshot(snapshot)
    return serialize_snapshot(full)


@router.get(
    "/{app_id}/visibility/watches/{watch_id}/snapshots",
    response_model=SnapshotListOut,
)
async def list_snapshots(
    app_id: int,
    watch_id: int,
    days: int = Query(default=30, ge=1, le=365),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SnapshotListOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)
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


# ----------------------------------------------------------------------
# Share of voice
# ----------------------------------------------------------------------


@router.get("/{app_id}/visibility/anomalies", response_model=AnomaliesOut)
async def list_anomalies(
    app_id: int,
    days: int = Query(default=14, ge=1, le=180),
    min_delta: int = Query(default=5, ge=1, le=200),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AnomaliesOut:
    """For each watched (keyword, country), surface position surges/drops
    plus tracks that newly appeared in or vanished from the latest snapshot
    relative to a ``days``-day median.
    """
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

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


@router.get("/{app_id}/visibility/sov", response_model=FullSovOut)
async def share_of_voice(
    app_id: int,
    days: int = Query(default=30, ge=1, le=365),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FullSovOut:
    user_id = int(current_user["user_id"])
    await _get_verified_app(app_id, user_id, session)

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
