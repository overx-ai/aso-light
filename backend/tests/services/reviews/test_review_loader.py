from __future__ import annotations

from datetime import date

import pytest

from app.api.v1.reviews import _load_reviews_for_trend


def _review_payload(review_id: str, created_date: str) -> dict:
    return {
        "id": review_id,
        "type": "customerReviews",
        "attributes": {
            "rating": 1,
            "title": None,
            "body": None,
            "reviewerNickname": None,
            "createdDate": created_date,
            "territory": "USA",
        },
    }


def _page(
    review_id: str,
    created_date: str,
    *,
    next_cursor: str | None = None,
) -> dict:
    payload = {
        "data": [_review_payload(review_id, created_date)],
        "included": [],
        "links": {},
    }
    if next_cursor:
        payload["links"]["next"] = (
            f"https://api.appstoreconnect.apple.com/v1/customerReviews?cursor={next_cursor}"
        )
    return payload


class FakeReviewService:
    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.calls: list[dict[str, str | int | None]] = []

    async def list_reviews(
        self,
        asc_app_id: str,
        *,
        territory: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        self.calls.append(
            {
                "asc_app_id": asc_app_id,
                "territory": territory,
                "cursor": cursor,
                "limit": limit,
            }
        )
        return self._pages[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_load_reviews_for_trend_keeps_loading_past_old_page_cap() -> None:
    pages = [
        _page(
            f"recent-{index}",
            "2026-05-12T12:00:00Z",
            next_cursor=f"page-{index + 1}",
        )
        for index in range(11)
    ]
    pages.append(_page("older-than-window", "2026-05-09T12:00:00Z"))
    svc = FakeReviewService(pages)

    reviews, partial = await _load_reviews_for_trend(
        svc,
        "asc-app-123",
        territory=None,
        cutoff_day=date(2026, 5, 10),
    )

    assert partial is False
    assert len(reviews) == 11
    assert len(svc.calls) == 12
    assert all(call["limit"] == 200 for call in svc.calls)
    assert svc.calls[-1]["cursor"] == "page-11"


@pytest.mark.asyncio
async def test_load_reviews_for_trend_marks_partial_on_cursor_loop() -> None:
    svc = FakeReviewService(
        [
            _page("recent-1", "2026-05-12T12:00:00Z", next_cursor="loop"),
            _page("recent-2", "2026-05-11T12:00:00Z", next_cursor="loop"),
        ]
    )

    reviews, partial = await _load_reviews_for_trend(
        svc,
        "asc-app-123",
        territory=None,
        cutoff_day=date(2026, 5, 10),
    )

    assert partial is True
    assert [review.id for review in reviews] == ["recent-1", "recent-2"]
    assert len(svc.calls) == 2
