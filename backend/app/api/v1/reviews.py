"""Customer Reviews + Responses API endpoints."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._deps import _get_asc_client_for_app, _get_verified_app
from app.core.config import settings
from app.core.security import get_current_user
from app.db.session import get_session
from app.schemas.review import (
    DraftIn,
    DraftOut,
    ReplyIn,
    ReviewListOut,
    ReviewOut,
    ReviewResponseOut,
    TranslateReviewIn,
    TranslateReviewOut,
)
from app.services.asc.errors import ASCAPIError
from app.services.asc.reviews import ASCReviewService
from app.services.metadata.translate import (
    AnthropicTranslator,
    translate_with_cache,
)
from app.services.reviews.draft import draft_reply
from app.services.reviews.templates import classify_review_theme

logger = logging.getLogger(__name__)
router = APIRouter()


@contextmanager
def _asc_to_502(action: str) -> Iterator[None]:
    """Translate any ASCAPIError raised in the block into a 502 HTTPException."""
    try:
        yield
    except ASCAPIError as exc:
        logger.warning("ASC %s failed: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="ASC API error",
        ) from exc


# ASC returns alpha-3 territory codes. Map common ones to a default reply
# locale; everything else falls back to en-US.
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


def _serialize_review(raw: dict[str, Any], included: list[dict[str, Any]] | None = None) -> ReviewOut:
    """Convert ASC JSON:API review payload + included responses → ReviewOut."""
    attrs = raw.get("attributes") or {}
    rating = int(attrs.get("rating") or 0)
    response: ReviewResponseOut | None = None

    rel = (raw.get("relationships") or {}).get("response", {}).get("data")
    if rel and included:
        for inc in included:
            if inc.get("type") == "customerReviewResponses" and inc.get("id") == rel.get("id"):
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
    next_link = (payload.get("links") or {}).get("next")
    if not next_link or "cursor=" not in next_link:
        return None
    # Apple's next link is a fully-qualified URL; we just want the cursor token.
    try:
        return next_link.split("cursor=", 1)[1].split("&", 1)[0]
    except IndexError:
        return None


# ----------------------------------------------------------------------
# List + detail
# ----------------------------------------------------------------------


@router.get("/{app_id}/reviews", response_model=ReviewListOut)
async def list_reviews(
    app_id: int,
    territory: str | None = Query(default=None, description="Alpha-3 ISO code, e.g. USA"),
    rating: int | None = Query(default=None, ge=1, le=5),
    has_response: bool | None = Query(
        default=None,
        description="If set, filter to reviews with/without a developer response.",
    ),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewListOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"list reviews for app {app_id}"):
            payload = await svc.list_reviews(
                app.asc_app_id,
                territory=territory,
                rating=rating,
                cursor=cursor,
                limit=limit,
            )

    items_raw = payload.get("data") or []
    included = payload.get("included") or []
    items = [_serialize_review(r, included) for r in items_raw]

    if has_response is True:
        items = [r for r in items if r.response is not None]
    elif has_response is False:
        items = [r for r in items if r.response is None]

    return ReviewListOut(items=items, next_cursor=_extract_cursor(payload))


@router.get("/{app_id}/reviews/{review_id}", response_model=ReviewOut)
async def get_review(
    app_id: int,
    review_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"get review {review_id}"):
            payload = await svc.get_review(review_id)

    raw = payload.get("data") or {}
    included = payload.get("included") or []
    return _serialize_review(raw, included)


# ----------------------------------------------------------------------
# AI draft + translate (no ASC writes)
# ----------------------------------------------------------------------


@router.post("/{app_id}/reviews/{review_id}/draft", response_model=DraftOut)
async def draft_review_reply(
    app_id: int,
    review_id: str,
    body: DraftIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DraftOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI drafting not configured. Set ANTHROPIC_API_KEY.",
        )

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"get review {review_id} for draft"):
            payload = await svc.get_review(review_id)

    review = _serialize_review(payload.get("data") or {})
    if not review.body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Review has no body to reply to.",
        )

    locale = _territory_to_locale(review.territory)
    theme = body.theme or review.theme
    try:
        suggestion = await draft_reply(
            api_key=settings.ANTHROPIC_API_KEY,
            review_body=review.body,
            review_rating=review.rating,
            target_locale=locale,
            tone=body.tone,
            theme=theme,
        )
    except Exception as exc:  # noqa: BLE001 — anthropic SDK raises diverse types
        logger.warning(
            "Anthropic draft failed for app %s review %s: %s",
            app_id, review_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI drafting service unavailable",
        ) from exc
    return DraftOut(suggestion=suggestion, locale=locale, theme=theme)


@router.post(
    "/{app_id}/reviews/{review_id}/translate",
    response_model=TranslateReviewOut,
)
async def translate_review(
    app_id: int,
    review_id: str,
    body: TranslateReviewIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TranslateReviewOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI translation not configured. Set ANTHROPIC_API_KEY.",
        )

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"get review {review_id} for translate"):
            payload = await svc.get_review(review_id)

    review = _serialize_review(payload.get("data") or {})
    if not review.body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Review has no body to translate.",
        )

    source_locale = _territory_to_locale(review.territory)
    if source_locale == body.target_locale:
        # Trivial passthrough — no API call, no DB write.
        return TranslateReviewOut(translation=review.body, cached=True)

    translator = AnthropicTranslator(api_key=settings.ANTHROPIC_API_KEY)
    try:
        translation, cached = await translate_with_cache(
            translator=translator,
            session=session,
            app_id=app_id,
            text=review.body,
            source_locale=source_locale,
            target_locale=body.target_locale,
            # Treat as long-form free text. Reuses the 4000-char limit bucket.
            field_kind="description",  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001 — anthropic SDK raises diverse types
        await session.rollback()
        logger.warning(
            "Anthropic translate failed for app %s review %s: %s",
            app_id, review_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI translation service unavailable",
        ) from exc
    await session.commit()
    return TranslateReviewOut(translation=translation, cached=cached)


# ----------------------------------------------------------------------
# Reply CRUD against ASC
# ----------------------------------------------------------------------


@router.post(
    "/{app_id}/reviews/{review_id}/respond",
    response_model=ReviewResponseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_reply(
    app_id: int,
    review_id: str,
    body: ReplyIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponseOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"create reply for review {review_id}"):
            data = await svc.create_response(review_id, body.body)

    attrs = data.get("attributes") or {}
    return ReviewResponseOut(
        id=data.get("id", ""),
        body=attrs.get("responseBody") or body.body,
        last_modified_date=attrs.get("lastModifiedDate"),
        state=attrs.get("state"),
    )


@router.patch(
    "/{app_id}/reviews/{review_id}/respond/{response_id}",
    response_model=ReviewResponseOut,
)
async def update_reply(
    app_id: int,
    review_id: str,  # noqa: ARG001 — kept for URL symmetry / ownership scoping
    response_id: str,
    body: ReplyIn,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponseOut:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"update reply {response_id}"):
            data = await svc.update_response(response_id, body.body)

    attrs = data.get("attributes") or {}
    return ReviewResponseOut(
        id=data.get("id", response_id),
        body=attrs.get("responseBody") or body.body,
        last_modified_date=attrs.get("lastModifiedDate"),
        state=attrs.get("state"),
    )


@router.delete(
    "/{app_id}/reviews/{review_id}/respond/{response_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_reply(
    app_id: int,
    review_id: str,  # noqa: ARG001
    response_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id = int(current_user["user_id"])
    app = await _get_verified_app(app_id, user_id, session)

    async with await _get_asc_client_for_app(app, session) as client:
        svc = ASCReviewService(client)
        with _asc_to_502(f"delete reply {response_id}"):
            await svc.delete_response(response_id)
