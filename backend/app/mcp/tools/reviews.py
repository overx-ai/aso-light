"""MCP tools for customer reviews + developer responses.

Mirrors ``app/api/v1/reviews.py``: list/get reviews, AI-draft a reply,
translate a review body, and full reply CRUD against ASC.
"""

from __future__ import annotations

import logging

from fastmcp.exceptions import ToolError

from app.core.config import settings
from app.mcp.context import resolve_app, resolve_asc_client, session_scope
from app.mcp.server import mcp
from app.schemas.review import (
    DraftOut,
    ReplyTone,
    ReviewListOut,
    ReviewOut,
    ReviewResponseOut,
    TranslateReviewOut,
)
from app.services.asc.errors import ASCAPIError
from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN, ASCReviewService
from app.services.metadata.translate import (
    AnthropicTranslator,
    translate_with_cache,
)
from app.services.reviews.common import (
    extract_cursor,
    serialize_review,
    territory_to_locale,
)
from app.services.reviews.draft import (
    default_tone_for_theme,
    draft_reply,
    reply_template_for_theme,
)

logger = logging.getLogger(__name__)


def _wrap_asc(action: str, exc: ASCAPIError) -> ToolError:
    logger.warning("ASC %s failed: %s", action, exc)
    return ToolError(f"ASC API error: {action}")


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------


