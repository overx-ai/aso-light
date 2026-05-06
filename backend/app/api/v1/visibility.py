"""Keyword visibility tracker endpoints (organic SERP snapshots)."""
from __future__ import annotations

import logging
from collections import defaultdict
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
    KeywordVisibilityResult,
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.schemas.visibility import (
    FullSovOut,
    SnapshotListOut,
    SovEntry,
    SovOut,
    VisibilityResultOut,
    VisibilitySnapshotOut,
    WatchCreate,
    WatchListOut,
    WatchOut,
)
from app.services.visibility.poller import MAX_RESULTS_PER_SNAPSHOT, poll_watch

logger = logging.getLogger(__name__)
router = APIRouter()

SOV_TOP_N = 3  # an app counts as "winning" if it lands in the top 3
SOV_MAX_ENTRIES = 10


# ----------------------------------------------------------------------
# Watch CRUD
# ----------------------------------------------------------------------


async def _load_watch(
    session: AsyncSession, app_id: int, watch_id: int,
) -> KeywordVisibilityWatch:
    stmt = select(KeywordVisibilityWatch).where(
        KeywordVisibilityWatch.id == watch_id,
        KeywordVisibilityWatch.app_id == app_id,
    )
    watch = (await session.execute(stmt)).scalar_one_or_none()
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watch {watch_id} not found for this app",
        )
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


def _serialize_snapshot(
    snap: KeywordVisibilitySnapshot,
) -> VisibilitySnapshotOut:
    return VisibilitySnapshotOut(
        id=snap.id,
        polled_at=snap.polled_at,
        results_count=snap.results_count,
        results=[
            VisibilityResultOut.model_validate(r)
            for r in sorted(snap.results, key=lambda r: r.position)
        ],
    )


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

    items: list[WatchOut] = []
    for w in watches:
        snap = await _latest_snapshot(session, w.id)
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
    await session.flush()
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
    full = await _latest_snapshot(session, watch.id)
    if full is None:
        # Defensive — the poll just inserted one.
        return _serialize_snapshot(snapshot)
    return _serialize_snapshot(full)


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
    return SnapshotListOut(items=[_serialize_snapshot(s) for s in snaps])


# ----------------------------------------------------------------------
# Share of voice
# ----------------------------------------------------------------------


def _compute_sov_for_watch(
    snapshots: list[KeywordVisibilitySnapshot],
) -> tuple[int, list[SovEntry]]:
    """Return (poll_count, top entries) where each entry's appearances counts
    polls in which the track landed in the top SOV_TOP_N positions."""
    polls = len(snapshots)
    if polls == 0:
        return 0, []

    counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"appearances": 0, "name": "", "icon_url": ""},
    )
    for snap in snapshots:
        for r in sorted(snap.results, key=lambda x: x.position)[:SOV_TOP_N]:
            entry = counts[r.track_id]
            entry["appearances"] += 1
            entry["name"] = r.name
            entry["icon_url"] = r.icon_url

    entries: list[SovEntry] = [
        SovEntry(
            track_id=track_id,
            name=info["name"],
            icon_url=info["icon_url"],
            appearances=info["appearances"],
            polls=polls,
            sov_pct=round(info["appearances"] / polls * 100, 2) if polls else 0.0,
        )
        for track_id, info in counts.items()
    ]
    entries.sort(key=lambda e: e.appearances, reverse=True)
    return polls, entries[:SOV_MAX_ENTRIES]


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
    items: list[SovOut] = []
    for w in watches:
        snap_stmt = (
            select(KeywordVisibilitySnapshot)
            .where(
                KeywordVisibilitySnapshot.watch_id == w.id,
                KeywordVisibilitySnapshot.polled_at >= since,
            )
            .options(selectinload(KeywordVisibilitySnapshot.results))
        )
        snaps = (await session.execute(snap_stmt)).scalars().all()
        polls, entries = _compute_sov_for_watch(snaps)
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
