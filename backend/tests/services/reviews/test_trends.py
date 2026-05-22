from __future__ import annotations

from datetime import date

from app.schemas.review import ReviewOut
from app.services.reviews.trends import build_review_trend


def _review(
    review_id: str,
    rating: int,
    created_date: str,
) -> ReviewOut:
    return ReviewOut(
        id=review_id,
        rating=rating,
        title=None,
        body=None,
        territory="USA",
        reviewer_nickname=None,
        created_date=created_date,
        response=None,
    )


def test_build_review_trend_zero_fills_window_and_counts_low_ratings() -> None:
    trend = build_review_trend(
        [
            _review("r-1", 1, "2026-05-18T10:00:00Z"),
            _review("r-2", 5, "2026-05-18T11:00:00Z"),
            _review("r-3", 2, "2026-05-20T12:00:00Z"),
            _review("r-4", 4, "2026-05-22T13:00:00Z"),
        ],
        today=date(2026, 5, 22),
        days=5,
        low_rating_max=2,
    )

    assert [point.date for point in trend.points] == [
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
        "2026-05-22",
    ]
    assert [point.total_reviews for point in trend.points] == [2, 0, 1, 0, 1]
    assert [point.low_rating_reviews for point in trend.points] == [1, 0, 1, 0, 0]
    assert [point.average_rating for point in trend.points] == [3.0, None, 2.0, None, 4.0]

    assert trend.summary.total_reviews == 4
    assert trend.summary.low_rating_reviews == 2
    assert trend.summary.low_rating_share_pct == 50.0
    assert trend.summary.average_rating == 3.0


def test_build_review_trend_ignores_older_reviews_and_finds_biggest_spike() -> None:
    trend = build_review_trend(
        [
            _review("r-0", 1, "2026-05-10T10:00:00Z"),
            _review("r-1", 1, "2026-05-19T09:00:00Z"),
            _review("r-2", 2, "2026-05-20T09:00:00Z"),
            _review("r-3", 1, "2026-05-20T11:00:00Z"),
            _review("r-4", 5, "2026-05-21T14:00:00Z"),
        ],
        today=date(2026, 5, 21),
        days=3,
        low_rating_max=2,
    )

    assert [point.low_rating_reviews for point in trend.points] == [1, 2, 0]
    assert trend.summary.biggest_spike_date == "2026-05-20"
    assert trend.summary.biggest_spike_delta == 1
    assert trend.summary.biggest_drop_date == "2026-05-21"
    assert trend.summary.biggest_drop_delta == -2
