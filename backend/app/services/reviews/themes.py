"""LLM theme classifier for App Store reviews.

Classifies a batch of reviews into one of seven themes plus a 1–5 severity
score. Uses a *forced* tool call so the model can't drift into prose: the
return shape is enforced by the JSON schema.

Themes:
* ``bug`` — broken behavior, crashes, regressions
* ``feature_request`` — explicit ask for a new capability
* ``praise`` — positive sentiment, no actionable issue
* ``pricing`` — complaints/observations about cost, subscription, IAP
* ``ux`` — confusing, slow, or unpolished UX (not a hard bug)
* ``support`` — user trying to reach the developer / asking a question
* ``other`` — fallback when none of the above fit

Severity 1–5: a rough impact estimator the priority queue uses to surface
"5★ vs 1★ bug-with-severity-5 unanswered" first.
"""
from __future__ import annotations

import logging
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_theme import ReviewThemeCache
from app.schemas.review import ReviewOut

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
THEMES = ("bug", "feature_request", "praise", "pricing", "ux", "support", "other")
# Per-call batch size. Anthropic input is generous; the bound here is more
# about predictable JSON output size than rate limits.
DEFAULT_BATCH_SIZE = 30
# Server-side cap on reviews fetched/classified in a single "auto" call (no
# explicit IDs). Shared by the REST endpoint and the MCP tool so the limit
# stays consistent without duplicating the magic number.
CLASSIFY_AUTO_LIMIT = 50

_TOOL = {
    "name": "classify_reviews",
    "description": (
        "Return one classification entry per input review, in the same order. "
        "Each entry includes the review_id verbatim, a theme, and a 1–5 severity."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "review_id": {"type": "string"},
                        "theme": {"type": "string", "enum": list(THEMES)},
                        "severity": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                        },
                    },
                    "required": ["review_id", "theme", "severity"],
                },
            },
        },
        "required": ["classifications"],
    },
}

_SYSTEM_PROMPT = (
    "You triage App Store reviews into themes for a product team. "
    "Always call the classify_reviews tool exactly once with one entry per "
    "input review. Do not write prose. Severity rubric: "
    "5 = blocks core flow / data loss / crashes for many; "
    "4 = serious bug / strong frustration; "
    "3 = noticeable issue or moderate request; "
    "2 = minor polish or nit; "
    "1 = casual praise or off-topic. "
    "If the body is empty or only emoji, infer from rating + title."
)


def _build_user_message(reviews: list[ReviewOut]) -> str:
    lines: list[str] = ["Classify these reviews:\n"]
    for r in reviews:
        body = (r.body or "").strip().replace("\n", " ")
        title = (r.title or "").strip().replace("\n", " ")
        lines.append(
            f"- review_id={r.id} rating={r.rating}/5 "
            f"title={title!r} body={body!r}"
        )
    return "\n".join(lines)


def _extract_tool_input(content: list[Any]) -> dict[str, Any]:
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise ValueError("Model returned no tool_use block")


async def classify_reviews_batch(
    *,
    api_key: str,
    reviews: list[ReviewOut],
    model: str = DEFAULT_MODEL,
) -> dict[str, dict[str, Any]]:
    """Classify one batch of reviews. Returns {review_id: {theme, severity}}.

    Reviews missing from the model's response are simply omitted from the map.
    """
    if not reviews:
        return {}
    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "classify_reviews"},
        messages=[{"role": "user", "content": _build_user_message(reviews)}],
    )
    try:
        payload = _extract_tool_input(response.content)
    except (ValueError, TypeError) as exc:
        logger.warning("Theme classifier returned no tool block: %s", exc)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for entry in payload.get("classifications", []):
        rid = entry.get("review_id")
        theme = entry.get("theme")
        severity = entry.get("severity")
        if not rid or theme not in THEMES or not isinstance(severity, int):
            continue
        out[str(rid)] = {"theme": theme, "severity": severity}
    return out


async def classify_reviews(
    *,
    api_key: str,
    reviews: list[ReviewOut],
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, dict[str, Any]]:
    """Classify any number of reviews, batching under the hood."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(reviews), batch_size):
        chunk = reviews[i : i + batch_size]
        out.update(
            await classify_reviews_batch(
                api_key=api_key, reviews=chunk, model=model
            )
        )
    return out


# ---------------------------------------------------------------------------
# Cache helpers shared by REST and MCP adapters.
# ---------------------------------------------------------------------------


async def load_theme_map(
    session: AsyncSession, app_id: int, review_ids: list[str],
) -> dict[str, tuple[str, int]]:
    """Return ``{review_id: (theme, severity)}`` for any cached rows in scope."""
    if not review_ids:
        return {}
    res = await session.execute(
        select(
            ReviewThemeCache.review_id,
            ReviewThemeCache.theme,
            ReviewThemeCache.severity,
        ).where(
            ReviewThemeCache.app_id == app_id,
            ReviewThemeCache.review_id.in_(review_ids),
        )
    )
    return {row[0]: (row[1], row[2]) for row in res.all()}


def apply_themes(
    items: list[ReviewOut], themes: dict[str, tuple[str, int]],
) -> None:
    """Mutate ``items`` in place, populating ``theme`` / ``severity`` from cache."""
    for it in items:
        hit = themes.get(it.id)
        if hit is not None:
            it.theme, it.severity = hit


async def upsert_classifications(
    session: AsyncSession,
    app_id: int,
    reviews: list[ReviewOut],
    classifications: dict[str, dict[str, Any]],
    *,
    model: str,
) -> int:
    """Upsert one ``review_theme_cache`` row per classified review.

    Pre-fetches all existing rows in a single query to avoid the N+1 pattern
    (one SELECT per review). Per-row delete-then-insert (rather than a
    dialect-specific ON CONFLICT) keeps this portable between SQLite-dev and
    Postgres-prod. Caller is responsible for the surrounding transaction
    (flush only here).
    """
    review_ids_to_classify = [r.id for r in reviews if classifications.get(r.id)]
    if not review_ids_to_classify:
        return 0

    # Single bulk fetch instead of one SELECT per review.
    existing_rows_res = await session.execute(
        select(ReviewThemeCache).where(
            ReviewThemeCache.app_id == app_id,
            ReviewThemeCache.review_id.in_(review_ids_to_classify),
        )
    )
    existing_by_id: dict[str, ReviewThemeCache] = {
        row.review_id: row for row in existing_rows_res.scalars().all()
    }

    saved = 0
    for r in reviews:
        result = classifications.get(r.id)
        if not result:
            continue
        row = existing_by_id.get(r.id)
        if row is None:
            session.add(
                ReviewThemeCache(
                    app_id=app_id,
                    review_id=r.id,
                    theme=result["theme"],
                    severity=result["severity"],
                    model=model,
                )
            )
        else:
            row.theme = result["theme"]
            row.severity = result["severity"]
            row.model = model
        saved += 1
    await session.flush()
    return saved


def partition_for_classify(
    reviews: list[ReviewOut],
    cached: dict[str, tuple[str, int]],
    *,
    force: bool,
) -> tuple[list[ReviewOut], int, int]:
    """Bucket reviews into (to_classify, skipped_cached, skipped_no_body).

    Skips rows already cached unless ``force``, and rows with no body/title
    the model can't usefully classify.
    """
    skipped_cached = 0
    skipped_no_body = 0
    to_classify: list[ReviewOut] = []
    for r in reviews:
        if not force and r.id in cached:
            skipped_cached += 1
            continue
        if not (r.body or r.title):
            skipped_no_body += 1
            continue
        to_classify.append(r)
    return to_classify, skipped_cached, skipped_no_body
