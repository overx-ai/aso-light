"""Detect rank anomalies inside the visibility snapshot history.

A *surge* = a track's position improved (smaller number) by ``min_delta`` or
more compared to the median of its earlier appearances.
A *drop* = the opposite.
A *new* = the track appeared in the latest snapshot but never before in the
window.
A *gone* = the track was in the historical median but is missing from the
latest snapshot.

The router supplies a list of snapshots (newest-first) for a single watch;
this module is pure data — no DB access — so it stays trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Literal

from app.models.visibility import (
    KeywordVisibilityResult,
    KeywordVisibilitySnapshot,
)

AnomalyKind = Literal["surge", "drop", "new", "gone"]


@dataclass(frozen=True)
class Anomaly:
    kind: AnomalyKind
    track_id: str
    name: str
    icon_url: str
    prev_median_position: int | None  # None for "new"
    latest_position: int | None       # None for "gone"
    delta: int                        # negative = improvement


def _result_index(
    snap: KeywordVisibilitySnapshot,
) -> dict[str, KeywordVisibilityResult]:
    return {r.track_id: r for r in snap.results}


def detect_anomalies(
    snapshots_newest_first: Iterable[KeywordVisibilitySnapshot],
    *,
    min_delta: int = 5,
    history_min: int = 2,
) -> list[Anomaly]:
    """Return anomalies for one watch's snapshot history.

    Args:
      snapshots_newest_first: ordered newest -> oldest, length >= 1.
      min_delta: minimum absolute position change to flag a surge/drop.
      history_min: minimum number of historical (non-latest) snapshots a track
        needs to have appeared in before we'll compare medians. Below this we
        only emit ``new`` / ``gone`` events for cleanliness.
    """
    snaps = list(snapshots_newest_first)
    if not snaps:
        return []

    latest = snaps[0]
    prior = snaps[1:]
    latest_idx = _result_index(latest)

    # Build per-track history of positions across the prior snapshots.
    history: dict[str, list[int]] = {}
    history_meta: dict[str, KeywordVisibilityResult] = {}
    for snap in prior:
        for result in snap.results:
            history.setdefault(result.track_id, []).append(result.position)
            history_meta[result.track_id] = result

    anomalies: list[Anomaly] = []

    # Tracks present in latest
    for track_id, latest_result in latest_idx.items():
        positions = history.get(track_id, [])
        if not positions:
            anomalies.append(
                Anomaly(
                    kind="new",
                    track_id=track_id,
                    name=latest_result.name,
                    icon_url=latest_result.icon_url,
                    prev_median_position=None,
                    latest_position=latest_result.position,
                    delta=0,
                )
            )
            continue
        if len(positions) < history_min:
            continue
        prev_med = int(round(median(positions)))
        delta = latest_result.position - prev_med
        if abs(delta) >= min_delta:
            anomalies.append(
                Anomaly(
                    kind="surge" if delta < 0 else "drop",
                    track_id=track_id,
                    name=latest_result.name,
                    icon_url=latest_result.icon_url,
                    prev_median_position=prev_med,
                    latest_position=latest_result.position,
                    delta=delta,
                )
            )

    # Tracks that vanished from latest
    for track_id, positions in history.items():
        if track_id in latest_idx or len(positions) < history_min:
            continue
        prev_med = int(round(median(positions)))
        meta = history_meta[track_id]
        anomalies.append(
            Anomaly(
                kind="gone",
                track_id=track_id,
                name=meta.name,
                icon_url=meta.icon_url,
                prev_median_position=prev_med,
                latest_position=None,
                delta=0,
            )
        )

    # Most "interesting" anomalies first: surges/drops by |delta|, then new, then gone.
    _KIND_RANK = {"surge": 0, "drop": 1, "new": 2, "gone": 3}
    anomalies.sort(key=lambda a: (_KIND_RANK[a.kind], -abs(a.delta)))
    return anomalies
