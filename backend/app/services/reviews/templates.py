"""Theme classification and default reply templates for app reviews."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
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
    "not working",
    "doesn't work",
    "does not work",
    "won't open",
    "cant open",
    "can't open",
)

_BUG_REGEX_PATTERNS = (
    r"\b(?:can't|cant|cannot|unable to|fails to)\s+(?:\w+\s+){0,4}anymore\b",
    r"\b(?:can't|cant|cannot|unable to|fails to)\s+(?:\w+\s+){0,4}(?:"
    r"open|load|launch|save|sync|connect|share|search|refresh|update|"
    r"upload|download|export|import|record|track|submit|login|log in|"
    r"sign in)\b",
)

_FEATURE_REQUEST_KEYWORDS = (
    "feature request",
    "please add",
    "would be nice",
    "wish it had",
    "wish you had",
    "could you add",
    "add support",
    "need a way to",
    "it would be nice",
)

_FEATURE_REQUEST_REGEX_PATTERNS = (
    r"\bwould love to see\b",
    r"\bwould love (?:a|an)\b",
    r"\bwould love it if\b",
    r"\bwish there (?:was|were)\b",
    r"\b(?:can|could) you add\b",
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

_BILLING_FEATURE_REQUEST_NOUNS = (
    "subscription",
    "purchase",
    "purchased",
)

_BILLING_CONCERN_KEYWORDS = tuple(
    keyword
    for keyword in _BILLING_KEYWORDS
    if keyword not in _BILLING_FEATURE_REQUEST_NOUNS
)

_BILLING_ISSUE_CONTEXT_KEYWORDS = (
    "confusing",
    "unclear",
    "expensive",
    "overpriced",
    "pricey",
    "issue",
    "problem",
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


@lru_cache(maxsize=None)
def _compile_phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_compile_phrase_pattern(phrase).search(text) for phrase in phrases)


@lru_cache(maxsize=None)
def _compile_regex_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(_compile_regex_pattern(pattern).search(text) for pattern in patterns)


def classify_review_theme(review_body: str | None, review_rating: int) -> ReviewTheme:
    text = _normalize_text(review_body)
    has_bug_signal = _contains_any(text, _BUG_KEYWORDS) or _matches_any_pattern(
        text, _BUG_REGEX_PATTERNS
    )
    has_feature_request_signal = _contains_any(
        text, _FEATURE_REQUEST_KEYWORDS
    ) or _matches_any_pattern(text, _FEATURE_REQUEST_REGEX_PATTERNS)
    has_billing_concern = _contains_any(text, _BILLING_CONCERN_KEYWORDS)
    has_billing_topic = _contains_any(text, _BILLING_FEATURE_REQUEST_NOUNS)
    has_praise_signal = _contains_any(text, _PRAISE_KEYWORDS)
    has_complaint_signal = _contains_any(text, _COMPLAINT_KEYWORDS)
    has_billing_issue_context = _contains_any(text, _BILLING_ISSUE_CONTEXT_KEYWORDS)

    if has_bug_signal:
        return "bug_report"
    if has_feature_request_signal and not has_billing_concern:
        return "feature_request"
    if has_billing_concern or (
        has_billing_topic
        and (
            has_billing_issue_context
            or has_complaint_signal
            or review_rating <= 2
        )
    ):
        return "billing"
    if review_rating >= 4 and (has_praise_signal or review_rating == 5):
        return "praise"
    if review_rating <= 2 or has_complaint_signal:
        return "complaint"
    if has_praise_signal:
        return "praise"
    return "other"


def get_reply_template(theme: ReviewTheme) -> ReplyTemplate:
    return _TEMPLATES[theme]


def select_reply_template(*, review_body: str | None, review_rating: int) -> ReplyTemplate:
    return get_reply_template(classify_review_theme(review_body, review_rating))