@mcp.tool(name="reviews.list")
async def list_reviews(
    app_id: int,
    territory: str | None = None,
    rating: int | None = None,
    has_response: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ReviewListOut:
    """List customer reviews for an app, paginated.

    ``territory`` is an alpha-3 ISO code (e.g. ``USA``), ``rating`` filters
    1-5, and ``has_response`` filters the page in memory after fetching
    (Apple's API has no native filter).
    """
    if rating is not None and not 1 <= rating <= 5:
        raise ToolError("rating must be between 1 and 5")
    if not 1 <= limit <= 200:
        raise ToolError("limit must be between 1 and 200")

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.list_reviews(
                    app.asc_app_id,
                    territory=territory,
                    rating=rating,
                    cursor=cursor,
                    limit=limit,
                )
            except ASCAPIError as exc:
                raise _wrap_asc(f"list reviews for app {app_id}", exc) from exc

    items_raw = payload.get("data") or []
    included = payload.get("included") or []
    items = [serialize_review(r, included) for r in items_raw]

    if has_response is True:
        items = [r for r in items if r.response is not None]
    elif has_response is False:
        items = [r for r in items if r.response is None]

    return ReviewListOut(items=items, next_cursor=extract_cursor(payload))


@mcp.tool(name="reviews.get")
async def get_review(app_id: int, review_id: str) -> ReviewOut:
    """Fetch a single review with its response (if any)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"get review {review_id}", exc) from exc

    raw = payload.get("data") or {}
    included = payload.get("included") or []
    return serialize_review(raw, included)


# ---------------------------------------------------------------------------
# AI draft + translate (no ASC writes)
# ---------------------------------------------------------------------------


@mcp.tool(name="reviews.draft_reply")
async def draft_review_reply(
    app_id: int,
    review_id: str,
    tone: ReplyTone | None = None,
) -> DraftOut:
    """Generate a suggested reply to a review using Claude.

    Returns the suggestion + the locale it was drafted in (derived from the
    review's territory). Suggestion only — caller must explicitly post via
    ``reviews.respond``.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ToolError("AI drafting not configured. Set ANTHROPIC_API_KEY.")

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"get review {review_id} for draft", exc) from exc

    review = serialize_review(payload.get("data") or {})
    if not review.body:
        raise ToolError("Review has no body to reply to.")

    locale = territory_to_locale(review.territory)
    resolved_tone = tone or default_tone_for_theme(review.theme)
    try:
        suggestion = await draft_reply(
            api_key=settings.ANTHROPIC_API_KEY,
            review_title=review.title,
            review_body=review.body,
            review_rating=review.rating,
            target_locale=locale,
            tone=resolved_tone,
            theme=review.theme,
        )
    except Exception as exc:  # noqa: BLE001 — anthropic SDK raises diverse types
        logger.warning(
            "Anthropic draft failed for app %s review %s: %s",
            app_id, review_id, exc,
        )
        raise ToolError("AI drafting service unavailable") from exc
    return DraftOut(
        suggestion=suggestion,
        locale=locale,
        theme=review.theme,
        tone=resolved_tone,
        reply_template=reply_template_for_theme(review.theme),
    )


@mcp.tool(name="reviews.translate")
async def translate_review(
    app_id: int,
    review_id: str,
    target_locale: str,
) -> TranslateReviewOut:
    """Translate a review's body into ``target_locale`` using Claude.

    Cached on a (app_id, source, target, text) key. Returns
    ``cached=True`` on cache hit.
    """
    if not target_locale or len(target_locale) < 2:
        raise ToolError("target_locale must be a non-empty locale code")
    if not settings.ANTHROPIC_API_KEY:
        raise ToolError("AI translation not configured. Set ANTHROPIC_API_KEY.")

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(
                    f"get review {review_id} for translate", exc,
                ) from exc

        review = serialize_review(payload.get("data") or {})
        if not review.body:
            raise ToolError("Review has no body to translate.")

        source_locale = territory_to_locale(review.territory)
        if source_locale == target_locale:
            return TranslateReviewOut(translation=review.body, cached=True)

        translator = AnthropicTranslator(api_key=settings.ANTHROPIC_API_KEY)
        try:
            translation, cached = await translate_with_cache(
                translator=translator,
                session=session,
                app_id=app_id,
                text=review.body,
                source_locale=source_locale,
                target_locale=target_locale,
                # Long-form free text — share the 4000-char description bucket.
                field_kind="description",  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Anthropic translate failed for app %s review %s: %s",
                app_id, review_id, exc,
            )
            raise ToolError("AI translation service unavailable") from exc

        return TranslateReviewOut(translation=translation, cached=cached)


# ---------------------------------------------------------------------------
# Reply CRUD (writes to ASC)
# ---------------------------------------------------------------------------


def _validate_reply_body(body: str) -> None:
    if not body:
        raise ToolError("body cannot be empty")
    if len(body) > RESPONSE_BODY_MAX_LEN:
        raise ToolError(f"body exceeds {RESPONSE_BODY_MAX_LEN}-character limit")


@mcp.tool(name="reviews.respond")
async def create_reply(
    app_id: int,
    review_id: str,
    body: str,
) -> ReviewResponseOut:
    """Post a developer response to a review."""
    _validate_reply_body(body)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                data = await svc.create_response(review_id, body)
            except ASCAPIError as exc:
                raise _wrap_asc(
                    f"create reply for review {review_id}", exc,
                ) from exc

    attrs = data.get("attributes") or {}
    return ReviewResponseOut(
        id=data.get("id", ""),
        body=attrs.get("responseBody") or body,
        last_modified_date=attrs.get("lastModifiedDate"),
        state=attrs.get("state"),
    )


@mcp.tool(name="reviews.update_response")
async def update_reply(
    app_id: int,
    response_id: str,
    body: str,
) -> ReviewResponseOut:
    """Edit an existing developer response."""
    _validate_reply_body(body)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                data = await svc.update_response(response_id, body)
            except ASCAPIError as exc:
                raise _wrap_asc(f"update reply {response_id}", exc) from exc

    attrs = data.get("attributes") or {}
    return ReviewResponseOut(
        id=data.get("id", response_id),
        body=attrs.get("responseBody") or body,
        last_modified_date=attrs.get("lastModifiedDate"),
        state=attrs.get("state"),
    )


@mcp.tool(name="reviews.delete_response")
async def delete_reply(
    app_id: int,
    response_id: str,
) -> dict[str, str]:
    """Delete a developer response."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                await svc.delete_response(response_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"delete reply {response_id}", exc) from exc
    return {"detail": "Response deleted"}
