"""Tests for review-theme classification and reply defaults."""
from __future__ import annotations

from app.services.reviews.common import serialize_review
from app.services.reviews.draft import classify_review_theme, default_tone_for_theme


def test_classifies_bug_reports_from_failure_language() -> None:
    assert classify_review_theme(
        review_title="App keeps crashing",
        review_body="It crashes every time I try to open the editor.",
        review_rating=1,
    ) == "bug_report"


def test_classifies_feature_requests_before_praise() -> None:
    assert classify_review_theme(
        review_title="Love it",
        review_body="Great app. Please add offline mode for flights.",
        review_rating=5,
    ) == "feature_request"


def test_classifies_billing_issues() -> None:
    assert classify_review_theme(
        review_title=None,
        review_body="I was charged after the trial and cannot restore purchase.",
        review_rating=1,
    ) == "billing_issue"


def test_classifies_support_requests() -> None:
    assert classify_review_theme(
        review_title=None,
        review_body="How do I export a report? I can't find the option.",
        review_rating=3,
    ) == "support_request"


def test_classifies_high_rating_positive_feedback_as_praise() -> None:
    assert classify_review_theme(
        review_title="Excellent",
        review_body="Love this app. It works great every day.",
        review_rating=5,
    ) == "praise"


def test_default_tone_matches_theme() -> None:
    assert default_tone_for_theme("bug_report") == "apologetic"
    assert default_tone_for_theme("feature_request") == "appreciative"
    assert default_tone_for_theme("support_request") == "neutral"


def test_serialize_review_includes_classified_theme() -> None:
    review = serialize_review(
        {
            "id": "123",
            "attributes": {
                "rating": 5,
                "title": "Thank you",
                "body": "Amazing app, thanks for building this.",
                "territory": "USA",
                "reviewerNickname": "Taylor",
                "createdDate": "2026-05-22T10:00:00Z",
            },
        }
    )

    assert review.theme == "praise"
