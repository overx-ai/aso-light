"""Theme classification and reply templates for customer reviews."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

ReplyTone = Literal["neutral", "apologetic", "appreciative"]
ReviewTheme = Literal[
    "bug",
    "performance",
    "feature_request",
    "billing",
    "account",
    "usability",
    "content",
    "praise",
    "other",
]


@dataclass(frozen=True)
class ReviewReplyTemplate:
    theme: ReviewTheme
    label: str
    default_tone: ReplyTone
    prompt_guidance: str


@dataclass(frozen=True)
class ThemeRule:
    theme: ReviewTheme
    keywords: tuple[str, ...]
    min_rating: int | None = None
    max_rating: int | None = None


REVIEW_REPLY_TEMPLATES: dict[ReviewTheme, ReviewReplyTemplate] = {
    "bug": ReviewReplyTemplate(
        theme="bug",
        label="Bug",
        default_tone="apologetic",
        prompt_guidance=(
            "Use a bug-report template: briefly apologize, acknowledge the "
            "reported problem, say the team will look into it, and suggest "
            "the in-app support channel for diagnostics if more detail is needed. "
            "Do not promise a fix date."
        ),
    ),
    "performance": ReviewReplyTemplate(
        theme="performance",
        label="Performance",
        default_tone="apologetic",
        prompt_guidance=(
            "Use a performance template: acknowledge the slowness, lag, battery, "
            "or stability issue, state that performance is being improved, and "
            "ask for device/app-version details through support only if needed."
        ),
    ),
    "feature_request": ReviewReplyTemplate(
        theme="feature_request",
        label="Feature request",
        default_tone="appreciative",
        prompt_guidance=(
            "Use a feature-request template: thank the reviewer for the idea, "
            "acknowledge the use case, and say the feedback will be considered "
            "or shared with the product team. Do not promise roadmap timing."
        ),
    ),
    "billing": ReviewReplyTemplate(
        theme="billing",
        label="Billing",
        default_tone="apologetic",
        prompt_guidance=(
            "Use a billing template: acknowledge the subscription, payment, "
            "trial, charge, or refund concern, avoid account-specific claims, "
            "and direct the reviewer to App Store subscription management or "
            "the in-app support channel for private help."
        ),
    ),
    "account": ReviewReplyTemplate(
        theme="account",
        label="Account",
        default_tone="apologetic",
        prompt_guidance=(
            "Use an account-support template: acknowledge login, sync, password, "
            "or missing-data trouble, avoid collecting personal data publicly, "
            "and point the reviewer to the in-app support channel for private "
            "troubleshooting."
        ),
    ),
    "usability": ReviewReplyTemplate(
        theme="usability",
        label="Usability",
        default_tone="neutral",
        prompt_guidance=(
            "Use a usability template: acknowledge that the flow or interface "
            "felt confusing, thank them for calling it out, and say the feedback "
            "helps improve the product experience."
        ),
    ),
    "content": ReviewReplyTemplate(
        theme="content",
        label="Content",
        default_tone="neutral",
        prompt_guidance=(
            "Use a content-quality template: acknowledge incorrect, missing, "
            "outdated, or localized content, say the team will review it, and "
            "ask for specifics through support if the review lacks enough detail."
        ),
    ),
    "praise": ReviewReplyTemplate(
        theme="praise",
        label="Praise",
        default_tone="appreciative",
        prompt_guidance=(
            "Use a praise template: thank the reviewer warmly, acknowledge what "
            "they liked if they mentioned it, and keep the reply brief."
        ),
    ),
    "other": ReviewReplyTemplate(
        theme="other",
        label="General",
        default_tone="neutral",
        prompt_guidance=(
            "Use a general support template: acknowledge the feedback, answer "
            "only what is supported by the review text, and keep the reply concise."
        ),
    ),
}


THEME_RULES: tuple[ThemeRule, ...] = (
    ThemeRule(
        theme="bug",
        keywords=(
            "bug",
            "broken",
            "crash",
            "crashes",
            "crashed",
            "error",
            "fails",
            "failed",
            "failure",
            "glitch",
            "doesn't work",
            "doesnt work",
            "not working",
            "won't open",
            "wont open",
            "can't open",
            "cant open",
            "stuck",
        ),
    ),
    ThemeRule(
        theme="performance",
        keywords=(
            "slow",
            "slower",
            "lag",
            "laggy",
            "freezes",
            "freezing",
            "loading",
            "battery",
            "drain",
            "sluggish",
            "performance",
        ),
    ),
    ThemeRule(
        theme="billing",
        keywords=(
            "subscription",
            "subscribe",
            "payment",
            "pay",
            "paid",
            "charge",
            "charged",
            "billing",
            "refund",
            "trial",
            "renewal",
            "expensive",
            "price",
            "pricing",
        ),
    ),
    ThemeRule(
        theme="account",
        keywords=(
            "account",
            "login",
            "log in",
            "signin",
            "sign in",
            "password",
            "sync",
            "restore",
            "lost data",
            "data lost",
        ),
    ),
    ThemeRule(
        theme="feature_request",
        keywords=(
            "feature",
            "request",
            "please add",
            "add support",
            "would like",
            "i wish",
            "wish",
            "missing",
            "needs",
            "need",
            "could you",
        ),
    ),
    ThemeRule(
        theme="usability",
        keywords=(
            "confusing",
            "hard to use",
            "difficult",
            "interface",
            "ui",
            "ux",
            "navigation",
            "can't find",
            "cant find",
            "where is",
        ),
    ),
    ThemeRule(
        theme="content",
        keywords=(
            "content",
            "wrong",
            "incorrect",
            "inaccurate",
            "outdated",
            "translation",
            "translated",
            "typo",
            "missing data",
        ),
    ),
    ThemeRule(
        theme="praise",
        keywords=(
            "love",
            "great",
            "excellent",
            "amazing",
            "awesome",
            "perfect",
            "helpful",
            "useful",
            "thanks",
            "thank you",
        ),
        min_rating=4,
    ),
)


class KeywordReviewThemeClassifier:
    """Small deterministic classifier for selecting reply templates."""

    def __init__(self, rules: Sequence[ThemeRule] = THEME_RULES) -> None:
        self.rules = tuple(rules)

    def classify(
        self,
        *,
        title: str | None,
        body: str | None,
        rating: int,
    ) -> ReviewTheme:
        text = " ".join(part.strip().lower() for part in (title, body) if part)
        if not text:
            return "praise" if rating >= 4 else "other"

        best_theme: ReviewTheme | None = None
        best_score = 0
        for rule in self.rules:
            if rule.min_rating is not None and rating < rule.min_rating:
                continue
            if rule.max_rating is not None and rating > rule.max_rating:
                continue
            score = sum(1 for keyword in rule.keywords if keyword in text)
            if score > best_score:
                best_theme = rule.theme
                best_score = score

        if best_theme is not None:
            return best_theme
        if rating >= 4:
            return "praise"
        return "other"


_CLASSIFIER = KeywordReviewThemeClassifier()


def classify_review_theme(
    *,
    title: str | None,
    body: str | None,
    rating: int,
) -> ReviewTheme:
    return _CLASSIFIER.classify(title=title, body=body, rating=rating)


def review_reply_template(theme: ReviewTheme) -> ReviewReplyTemplate:
    return REVIEW_REPLY_TEMPLATES[theme]
