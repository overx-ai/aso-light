"""Pure helpers for aggregating customer reviews into daily trend windows."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.schemas.review import (
    ReviewOut,
    ReviewTrendOut,
    ReviewTrendPointOut,
    ReviewTrendSummaryOut,
)


def _parse_review_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_review_trend(
    reviews: list[ReviewOut],
    *,
    days: int,
    low_rating_max: int = 2,
    today: date | None = None,
    territory: str | None = None,
    partial: bool = False,
) -> ReviewTrendOut:
    """Aggregate reviews into a zero-filled day-by-day trend window."""
    if days < 1:
        raise ValueError("days must be >= 1")
    if low_rating_max < 1 or low_rating_max > 5:
        raise ValueError("low_rating_max must be between 1 and 5")

    end_day = today or datetime.now(UTC).date()
    start_day = end_day - timedelta(days=days - 1)

    buckets: dict[str, dict[str, float | int]] = {}
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        buckets[day.isoformat()] = {
            "total_reviews": 0,
            "low_rating_reviews": 0,
            "replied_reviews": 0,
            "rating_sum": 0,
        }

    for review in reviews:
        created_at = _parse_review_datetime(review.created_date)
        if created_at is None:
            continue

        review_day = created_at.date()
        if review_day < start_day or review_day > end_day:
            continue

        bucket = buckets[review_day.isoformat()]
        bucket["total_reviews"] += 1
        bucket["rating_sum"] += review.rating
        if review.rating <= low_rating_max:
            bucket["low_rating_reviews"] += 1
        if review.response is not None:
            bucket["replied_reviews"] += 1

    points: list[ReviewTrendPointOut] = []
    total_reviews = 0
    low_rating_reviews = 0
    replied_reviews = 0
    rating_sum = 0

    for day in sorted(buckets):
        bucket = buckets[day]
        day_total = int(bucket["total_reviews"])
        day_low = int(bucket["low_rating_reviews"])
        day_replied = int(bucket["replied_reviews"])
        day_rating_sum = int(bucket["rating_sum"])

        total_reviews += day_total
        low_rating_reviews += day_low
        replied_reviews += day_replied
        rating_sum += day_rating_sum

        points.append(
            ReviewTrendPointOut(
                date=day,
                total_reviews=day_total,
                low_rating_reviews=day_low,
                replied_reviews=day_replied,
                average_rating=(
                    round(day_rating_sum / day_total, 2) if day_total else None
                ),
            )
        )

    biggest_spike_date: str | None = None
    biggest_spike_delta = 0
    biggest_drop_date: str | None = None
    biggest_drop_delta = 0
    for previous, current in zip(points, points[1:], strict=False):
        delta = current.low_rating_reviews - previous.low_rating_reviews
        if delta > biggest_spike_delta:
            biggest_spike_delta = delta
            biggest_spike_date = current.date
        if delta < biggest_drop_delta:
            biggest_drop_delta = delta
            biggest_drop_date = current.date

    return ReviewTrendOut(
        days=days,
        low_rating_max=low_rating_max,
        territory=territory,
        partial=partial,
        points=points,
        summary=ReviewTrendSummaryOut(
            total_reviews=total_reviews,
            low_rating_reviews=low_rating_reviews,
            replied_reviews=replied_reviews,
            average_rating=(
                round(rating_sum / total_reviews, 2) if total_reviews else None
            ),
            low_rating_share_pct=(
                round((low_rating_reviews / total_reviews) * 100, 1)
                if total_reviews
                else 0.0
            ),
            response_rate_pct=(
                round((replied_reviews / total_reviews) * 100, 1)
                if total_reviews
                else 0.0
            ),
            latest_total_reviews=points[-1].total_reviews if points else 0,
            latest_low_rating_reviews=points[-1].low_rating_reviews if points else 0,
            biggest_spike_date=biggest_spike_date,
            biggest_spike_delta=biggest_spike_delta,
            biggest_drop_date=biggest_drop_date,
            biggest_drop_delta=biggest_drop_delta,
        ),
    )
