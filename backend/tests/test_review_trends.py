"""Tests for customer review trend aggregation."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.review import ReviewOut
from app.services.reviews.trends import ReviewTrendAnalyzer


def _review(
    review_id: str,
    *,
    rating: int,
    created_date: str,
    body: str | None,
    title: str | None = None,
) -> ReviewOut:
    return ReviewOut(
        id=review_id,
        rating=rating,
        title=title,
        body=body,
        created_date=created_date,
    )


def test_review_trends_bucket_low_ratings_and_themes() -> None:
    analyzer = ReviewTrendAnalyzer(
        now=datetime(2026, 5, 23, 12, tzinfo=timezone.utc),
    )
    trends = analyzer.build(
        [
            _review(
                "r1",
                rating=1,
                created_date="2026-05-22T10:00:00Z",
                body="Crashes every time I open it.",
            ),
            _review(
                "r2",
                rating=2,
                created_date="2026-05-23T09:00:00Z",
                body="The subscription price is too expensive.",
            ),
            _review(
                "r3",
                rating=5,
                created_date="2026-05-23T10:00:00Z",
                body="Works great now.",
            ),
            _review(
                "old",
                rating=1,
                created_date="2026-05-01T10:00:00Z",
                body="Old crash report.",
            ),
        ],
        days=3,
    )

    assert trends.start_date == "2026-05-21"
    assert trends.end_date == "2026-05-23"
    assert trends.total_reviews == 3
    assert trends.low_rating_total == 2
    assert trends.average_rating == 2.67
    assert [point.date for point in trends.points] == [
        "2026-05-21",
        "2026-05-22",
        "2026-05-23",
    ]
    assert [point.low_rating for point in trends.points] == [0, 1, 1]
    assert trends.points[2].total == 2

    theme_totals = {theme.theme: theme.total for theme in trends.themes}
    assert theme_totals["Stability"] == 1
    assert theme_totals["Pricing"] == 1


def test_review_trends_surface_spikes_and_drops() -> None:
    analyzer = ReviewTrendAnalyzer(
        now=datetime(2026, 5, 23, 12, tzinfo=timezone.utc),
    )
    trends = analyzer.build(
        [
            _review(
                "r1",
                rating=1,
                created_date="2026-05-21T10:00:00Z",
                body="Login is broken.",
            ),
            _review(
                "r2",
                rating=1,
                created_date="2026-05-22T10:00:00Z",
                body="Login is broken.",
            ),
            _review(
                "r3",
                rating=1,
                created_date="2026-05-22T11:00:00Z",
                body="Cannot log in.",
            ),
            _review(
                "r4",
                rating=5,
                created_date="2026-05-23T10:00:00Z",
                body="Fixed.",
            ),
        ],
        days=3,
    )

    insights = {
        (insight.metric, insight.kind): insight.change
        for insight in trends.insights
    }
    assert insights[("Low ratings", "spike")] == 1
    assert insights[("Low ratings", "drop")] == -2
    assert insights[("Login", "spike")] == 1
