"""Shared, non-HTTP query + serialization helpers for the visibility tracker.

Both the REST router (``app/api/v1/visibility.py``) and the MCP tool surface
(``app/mcp/tools/visibility.py``) reuse these. Keeping them here is the
single source of truth for *how* visibility data is loaded, grouped, and
shaped — so the two layers can never drift apart.

Each layer keeps its own ownership check (``_get_verified_app`` /
``resolve_app``) and error mapping (``HTTPException`` vs ``ToolError``) at the
edges. These helpers raise nothing layer-specific: ``load_watch`` returns
``None`` when the watch is missing so each caller can frame the not-found error
in its own dialect.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.visibility import (
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.schemas.visibility import (
    SovEntry,
    VisibilityResultOut,
    VisibilitySnapshotOut,
)

SOV_TOP_N = 3  # a track counts as "winning" if it lands in the top 3
SOV_MAX_ENTRIES = 10


async def load_watch(
    session: AsyncSession, app_id: int, watch_id: int,
) -> KeywordVisibilityWatch | None:
    """Load a watch scoped to its app, or ``None`` if it does not exist.

    Callers map the ``None`` case to their own not-found error.
    """
    stmt = select(KeywordVisibilityWatch).where(
        KeywordVisibilityWatch.id == watch_id,
        KeywordVisibilityWatch.app_id == app_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_snapshot(
    session: AsyncSession, watch_id: int,
) -> KeywordVisibilitySnapshot | None:
    """Return the newest snapshot for a watch with its results eager-loaded."""
    stmt = (
        select(KeywordVisibilitySnapshot)
        .where(KeywordVisibilitySnapshot.watch_id == watch_id)
        .order_by(desc(KeywordVisibilitySnapshot.polled_at))
        .options(selectinload(KeywordVisibilitySnapshot.results))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def snapshots_by_watch_in_window(
    session: AsyncSession,
    watch_ids: list[int],
    since: datetime,
) -> dict[int, list[KeywordVisibilitySnapshot]]:
    """Batch-load snapshots (results eager-loaded) since ``since``, ordered
    newest-first, grouped by watch_id."""
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


def serialize_snapshot(snap: KeywordVisibilitySnapshot) -> VisibilitySnapshotOut:
    """Shape a snapshot ORM row into its API schema, results sorted by rank."""
    return VisibilitySnapshotOut(
        id=snap.id,
        polled_at=snap.polled_at,
        results_count=snap.results_count,
        results=[
            VisibilityResultOut.model_validate(r)
            for r in sorted(snap.results, key=lambda r: r.position)
        ],
    )


def compute_sov_for_watch(
    snapshots: list[KeywordVisibilitySnapshot],
) -> tuple[int, list[SovEntry]]:
    """Return ``(poll_count, top entries)`` where each entry's ``appearances``
    counts polls in which the track landed in the top ``SOV_TOP_N`` positions."""
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
