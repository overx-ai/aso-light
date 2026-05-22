"""Tests for theme-based review reply templates."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.reviews.draft import draft_reply
from app.services.reviews.templates import select_reply_template


def test_bug_reports_use_bug_template_with_apologetic_tone() -> None:
    template = select_reply_template(
        review_body="The app crashes every time I try to save a workout.",
        review_rating=1,
    )

    assert template.theme == "bug_report"
    assert template.default_tone == "apologetic"
    assert "acknowledge the bug" in template.guidance.lower()


def test_feature_requests_use_feature_request_template() -> None:
    template = select_reply_template(
        review_body="Please add a dark mode and iPad layout.",
        review_rating=4,
    )

    assert template.theme == "feature_request"
    assert template.default_tone == "appreciative"
    assert "share that the feedback will be reviewed" in template.guidance.lower()


def test_billing_reviews_use_billing_template() -> None:
    template = select_reply_template(
        review_body="I was charged after the free trial and need help with my subscription.",
        review_rating=1,
    )

    assert template.theme == "billing"
    assert template.default_tone == "apologetic"
    assert "billing or subscription concern" in template.guidance.lower()


def test_positive_reviews_use_praise_template() -> None:
    template = select_reply_template(
        review_body="Love this app. It has been amazing for my daily routine.",
        review_rating=5,
    )

    assert template.theme == "praise"
    assert template.default_tone == "appreciative"
    assert "thank the reviewer" in template.guidance.lower()


def test_low_rating_without_specific_keywords_uses_complaint_template() -> None:
    template = select_reply_template(
        review_body="Really disappointed with the latest update.",
        review_rating=2,
    )

    assert template.theme == "complaint"
    assert template.default_tone == "apologetic"
    assert "acknowledge the frustration" in template.guidance.lower()


@pytest.mark.parametrize(
    ("review_body", "review_rating", "expected_theme"),
    [
        ("No issues so far, works great", 5, "praise"),
        ("Badge support would be nice", 3, "feature_request"),
        ("The recharge flow is confusing", 2, "complaint"),
    ],
)
def test_template_selection_avoids_false_positive_substring_matches(
    review_body: str,
    review_rating: int,
    expected_theme: str,
) -> None:
    template = select_reply_template(
        review_body=review_body,
        review_rating=review_rating,
    )

    assert template.theme == expected_theme


@pytest.mark.asyncio
async def test_draft_reply_uses_theme_template_when_tone_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeMessages:
        async def create(self, **kwargs: str) -> SimpleNamespace:
            captured["system"] = kwargs["system"]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Draft reply")],
            )

    class FakeAnthropic:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr("app.services.reviews.draft.AsyncAnthropic", FakeAnthropic)

    result = await draft_reply(
        api_key="test-key",
        review_body="The app crashes every time I try to save a workout.",
        review_rating=1,
        target_locale="en-US",
    )

    assert result == "Draft reply"
    assert "acknowledge the bug" in captured["system"].lower()
    assert "tone: sincerely apologetic" in captured["system"].lower()


@pytest.mark.asyncio
async def test_draft_reply_honors_manual_tone_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeMessages:
        async def create(self, **kwargs: str) -> SimpleNamespace:
            captured["system"] = kwargs["system"]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Draft reply")],
            )

    class FakeAnthropic:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr("app.services.reviews.draft.AsyncAnthropic", FakeAnthropic)

    await draft_reply(
        api_key="test-key",
        review_body="The app crashes every time I try to save a workout.",
        review_rating=1,
        target_locale="en-US",
        tone="neutral",
    )

    assert "acknowledge the bug" in captured["system"].lower()
    assert "tone: professional, helpful, even-handed." in captured["system"].lower()
