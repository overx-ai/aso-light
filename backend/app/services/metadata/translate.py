"""AI translation suggestions for App Store metadata fields.

Translations are SUGGESTION-ONLY: they are returned to the UI for the user to
review/edit, then applied via the regular metadata write paths. They are
never auto-applied to App Store Connect.

Design notes
------------
* Model default: ``claude-haiku-4-5-20251001`` for cost (translations are short
  and low-stakes); overridable via the ``model`` parameter.
* Prompt caching is intentionally NOT enabled: Haiku 4.5's minimum cacheable
  prefix is 4096 tokens, while our system prompts are ~50-200 tokens. Adding
  ``cache_control`` would silently no-op (no error, just zero cache reads) and
  cost the ~1.25x cache-write premium for nothing. The DB-backed translation
  cache (``MetadataTranslationCache``) is the real cache layer here.
* All ASC field char limits are enforced post-translation via hard truncation.
* Per-app monthly cap (rolling 30 days) bounds Anthropic spend.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Literal

from anthropic import AsyncAnthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetadataTranslationCache

FieldKind = Literal[
    "name",
    "subtitle",
    "description",
    "keywords",
    "promotional_text",
    "whats_new",
]

FIELD_CHAR_LIMITS: dict[str, int] = {
    "name": 30,
    "subtitle": 30,
    "description": 4000,
    "keywords": 100,
    "promotional_text": 170,
    "whats_new": 4000,
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class TranslationQuotaExceededError(Exception):
    """Raised when an app exceeds its rolling 30-day translation cap."""


class AbstractTranslator(ABC):
    """Abstract translator so DeepL/OpenAI/etc. can plug in later."""

    @abstractmethod
    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        field_kind: FieldKind,
        brand_allowlist: list[str] | None = None,
    ) -> str:
        ...


class AnthropicTranslator(AbstractTranslator):
    """Claude-backed translator. Field-kind-aware + brand allowlist.

    No prompt caching: see module docstring for rationale.
    """

    def __init__(
        self, api_key: str, model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        field_kind: FieldKind,
        brand_allowlist: list[str] | None = None,
    ) -> str:
        char_limit = FIELD_CHAR_LIMITS.get(field_kind, 4000)
        system = _build_system_prompt(
            field_kind, target_locale, char_limit, brand_allowlist or [],
        )
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": f"Source ({source_locale}):\n{text}",
                },
            ],
        )
        # Defensive: response.content is List[ContentBlock]; first block is
        # text for our prompt shape.
        translated = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                translated = block.text.strip()
                break

        if field_kind == "keywords":
            translated = _post_process_keywords(translated)

        return translated[:char_limit]


def _build_system_prompt(
    field_kind: str,
    target_locale: str,
    char_limit: int,
    brand_allowlist: list[str],
) -> str:
    lines = [
        (
            f"You are a localization expert translating App Store metadata "
            f"to {target_locale}."
        ),
        f"Field type: {field_kind}. Hard character limit: {char_limit}.",
        (
            "Output ONLY the translated text — no quotes, explanations, "
            "or commentary."
        ),
    ]
    if field_kind == "keywords":
        lines.append(
            "This is the App Store keywords field: comma-separated single-word "
            "search terms. Output comma-separated, no spaces after commas, "
            "all lowercase, deduplicated, no commentary.",
        )
    elif field_kind in {"name", "subtitle"}:
        lines.append(
            f"This is shown directly in the App Store. Be concise. "
            f"STRICT {char_limit}-character limit.",
        )
    if brand_allowlist:
        lines.append(
            "Do NOT translate these brand/proper names — keep them verbatim: "
            f"{', '.join(brand_allowlist)}.",
        )
    return "\n".join(lines)


def _post_process_keywords(text: str) -> str:
    """Normalize keyword field: lowercase, dedupe, comma-separated, no spaces."""
    tokens = [t.strip().lower() for t in text.split(",") if t.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return ",".join(out)[:100]


def source_hash(text: str) -> str:
    """SHA-256 of the source text — cache key for translation reuse."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def translate_with_cache(
    translator: AbstractTranslator,
    session: AsyncSession,
    app_id: int,
    text: str,
    source_locale: str,
    target_locale: str,
    field_kind: FieldKind,
    brand_allowlist: list[str] | None = None,
    monthly_cap: int = 500,
) -> tuple[str, bool]:
    """Translate ``text`` with DB-backed cache + per-app monthly cap.

    Returns
    -------
    (translation, cached): cached is True when the result came from the
    ``MetadataTranslationCache`` table (no Anthropic call made).

    Raises
    ------
    TranslationQuotaExceededError: when the app has used >= ``monthly_cap``
        translations in the rolling last 30 days.
    """
    sh = source_hash(text)

    # 1. Cache lookup
    stmt = select(MetadataTranslationCache).where(
        MetadataTranslationCache.app_id == app_id,
        MetadataTranslationCache.source_locale == source_locale,
        MetadataTranslationCache.target_locale == target_locale,
        MetadataTranslationCache.source_hash == sh,
        MetadataTranslationCache.field_kind == field_kind,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row.translated_text, True

    # 2. Rolling 30-day cap
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    count_stmt = (
        select(func.count())
        .select_from(MetadataTranslationCache)
        .where(
            MetadataTranslationCache.app_id == app_id,
            MetadataTranslationCache.created_at >= cutoff,
        )
    )
    count_result = await session.execute(count_stmt)
    count = count_result.scalar_one()
    if count >= monthly_cap:
        raise TranslationQuotaExceededError(
            f"App {app_id} has used {count} translations in the last "
            f"30 days (cap: {monthly_cap})",
        )

    # 3. Translate
    translated = await translator.translate(
        text, source_locale, target_locale, field_kind, brand_allowlist,
    )

    # 4. Persist to cache
    new_row = MetadataTranslationCache(
        app_id=app_id,
        source_locale=source_locale,
        target_locale=target_locale,
        source_hash=sh,
        field_kind=field_kind,
        translated_text=translated,
        model=getattr(translator, "_model", "unknown"),
    )
    session.add(new_row)
    await session.flush()
    return translated, False
