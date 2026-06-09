"""Tests for review theme classification and draft prompt templates."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.reviews.draft import _build_system_prompt
from app.services.reviews.templates import (
    classify_review_theme,
    review_reply_template,
)


def test_classifies_bug_reviews() -> None:
    theme = classify_review_theme(
        title="Crashes on launch",
        body="The app crashes every time I open it.",
        rating=1,
    )

    assert theme == "bug"
    assert review_reply_template(theme).default_tone == "apologetic"


def test_classifies_feature_requests() -> None:
    theme = classify_review_theme(
        title="Please add widgets",
        body="I wish the app had home screen widget support.",
        rating=4,
    )

    assert theme == "feature_request"
    assert review_reply_template(theme).default_tone == "appreciative"


def test_high_rating_without_actionable_keywords_defaults_to_praise() -> None:
    assert (
        classify_review_theme(title="Nice", body="Works for me.", rating=5)
        == "praise"
    )


def test_prompt_includes_selected_theme_template() -> None:
    prompt = _build_system_prompt(
        tone="appreciative",
        target_locale="en-US",
        theme="feature_request",
    )

    assert "Classified review theme: Feature request." in prompt
    assert "Use a feature-request template" in prompt
    assert "Do not promise roadmap timing." in prompt
