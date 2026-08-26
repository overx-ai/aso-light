"""Cross-app IDOR guard for reviews (bug 001).

ASC scopes ``/v1/customerReviews/*`` and ``/v1/customerReviewResponses/*`` to
the whole Apple team, not per app — see ``app.models.review_app_map`` for
the full rationale. This module is the single place both
``app/api/v1/reviews.py`` (REST) and ``app/mcp/tools/reviews.py`` (MCP)
populate and check the ``review_id -> app_id`` / ``response_id -> review_id``
map; only the exception -> HTTP-status / ToolError translation differs
per module, matching the existing ``ChildResourceNotFoundError`` convention
used by ``app.services.asc.pricing``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_app_map import ReviewAppMap, ReviewResponseMap
from app.services.asc.errors import ChildResourceNotFoundError

# ----------------------------------------------------------------------
# Population — called from list_reviews and every other read that returns
# a review's JSON:API resource (get_review / draft / translate).
# ----------------------------------------------------------------------


async def record_review_app_mappings(
    session: AsyncSession,
    app_id: int,
    items_raw: list[dict[str, Any]],
) -> None:
    """Upsert review_id -> app_id (+ response_id -> review_id) from a page.

    ``items_raw`` is a raw ASC JSON:API ``data`` array (or a single-element
    list wrapping one review resource, as used by ``get_review``). Each
    review's ``relationships.response.data.id`` — present when the review
    has a developer response, since every call requests
    ``include=response`` — is recorded as ``response_id -> review_id`` in
    the same pass.

    Single bulk SELECT + per-row upsert, mirroring
    ``app.services.reviews.themes.upsert_classifications``: portable
    between SQLite-dev and Postgres-prod (no dialect-specific
    ``ON CONFLICT``). Caller is responsible for the surrounding transaction
    (flush only here) — REST routes rely on ``Depends(get_session)``
    committing at the request boundary; MCP tools rely on
    ``session_scope()`` committing at the tool-call boundary.
    """
    review_ids = [item["id"] for item in items_raw if item.get("id")]
    if not review_ids:
        return

    existing_res = await session.execute(
        select(ReviewAppMap).where(ReviewAppMap.review_id.in_(review_ids))
    )
    existing_by_id = {row.review_id: row for row in existing_res.scalars().all()}

    response_pairs: list[tuple[str, str]] = []
    for item in items_raw:
        review_id = item.get("id")
        if not review_id:
            continue
        row = existing_by_id.get(review_id)
        if row is None:
            session.add(ReviewAppMap(review_id=review_id, app_id=app_id))
        elif row.app_id != app_id:
            # Apple review ids are globally unique, so this should be
            # unreachable in practice — but if it ever happened, keep the
            # map fresh rather than latching onto a stale app_id.
            row.app_id = app_id

        relationships = item.get("relationships") or {}
        response_rel = (relationships.get("response") or {}).get("data") or {}
        response_id = response_rel.get("id")
        if response_id:
            response_pairs.append((response_id, review_id))

    if response_pairs:
        await _upsert_response_mappings(session, response_pairs)

    await session.flush()


async def record_review_app_mapping(
    session: AsyncSession, app_id: int, item_raw: dict[str, Any],
) -> None:
    """Record the mapping for one review resource, e.g. a ``get_review`` payload.

    Thin singular wrapper over :func:`record_review_app_mappings` — the
    defensive population done by ``get_review`` / ``draft`` / ``translate``
    always has exactly one (possibly empty) resource in hand. An empty
    payload records nothing, exactly as an empty page does.
    """
    await record_review_app_mappings(session, app_id, [item_raw])


async def record_response_mapping(
    session: AsyncSession, response_id: str, review_id: str,
) -> None:
    """Record one response_id -> review_id pair, e.g. right after create_response.

    ``list_reviews`` would not otherwise learn about a brand-new response
    until its next page fetch, so ``create_response`` records it directly.
    """
    if not response_id:
        return
    await _upsert_response_mappings(session, [(response_id, review_id)])
    await session.flush()


async def forget_response_mapping(session: AsyncSession, response_id: str) -> None:
    """Delete a response_id -> review_id row after its ASC response is deleted.

    Hygiene, not a security boundary: a stale row would still resolve to the
    correct, still-app-scoped review_id (ASC itself 404s the dead
    response_id on any further operation), but rows shouldn't survive their
    target indefinitely. Called from ``delete_reply`` (REST + MCP) after the
    ASC delete succeeds.
    """
    await session.execute(
        delete(ReviewResponseMap).where(ReviewResponseMap.response_id == response_id)
    )
    await session.flush()


async def _upsert_response_mappings(
    session: AsyncSession, pairs: list[tuple[str, str]],
) -> None:
    response_ids = [rid for rid, _ in pairs]
    existing_res = await session.execute(
        select(ReviewResponseMap).where(ReviewResponseMap.response_id.in_(response_ids))
    )
    existing_by_id = {row.response_id: row for row in existing_res.scalars().all()}
    for response_id, review_id in pairs:
        row = existing_by_id.get(response_id)
        if row is None:
            session.add(ReviewResponseMap(response_id=response_id, review_id=review_id))
        elif row.review_id != review_id:
            row.review_id = review_id


# ----------------------------------------------------------------------
# Assertion — the fail-closed IDOR guard itself.
# ----------------------------------------------------------------------


async def assert_review_belongs_to_app(
    session: AsyncSession, review_id: str, app_id: int,
) -> None:
    """Fail closed unless ``review_id`` was previously observed under ``app_id``.

    Raises :class:`ChildResourceNotFoundError` (-> 404 REST / ``ToolError``
    MCP) when the review has never been seen by ``list_reviews`` for this
    app — either because it belongs to a different app on the same Apple
    team, or because it is genuinely unknown (deliberate id-guessing, or a
    stale id predating this fix). Both cases 404 identically so existence
    is never leaked.
    """
    res = await session.execute(
        select(ReviewAppMap.app_id).where(ReviewAppMap.review_id == review_id)
    )
    mapped_app_id = res.scalar_one_or_none()
    if mapped_app_id is None or mapped_app_id != app_id:
        raise ChildResourceNotFoundError("Review not found for this app")


async def resolve_response_review_id(session: AsyncSession, response_id: str) -> str:
    """Return the review_id a response belongs to, or fail closed."""
    res = await session.execute(
        select(ReviewResponseMap.review_id).where(
            ReviewResponseMap.response_id == response_id
        )
    )
    review_id = res.scalar_one_or_none()
    if review_id is None:
        raise ChildResourceNotFoundError("Response not found for this app")
    return review_id


async def assert_response_belongs_to_app(
    session: AsyncSession,
    response_id: str,
    app_id: int,
    *,
    review_id: str | None = None,
) -> str:
    """Fail closed unless response_id resolves to a review that belongs to app_id.

    When ``review_id`` is given (the REST path param — MCP's
    ``update_response``/``delete_response`` never took one), also verifies
    the response's real owning review matches it, defending against a
    caller supplying a mismatched review_id/response_id pair. Returns the
    response's real review_id.
    """
    mapped_review_id = await resolve_response_review_id(session, response_id)
    if review_id is not None and mapped_review_id != review_id:
        raise ChildResourceNotFoundError("Response not found for this review")
    await assert_review_belongs_to_app(session, mapped_review_id, app_id)
    return mapped_review_id
