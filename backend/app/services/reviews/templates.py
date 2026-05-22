"""Theme classification and default reply templates for app reviews."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReplyTone = Literal["neutral", "apologetic", "appreciative"]
ReviewTheme = Literal[
    "bug_report",
    "feature_request",
    "billing",
    "praise",
    "complaint",
    "other",
]


@dataclass(frozen=True)
class ReplyTemplate:
    theme: ReviewTheme
    label: str
    default_tone: ReplyTone
    guidance: str


_BUG_KEYWORDS = (
    "bug",
    "crash",
    "crashes",
    "crashed",
    "broken",
    "freeze",
    "freezes",
    "stuck",
    "error",
    "issue",
    "not working",
    "doesn't work",
    "does not work",
    "won't open",
    "cant open",
    "can't open",
    "unable to",
    "fails to",
)

_FEATURE_REQUEST_KEYWORDS = (
    "feature request",
    "please add",
    "would love",
    "wish it had",
    "wish you had",
    "could you add",
    "add support",
    "add a",
    "add an",
    "need a way to",
    "it would be nice",
)

_BILLING_KEYWORDS = (
    "refund",
    "charged",
    "charge",
    "billing",
    "subscription",
    "trial",
    "payment",
    "purchase",
    "purchased",
    "renewal",
    "renewed",
    "cancel",
    "canceled",
    "cancelled",
)

_PRAISE_KEYWORDS = (
    "love",
    "amazing",
    "great",
    "awesome",
    "excellent",
    "perfect",
    "fantastic",
    "helpful",
    "useful",
    "thank you",
    "thanks",
)

_COMPLAINT_KEYWORDS = (
    "disappointed",
    "frustrating",
    "terrible",
    "awful",
    "bad",
    "poor",
    "hate",
)

_TEMPLATES: dict[ReviewTheme, ReplyTemplate] = {
    "bug_report": ReplyTemplate(
        theme="bug_report",
        label="Bug report",
        default_tone="apologetic",
        guidance=(
            "Reply template: acknowledge the bug, apologize briefly, "
            "say the team is reviewing the issue, and point the reviewer "
            "to the in-app support channel for follow-up if needed."
        ),
    ),
    "feature_request": ReplyTemplate(
        theme="feature_request",
        label="Feature request",
        default_tone="appreciative",
        guidance=(
            "Reply template: thank the reviewer for the idea, "
            "share that the feedback will be reviewed by the team, "
            "and avoid promising a delivery date."
        ),
    ),
    "billing": ReplyTemplate(
        theme="billing",
        label="Billing issue",
        default_tone="apologetic",
        guidance=(
            "Reply template: acknowledge the billing or subscription concern, "
            "apologize for the confusion, keep the reply factual, "
            "and point the reviewer to the in-app support channel for "
            "account-specific help."
        ),
    ),
    "praise": ReplyTemplate(
        theme="praise",
        label="Praise",
        default_tone="appreciative",
        guidance=(
            "Reply template: thank the reviewer, reflect one benefit they "
            "mentioned, and keep the reply upbeat but concise."
        ),
    ),
    "complaint": ReplyTemplate(
        theme="complaint",
        label="General complaint",
        default_tone="apologetic",
        guidance=(
            "Reply template: acknowledge the frustration, apologize "
            "succinctly, say the team is reviewing the feedback, and invite "
            "the reviewer to use the in-app support channel if they want to "
            "share more detail."
        ),
    ),
    "other": ReplyTemplate(
        theme="other",
        label="General feedback",
        default_tone="neutral",
        guidance=(
            "Reply template: answer professionally and concisely, "
            "acknowledge the feedback, and keep the reply neutral."
        ),
    ),
}


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.casefold().split())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def classify_review_theme(review_body: str | None, review_rating: int) -> ReviewTheme:
    text = _normalize_text(review_body)

    if _contains_any(text, _BUG_KEYWORDS):
        return "bug_report"
    if _contains_any(text, _BILLING_KEYWORDS):
        return "billing"
    if _contains_any(text, _FEATURE_REQUEST_KEYWORDS):
        return "feature_request"
    if review_rating >= 4 and (_contains_any(text, _PRAISE_KEYWORDS) or review_rating == 5):
        return "praise"
    if review_rating <= 2 or _contains_any(text, _COMPLAINT_KEYWORDS):
        return "complaint"
    if _contains_any(text, _PRAISE_KEYWORDS):
        return "praise"
    return "other"


def get_reply_template(theme: ReviewTheme) -> ReplyTemplate:
    return _TEMPLATES[theme]


def select_reply_template(*, review_body: str | None, review_rating: int) -> ReplyTemplate:
    return get_reply_template(classify_review_theme(review_body, review_rating))
