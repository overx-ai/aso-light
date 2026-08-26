"""MCP tools for customer reviews + developer responses.

Mirrors ``app/api/v1/reviews.py``: list/get reviews, AI-draft a reply,
translate a review body, and full reply CRUD against ASC.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastmcp.exceptions import ToolError

from app.core.config import settings
from app.mcp.context import resolve_app, resolve_asc_client, session_scope
from app.mcp.server import mcp
from app.schemas.review import (
    DraftOut,
    ReplyTone,
    ReviewTheme,
    ReviewListOut,
    ReviewOut,
    ReviewResponseOut,
    TranslateReviewOut,
)
from app.services.asc.errors import ASCAPIError, ChildResourceNotFoundError
from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN, ASCReviewService
from app.services.metadata.translate import (
    build_translator,
    translate_with_cache,
)
from app.services.reviews.draft import draft_reply
from app.services.reviews.ownership import (
    assert_response_belongs_to_app,
    assert_review_belongs_to_app,
    record_response_mapping,
    record_review_app_mappings,
)
from app.services.reviews.templates import classify_review_theme

logger = logging.getLogger(__name__)


# ASC returns alpha-3 territory codes; map to a sensible reply locale.
_TERRITORY_TO_LOCALE: dict[str, str] = {
    "USA": "en-US", "GBR": "en-GB", "AUS": "en-AU", "CAN": "en-CA",
    "NZL": "en-NZ", "IRL": "en-IE",
    "DEU": "de-DE", "AUT": "de-DE", "CHE": "de-DE",
    "FRA": "fr-FR", "BEL": "fr-FR", "LUX": "fr-FR",
    "ESP": "es-ES", "MEX": "es-MX", "ARG": "es-MX", "CHL": "es-MX",
    "COL": "es-MX", "PER": "es-MX",
    "ITA": "it-IT",
    "JPN": "ja-JP",
    "KOR": "ko-KR",
    "CHN": "zh-Hans", "TWN": "zh-Hant", "HKG": "zh-Hant",
    "RUS": "ru-RU",
    "BRA": "pt-BR", "PRT": "pt-PT",
    "NLD": "nl-NL",
    "POL": "pl-PL",
    "TUR": "tr-TR",
    "SWE": "sv-SE", "NOR": "no-NO", "DNK": "da-DK", "FIN": "fi-FI",
    "IDN": "id-ID", "MYS": "ms-MY", "THA": "th-TH", "VNM": "vi-VN",
    "ARE": "ar-SA", "SAU": "ar-SA",
    "ISR": "he-IL",
    "IND": "hi-IN",
    "GRC": "el-GR",
    "CZE": "cs-CZ", "SVK": "sk-SK", "HUN": "hu-HU", "ROU": "ro-RO",
    "UKR": "uk-UA", "BGR": "bg-BG", "HRV": "hr-HR",
}


def _territory_to_locale(territory: str | None) -> str:
    if not territory:
        return "en-US"
    return _TERRITORY_TO_LOCALE.get(territory.upper(), "en-US")


def _serialize_review(
    raw: dict[str, Any], included: list[dict[str, Any]] | None = None,
) -> ReviewOut:
    """Convert ASC JSON:API review payload (+ included responses) → ReviewOut."""
    attrs = raw.get("attributes") or {}
    rating = int(attrs.get("rating") or 0)
    response: ReviewResponseOut | None = None

    rel = (raw.get("relationships") or {}).get("response", {}).get("data")
    if rel and included:
        for inc in included:
            if (
                inc.get("type") == "customerReviewResponses"
                and inc.get("id") == rel.get("id")
            ):
                inc_attrs = inc.get("attributes") or {}
                response = ReviewResponseOut(
                    id=inc.get("id", ""),
                    body=inc_attrs.get("responseBody") or "",
                    last_modified_date=inc_attrs.get("lastModifiedDate"),
                    state=inc_attrs.get("state"),
                )
                break

    return ReviewOut(
        id=raw.get("id", ""),
        rating=rating,
        title=attrs.get("title"),
        body=attrs.get("body"),
        territory=attrs.get("territory"),
        theme=classify_review_theme(
            title=attrs.get("title"),
            body=attrs.get("body"),
            rating=rating,
        ),
        reviewer_nickname=attrs.get("reviewerNickname"),
        created_date=attrs.get("createdDate"),
        response=response,
    )


def _extract_cursor(payload: dict[str, Any]) -> str | None:
    """Pull the decoded ``cursor`` value out of Apple's pagination ``next`` link.

    Mirrors ``app.api.v1.reviews._extract_cursor`` — see that docstring for
    why ``parse_qs`` (not a hand-rolled split) is required here.
    """
    next_link = (payload.get("links") or {}).get("next")
    if not next_link:
        return None
    values = parse_qs(urlsplit(next_link).query).get("cursor")
    return values[0] if values else None


def _wrap_asc(action: str, exc: ASCAPIError) -> ToolError:
    logger.warning("ASC %s failed: %s", action, exc)
    return ToolError(f"ASC API error: {action}")


async def _assert_review_owned(session, review_id: str, app_id: int) -> None:
    """Cross-app IDOR guard (bug 001) — see app.services.reviews.ownership.

    Runs before any ASC client is built: a ToolError from our own DB map
    shouldn't pay for a credential decrypt + client construction it
    doesn't need.
    """
    try:
        await assert_review_belongs_to_app(session, review_id, app_id)
    except ChildResourceNotFoundError as exc:
        raise ToolError(str(exc)) from exc


async def _assert_response_owned(session, response_id: str, app_id: int) -> str:
    """Cross-app IDOR guard for response_id (bug 001). Returns the real review_id."""
    try:
        return await assert_response_belongs_to_app(session, response_id, app_id)
    except ChildResourceNotFoundError as exc:
        raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------


@mcp.tool(name="reviews_list")
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
        # Bug 001: the only app-scoped ASC read — record review_id -> app_id
        # (+ response_id -> review_id) so every other entry point below can
        # verify ownership of a bare id it's handed.
        await record_review_app_mappings(session, app.id, items_raw)

    included = payload.get("included") or []
    items = [_serialize_review(r, included) for r in items_raw]

    if has_response is True:
        items = [r for r in items if r.response is not None]
    elif has_response is False:
        items = [r for r in items if r.response is None]

    return ReviewListOut(items=items, next_cursor=_extract_cursor(payload))


@mcp.tool(name="reviews_get")
async def get_review(app_id: int, review_id: str) -> ReviewOut:
    """Fetch a single review with its response (if any)."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _assert_review_owned(session, review_id, app.id)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"get review {review_id}", exc) from exc

        raw = payload.get("data") or {}
        await record_review_app_mappings(session, app.id, [raw] if raw else [])

    included = payload.get("included") or []
    return _serialize_review(raw, included)


