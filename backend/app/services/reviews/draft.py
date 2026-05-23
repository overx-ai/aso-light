"""AI helper that drafts a customer-review reply.

The draft is SUGGESTION-ONLY — it is returned to the UI for the developer to
edit and explicitly post via the ASC create-response endpoint. We never
auto-post.

We re-use the AnthropicTranslator's underlying client + model, but use a
distinct system prompt focused on customer-support tone and Apple's content
guidelines for review replies.
"""
from __future__ import annotations

from anthropic import AsyncAnthropic

from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN
from app.services.reviews.templates import (
    ReplyTone,
    ReviewTheme,
    review_reply_template,
)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


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


def _build_system_prompt(
    tone: ReplyTone,
    target_locale: str,
    theme: ReviewTheme,
) -> str:
    base = (
        "You are a customer support specialist replying to an App Store "
        f"review on behalf of the developer. Write the reply in {target_locale}. "
        f"Hard limit: {RESPONSE_BODY_MAX_LEN} characters. Apple guidelines: "
        "no marketing, no links to third-party sites, no requests for the "
        "user to change their rating, no personal data. If the review reports "
        "a bug, suggest contacting the in-app support channel for follow-up. "
        "Output ONLY the reply text — no quotes, no preface, no signature."
    )
    template = review_reply_template(theme)
    return (
        f"{base}\n"
        f"{_TONE_GUIDANCE[tone]}\n"
        f"Classified review theme: {template.label}.\n"
        f"{template.prompt_guidance}"
    )


def _user_message(review_body: str, review_rating: int) -> str:
    return (
        f"Review (rating: {review_rating}/5):\n"
        f"{review_body.strip()}\n\n"
        "Write the reply now."
    )


async def draft_reply(
    *,
    api_key: str,
    review_body: str,
    review_rating: int,
    target_locale: str,
    tone: ReplyTone = "neutral",
    theme: ReviewTheme = "other",
    model: str = DEFAULT_MODEL,
) -> str:
    """Generate a draft reply via Claude. Returns the reply text only.

    The caller is responsible for trimming to ``RESPONSE_BODY_MAX_LEN`` if
    needed (we soft-trim here as a safety net).
    """
    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=_build_system_prompt(tone, target_locale, theme),
        messages=[
            {
                "role": "user",
                "content": _user_message(review_body, review_rating),
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
