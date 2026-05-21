"""AI helper that drafts a customer-review reply.

The draft is SUGGESTION-ONLY — it is returned to the UI for the developer to
edit and explicitly post via the ASC create-response endpoint. We never
auto-post.

We re-use the AnthropicTranslator's underlying client + model, but use a
distinct system prompt focused on customer-support tone and Apple's content
guidelines for review replies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic

from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN

ReplyTone = Literal["neutral", "apologetic", "appreciative"]
ReviewTheme = Literal[
    "bug_report",
    "feature_request",
    "praise",
    "billing_issue",
    "support_request",
    "other",
]
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class ReplyTemplate:
    default_tone: ReplyTone
    guidance: str


_TONE_GUIDANCE: dict[ReplyTone, str] = {
    "neutral": (
        "Tone: professional, helpful, even-handed. "
        "Do not over-apologize or over-thank."
    ),
    "apologetic": (
        "Tone: sincerely apologetic. Acknowledge the user's frustration "
        "explicitly without being defensive. Keep it concise."
    ),
    "appreciative": (
        "Tone: warmly appreciative. Thank the user genuinely for the "
        "feedback without being effusive."
    ),
}

_THEME_KEYWORDS: dict[ReviewTheme, tuple[str, ...]] = {
    "bug_report": (
        "bug",
        "crash",
        "crashes",
        "broken",
        "not working",
        "doesn't work",
        "doesnt work",
        "won't open",
        "wont open",
        "freezes",
        "freeze",
        "stuck",
        "error",
        "glitch",
        "issue",
        "problem",
        "fails",
        "failing",
        "blank screen",
        "unable to",
        "cannot open",
        "cannot log in",
        "cannot sign in",
    ),
    "feature_request": (
        "feature request",
        "please add",
        "could you add",
        "can you add",
        "would love",
        "would like",
        "it would be great",
        "wish it had",
        "wish there was",
        "missing feature",
        "need a way",
        "add support",
        "support for",
    ),
    "praise": (
        "love",
        "great",
        "amazing",
        "awesome",
        "excellent",
        "perfect",
        "helpful",
        "useful",
        "best",
        "thank you",
        "thanks",
        "well done",
        "works great",
        "works well",
    ),
    "billing_issue": (
        "refund",
        "charged",
        "charge",
        "billing",
        "subscription",
        "purchase",
        "payment",
        "paywall",
        "trial",
        "renewal",
        "renewed",
        "restore purchase",
        "restore my purchase",
        "price",
        "expensive",
    ),
    "support_request": (
        "how do i",
        "how can i",
        "how to",
        "where do i",
        "where can i",
        "can't find",
        "cant find",
        "confusing",
        "unclear",
        "help me",
        "question",
        "is there a way",
    ),
    "other": (),
}

_THEME_TEMPLATES: dict[ReviewTheme, ReplyTemplate] = {
    "bug_report": ReplyTemplate(
        default_tone="apologetic",
        guidance=(
            "Template: 1) acknowledge the broken experience and apologize, "
            "2) state that the team is investigating or improving it, "
            "3) suggest the in-app support channel for details like device or "
            "app version if follow-up is needed. Do not sound defensive."
        ),
    ),
    "feature_request": ReplyTemplate(
        default_tone="appreciative",
        guidance=(
            "Template: 1) thank the user for the suggestion, 2) acknowledge "
            "the use case or value behind it, 3) say the idea has been noted "
            "for future consideration without promising delivery or timing."
        ),
    ),
    "praise": ReplyTemplate(
        default_tone="appreciative",
        guidance=(
            "Template: 1) thank the user warmly, 2) briefly mention that "
            "you are glad the app is helping, 3) keep it concise and upbeat."
        ),
    ),
    "billing_issue": ReplyTemplate(
        default_tone="apologetic",
        guidance=(
            "Template: 1) apologize for the frustration, 2) acknowledge the "
            "purchase or subscription concern, 3) direct the user to the "
            "in-app support channel for account-specific help. Mention App "
            "Store subscription settings only if it fits naturally."
        ),
    ),
    "support_request": ReplyTemplate(
        default_tone="neutral",
        guidance=(
            "Template: 1) acknowledge the question or confusion, 2) give a "
            "brief helpful direction, 3) point to the in-app help or support "
            "channel for step-by-step assistance if needed."
        ),
    ),
    "other": ReplyTemplate(
        default_tone="neutral",
        guidance=(
            "Template: 1) acknowledge the feedback, 2) respond helpfully and "
            "specifically, 3) close politely without adding marketing language."
        ),
    ),
}


def _normalize_review_text(*parts: str | None) -> str:
    normalized_parts = [part.strip().lower() for part in parts if part and part.strip()]
    return " ".join(normalized_parts)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def classify_review_theme(
    *,
    review_title: str | None,
    review_body: str | None,
    review_rating: int,
) -> ReviewTheme:
    text = _normalize_review_text(review_title, review_body)
    if not text:
        return "other"

    if _contains_any(text, _THEME_KEYWORDS["billing_issue"]):
        return "billing_issue"
    if _contains_any(text, _THEME_KEYWORDS["feature_request"]):
        return "feature_request"
    if _contains_any(text, _THEME_KEYWORDS["bug_report"]):
        return "bug_report"
    if _contains_any(text, _THEME_KEYWORDS["support_request"]):
        return "support_request"
    if review_rating >= 4 and _contains_any(text, _THEME_KEYWORDS["praise"]):
        return "praise"
    if review_rating <= 2 and _contains_any(
        text,
        ("bad", "terrible", "awful", "frustrating", "disappointed"),
    ):
        return "bug_report"
    return "other"


def default_tone_for_theme(theme: ReviewTheme) -> ReplyTone:
    return _THEME_TEMPLATES[theme].default_tone


def _build_system_prompt(
    tone: ReplyTone,
    target_locale: str,
    theme: ReviewTheme,
) -> str:
    template = _THEME_TEMPLATES[theme]
    base = (
        "You are a customer support specialist replying to an App Store "
        f"review on behalf of the developer. Write the reply in {target_locale}. "
        f"Hard limit: {RESPONSE_BODY_MAX_LEN} characters. Apple guidelines: "
        "no marketing, no links to third-party sites, no requests for the "
        "user to change their rating, no personal data. If the review reports "
        "a bug, suggest contacting the in-app support channel for follow-up. "
        "Output ONLY the reply text — no quotes, no preface, no signature."
    )
    return (
        f"{base}\n"
        f"Theme: {theme}.\n"
        f"{_TONE_GUIDANCE[tone]}\n"
        f"{template.guidance}"
    )


def _user_message(
    review_body: str,
    review_rating: int,
    review_title: str | None,
) -> str:
    title_block = ""
    if review_title and review_title.strip():
        title_block = f"Title: {review_title.strip()}\n"
    return (
        f"{title_block}Review (rating: {review_rating}/5):\n"
        f"{review_body.strip()}\n\n"
        "Write the reply now."
    )


async def draft_reply(
    *,
    api_key: str,
    review_title: str | None,
    review_body: str,
    review_rating: int,
    target_locale: str,
    tone: ReplyTone | None = None,
    theme: ReviewTheme | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Generate a draft reply via Claude. Returns the reply text only.

    The caller is responsible for trimming to ``RESPONSE_BODY_MAX_LEN`` if
    needed (we soft-trim here as a safety net).
    """
    resolved_theme = theme or classify_review_theme(
        review_title=review_title,
        review_body=review_body,
        review_rating=review_rating,
    )
    resolved_tone = tone or default_tone_for_theme(resolved_theme)

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=_build_system_prompt(resolved_tone, target_locale, resolved_theme),
        messages=[
            {
                "role": "user",
                "content": _user_message(review_body, review_rating, review_title),
            },
        ],
    )
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    text = "".join(parts).strip()
    if len(text) > RESPONSE_BODY_MAX_LEN:
        text = text[: RESPONSE_BODY_MAX_LEN].rstrip()
    return text