# ---------------------------------------------------------------------------
# AI draft + translate (no ASC writes)
# ---------------------------------------------------------------------------


@mcp.tool(name="reviews_draft_reply")
async def draft_review_reply(
    app_id: int,
    review_id: str,
    tone: ReplyTone = "neutral",
    theme: ReviewTheme | None = None,
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
        await _assert_review_owned(session, review_id, app.id)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"get review {review_id} for draft", exc) from exc

        raw = payload.get("data") or {}
        await record_review_app_mappings(session, app.id, [raw] if raw else [])

    review = _serialize_review(raw)
    if not review.body:
        raise ToolError("Review has no body to reply to.")

    locale = _territory_to_locale(review.territory)
    selected_theme = theme or review.theme
    try:
        suggestion = await draft_reply(
            api_key=settings.ANTHROPIC_API_KEY,
            review_body=review.body,
            review_rating=review.rating,
            target_locale=locale,
            tone=tone,
            theme=selected_theme,
        )
    except Exception as exc:  # noqa: BLE001 — anthropic SDK raises diverse types
        logger.warning(
            "Anthropic draft failed for app %s review %s: %s",
            app_id, review_id, exc,
        )
        raise ToolError("AI drafting service unavailable") from exc
    return DraftOut(suggestion=suggestion, locale=locale, theme=selected_theme)


@mcp.tool(name="reviews_translate")
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
    translator = build_translator(settings)
    if translator is None:
        raise ToolError(
            "AI translation not configured. "
            "Set OPENROUTER_API_KEY or ANTHROPIC_API_KEY."
        )

    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _assert_review_owned(session, review_id, app.id)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                payload = await svc.get_review(review_id)
            except ASCAPIError as exc:
                raise _wrap_asc(
                    f"get review {review_id} for translate", exc,
                ) from exc

        raw = payload.get("data") or {}
        await record_review_app_mappings(session, app.id, [raw] if raw else [])

        review = _serialize_review(raw)
        if not review.body:
            raise ToolError("Review has no body to translate.")

        source_locale = _territory_to_locale(review.territory)
        if source_locale == target_locale:
            return TranslateReviewOut(translation=review.body, cached=True)

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


@mcp.tool(name="reviews_respond")
async def create_reply(
    app_id: int,
    review_id: str,
    body: str,
) -> ReviewResponseOut:
    """Post a developer response to a review."""
    _validate_reply_body(body)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _assert_review_owned(session, review_id, app.id)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                data = await svc.create_response(review_id, body)
            except ASCAPIError as exc:
                raise _wrap_asc(
                    f"create reply for review {review_id}", exc,
                ) from exc

        response_id = data.get("id", "")
        if response_id:
            await record_response_mapping(session, response_id, review_id)

    attrs = data.get("attributes") or {}
    return ReviewResponseOut(
        id=response_id,
        body=attrs.get("responseBody") or body,
        last_modified_date=attrs.get("lastModifiedDate"),
        state=attrs.get("state"),
    )


@mcp.tool(name="reviews_update_response")
async def update_reply(
    app_id: int,
    response_id: str,
    body: str,
) -> ReviewResponseOut:
    """Edit an existing developer response."""
    _validate_reply_body(body)
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _assert_response_owned(session, response_id, app.id)
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


@mcp.tool(name="reviews_delete_response")
async def delete_reply(
    app_id: int,
    response_id: str,
) -> dict[str, str]:
    """Delete a developer response."""
    async with session_scope() as session:
        app = await resolve_app(app_id, session)
        await _assert_response_owned(session, response_id, app.id)
        client = await resolve_asc_client(app, session)
        async with client:
            svc = ASCReviewService(client)
            try:
                await svc.delete_response(response_id)
            except ASCAPIError as exc:
                raise _wrap_asc(f"delete reply {response_id}", exc) from exc
    return {"detail": "Response deleted"}
