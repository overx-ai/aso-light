"""Trend aggregation for App Store customer reviews."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re

from app.schemas.review import (
    ReviewOut,
    ReviewThemeTrend,
    ReviewThemeTrendPoint,
    ReviewTrendInsight,
    ReviewTrendOut,
    ReviewTrendPoint,
)

LOW_RATING_THRESHOLD = 2
DEFAULT_THEME_LIMIT = 5


@dataclass(frozen=True)
class ReviewThemeRule:
    label: str
    keywords: tuple[str, ...]

    def matches(self, text: str) -> bool:
        return any(
            re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text)
            for keyword in self.keywords
        )


DEFAULT_THEME_RULES: tuple[ReviewThemeRule, ...] = (
    ReviewThemeRule(
        "Stability",
        (
            "bug",
            "bugs",
            "broken",
            "crash",
            "crashed",
            "crashes",
            "crashing",
            "error",
            "freeze",
            "freezes",
            "frozen",
            "glitch",
            "stuck",
        ),
    ),
    ReviewThemeRule(
        "Performance",
        (
            "battery",
            "hang",
            "lag",
            "laggy",
            "slow",
            "slower",
            "speed",
        ),
    ),
    ReviewThemeRule(
        "Pricing",
        (
            "billing",
            "charged",
            "expensive",
            "pay",
            "payment",
            "price",
            "pricing",
            "purchase",
            "refund",
            "subscription",
        ),
    ),
    ReviewThemeRule(
        "Login",
        (
            "account",
            "log in",
            "login",
            "password",
            "sign in",
            "signin",
        ),
    ),
    ReviewThemeRule(
        "Ads",
        (
            "ad",
            "ads",
            "advertisement",
            "advertisements",
            "advertising",
        ),
    ),
    ReviewThemeRule(
        "Usability",
        (
            "confusing",
            "difficult",
            "hard to use",
            "interface",
            "layout",
            "navigation",
            "ui",
            "unusable",
        ),
    ),
    ReviewThemeRule(
        "Features",
        (
            "feature",
            "features",
            "missing",
            "option",
            "request",
            "wish",
        ),
    ),
    ReviewThemeRule(
        "Sync",
        (
            "backup",
            "cloud",
            "data",
            "lost",
            "restore",
            "sync",
            "synced",
        ),
    ),
)


class ReviewThemeClassifier:
    def __init__(self, rules: tuple[ReviewThemeRule, ...] = DEFAULT_THEME_RULES) -> None:
        self.rules = rules

    def classify(self, review: ReviewOut) -> list[str]:
        text = f"{review.title or ''} {review.body or ''}".casefold().strip()
        if not text:
            return ["Rating only"]

        matches = [rule.label for rule in self.rules if rule.matches(text)]
        return matches or ["General feedback"]


def parse_review_created_date(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class ReviewTrendAnalyzer:
    now: datetime
    low_rating_threshold: int = LOW_RATING_THRESHOLD
    theme_limit: int = DEFAULT_THEME_LIMIT
    classifier: ReviewThemeClassifier = field(default_factory=ReviewThemeClassifier)

    def build(
        self,
        reviews: list[ReviewOut],
        *,
        days: int,
        truncated: bool = False,
    ) -> ReviewTrendOut:
        today = self.now.astimezone(timezone.utc).date()
        start_day = today - timedelta(days=days - 1)
        day_range = [start_day + timedelta(days=offset) for offset in range(days)]

        buckets = {
            day: {
                "total": 0,
                "low_rating": 0,
                "rating_sum": 0,
                "rating_count": 0,
                "ratings": {rating: 0 for rating in range(1, 6)},
            }
            for day in day_range
        }
        theme_buckets: dict[str, dict[date, dict[str, int]]] = defaultdict(
            lambda: {
                day: {"count": 0, "low_rating": 0}
                for day in day_range
            },
        )

        for review in reviews:
            created = parse_review_created_date(review.created_date)
            if created is None:
                continue

            review_day = created.date()
            if review_day < start_day or review_day > today:
                continue

            rating = review.rating
            is_low_rating = 1 <= rating <= self.low_rating_threshold
            bucket = buckets[review_day]
            bucket["total"] += 1
            if is_low_rating:
                bucket["low_rating"] += 1
            if 1 <= rating <= 5:
                bucket["rating_sum"] += rating
                bucket["rating_count"] += 1
                bucket["ratings"][rating] += 1

            for theme in self.classifier.classify(review):
                theme_bucket = theme_buckets[theme][review_day]
                theme_bucket["count"] += 1
                if is_low_rating:
                    theme_bucket["low_rating"] += 1

        points = [self._make_point(day, buckets[day]) for day in day_range]
        themes = self._make_theme_trends(theme_buckets, day_range)
        insights = self._make_insights(points, themes)

        rating_sum = sum(bucket["rating_sum"] for bucket in buckets.values())
        rating_count = sum(bucket["rating_count"] for bucket in buckets.values())

        return ReviewTrendOut(
            days=days,
            start_date=start_day.isoformat(),
            end_date=today.isoformat(),
            total_reviews=sum(point.total for point in points),
            low_rating_total=sum(point.low_rating for point in points),
            average_rating=round(rating_sum / rating_count, 2) if rating_count else None,
            low_rating_threshold=self.low_rating_threshold,
            truncated=truncated,
            points=points,
            themes=themes,
            insights=insights,
        )

    def _make_point(self, day: date, bucket: dict) -> ReviewTrendPoint:
        rating_count = bucket["rating_count"]
        rating_sum = bucket["rating_sum"]
        ratings = bucket["ratings"]
        return ReviewTrendPoint(
            date=day.isoformat(),
            total=bucket["total"],
            low_rating=bucket["low_rating"],
            average_rating=round(rating_sum / rating_count, 2)
            if rating_count
            else None,
            rating_1=ratings[1],
            rating_2=ratings[2],
            rating_3=ratings[3],
            rating_4=ratings[4],
            rating_5=ratings[5],
        )

    def _make_theme_trends(
        self,
        theme_buckets: dict[str, dict[date, dict[str, int]]],
        day_range: list[date],
    ) -> list[ReviewThemeTrend]:
        trends: list[ReviewThemeTrend] = []
        for theme, buckets in theme_buckets.items():
            points = [
                ReviewThemeTrendPoint(
                    date=day.isoformat(),
                    count=buckets[day]["count"],
                    low_rating=buckets[day]["low_rating"],
                )
                for day in day_range
            ]
            total = sum(point.count for point in points)
            if total == 0:
                continue
            trends.append(
                ReviewThemeTrend(
                    theme=theme,
                    total=total,
                    low_rating=sum(point.low_rating for point in points),
                    points=points,
                ),
            )

        return sorted(
            trends,
            key=lambda trend: (-trend.low_rating, -trend.total, trend.theme),
        )[: self.theme_limit]

    def _make_insights(
        self,
        points: list[ReviewTrendPoint],
        themes: list[ReviewThemeTrend],
    ) -> list[ReviewTrendInsight]:
        low_rating_insights = self._change_insights(
            metric="Low ratings",
            values=[(point.date, point.low_rating) for point in points],
        )
        theme_candidates: list[ReviewTrendInsight] = []

        for theme in themes:
            theme_candidates.extend(
                self._change_insights(
                    metric=theme.theme,
                    values=[
                        (point.date, point.count) for point in theme.points
                    ],
                ),
            )

        theme_insights = sorted(
            theme_candidates,
            key=lambda insight: (
                -abs(insight.change),
                insight.metric in {"General feedback", "Rating only"},
                insight.metric,
                insight.date,
            ),
        )
        return (low_rating_insights + theme_insights)[:4]

    def _change_insights(
        self,
        *,
        metric: str,
        values: list[tuple[str, int]],
    ) -> list[ReviewTrendInsight]:
        spike: ReviewTrendInsight | None = None
        drop: ReviewTrendInsight | None = None

        for index in range(1, len(values)):
            date_value, value = values[index]
            _, previous_value = values[index - 1]
            change = value - previous_value
            if change > 0 and (spike is None or change > spike.change):
                spike = ReviewTrendInsight(
                    kind="spike",
                    metric=metric,
                    date=date_value,
                    previous_value=previous_value,
                    value=value,
                    change=change,
                )
            elif change < 0 and (drop is None or change < drop.change):
                drop = ReviewTrendInsight(
                    kind="drop",
                    metric=metric,
                    date=date_value,
                    previous_value=previous_value,
                    value=value,
                    change=change,
                )

        return [insight for insight in (spike, drop) if insight is not None]
