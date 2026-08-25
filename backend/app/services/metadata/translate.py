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
* ASC field char limits are enforced post-translation WITHOUT truncation: an
  over-long translation is retried once with an explicit length instruction and
  then raises :class:`TranslationOverflowError`. Slicing the string would ship a
  mid-word cut to a storefront nobody on the team reads. The one exception is
  the comma-separated keywords field, where whole trailing terms are dropped.
* Char limits are imported from ``app.services.metadata.validation`` — a single
  source of truth shared with the editor/bulk validation path. They are global:
  Apple counts Unicode code points uniformly, so there is no per-locale limit.
* Per-app monthly cap (rolling 30 days) bounds Anthropic spend.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

import httpx
from anthropic import AsyncAnthropic
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.metadata import MetadataTranslationCache
from app.services.metadata.validation import FIELD_CHAR_LIMITS

logger = logging.getLogger(__name__)

FieldKind = Literal[
    "name",
    "subtitle",
    "description",
    "keywords",
    "promotional_text",
    "whats_new",
]

# Applied to a field kind with no documented Apple limit (defensive; every
# member of ``FieldKind`` is in FIELD_CHAR_LIMITS today).
FALLBACK_CHAR_LIMIT: Final[int] = 4000

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class TranslationQuotaExceededError(Exception):
    """Raised when an app exceeds its rolling 30-day translation cap."""


class TranslatorUnavailableError(Exception):
    """Raised when every configured translation provider fails."""


class TranslationOverflowError(Exception):
    """Raised when a translation still exceeds the field limit after a retry.

    Deliberately preferred over truncation: a suggestion that cannot fit is a
    suggestion the user must rewrite, not one we silently cut mid-word.
    """


class AbstractTranslator(ABC):
    """Abstract translator so DeepL/OpenAI/etc. can plug in later."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Provider-qualified model id, e.g. ``anthropic:claude-haiku-4-5``.

        Used for cache bookkeeping and failover logs.
        """
        ...

    @abstractmethod
    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        field_kind: FieldKind,
        brand_allowlist: list[str] | None = None,
    ) -> str: ...


class _PromptedTranslator(AbstractTranslator):
    """Shared pipeline: build prompt -> complete -> post-process -> fit limit.

    Subclasses implement :meth:`_complete` (exactly one provider round-trip).
    Everything else — the field-kind system prompt, keyword normalization and
    the no-truncation char-limit enforcement (retry once, then raise) — lives
    here so every provider behaves identically.
    """

    @abstractmethod
    async def _complete(
        self,
        system: str,
        source_locale: str,
        text: str,
    ) -> str:
        """One provider round-trip. Returns the raw (stripped) completion."""
        ...

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        field_kind: FieldKind,
        brand_allowlist: list[str] | None = None,
    ) -> str:
        char_limit = FIELD_CHAR_LIMITS.get(field_kind, FALLBACK_CHAR_LIMIT)
        allowlist = brand_allowlist or []

        async def attempt(overflow_by: int | None = None) -> str:
            """One prompt -> completion -> post-process round-trip."""
            system = _build_system_prompt(
                field_kind,
                target_locale,
                char_limit,
                allowlist,
                overflow_by=overflow_by,
            )
            return _post_process(
                await self._complete(system, source_locale, text),
                field_kind,
            )

        translated = await attempt()
        if len(translated) <= char_limit:
            return translated

        # Retry ONCE with an explicit length instruction. Truncating instead
        # would cut mid-word; a second overflow is the user's call to make.
        overflow = len(translated) - char_limit
        logger.warning(
            "Translation to %s for %r overflowed by %d char(s) — retrying "
            "with an explicit length instruction",
            target_locale,
            field_kind,
            overflow,
        )
        retried = await attempt(overflow)
        if len(retried) <= char_limit:
            return retried

        raise TranslationOverflowError(
            f"Translation of {field_kind!r} to {target_locale} is "
            f"{len(retried) - char_limit} char(s) over the {char_limit}-char "
            f"limit after a retry (model: {self.model_name})",
        )


class AnthropicTranslator(_PromptedTranslator):
    """Claude-backed translator. Field-kind-aware + brand allowlist.

    No prompt caching: see module docstring for rationale.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return f"anthropic:{self._model}"

    async def _complete(
        self,
        system: str,
        source_locale: str,
        text: str,
    ) -> str:
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
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


class OpenRouterTranslator(_PromptedTranslator):
    """OpenRouter-backed translator (openrouter.ai).

    OpenRouter exposes an OpenAI-compatible ``/chat/completions`` endpoint, so
    we call it with raw ``httpx`` (no extra SDK); the prompt, post-processing
    and char-limit handling come from :class:`_PromptedTranslator`, identical
    to AnthropicTranslator. A single OpenRouter key fans out to many upstream
    models (Anthropic, OpenAI, Google, etc.) selected by ``model`` slug.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return f"openrouter:{self._model}"

    async def _complete(
        self,
        system: str,
        source_locale: str,
        text: str,
    ) -> str:
        payload = {
            "model": self._model,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Source ({source_locale}):\n{text}",
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"
        if self._http_client is not None:
            response = await self._http_client.post(
                url,
                headers=headers,
                json=payload,
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()


class FallbackTranslator(AbstractTranslator):
    """Composite translator: try each provider in order, failover on error.

    A provider error (auth, rate limit, network, bad model slug, …) is logged
    and the next provider is tried. Raises ``TranslatorUnavailableError`` only
    when every provider has failed. ``TranslationOverflowError`` counts as a
    provider failure on purpose — a terser model may well fit the limit — and
    is chained as ``__cause__`` when it is the last one.
    """

    def __init__(self, translators: list[AbstractTranslator]) -> None:
        if not translators:
            raise ValueError("FallbackTranslator requires at least one translator")
        self._translators = translators

    @property
    def model_name(self) -> str:
        return "|".join(t.model_name for t in self._translators)

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        field_kind: FieldKind,
        brand_allowlist: list[str] | None = None,
    ) -> str:
        last_exc: Exception | None = None
        for translator in self._translators:
            try:
                return await translator.translate(
                    text,
                    source_locale,
                    target_locale,
                    field_kind,
                    brand_allowlist,
                )
            except Exception as exc:  # noqa: BLE001 — providers raise diverse types
                last_exc = exc
                logger.warning(
                    "Translator %s failed (%s) — failing over to next provider",
                    translator.model_name,
                    exc,
                )
                continue
        raise TranslatorUnavailableError(
            "All configured translation providers failed",
        ) from last_exc


def build_translator(settings: Settings) -> AbstractTranslator | None:
    """Build a translator from ``settings.TRANSLATION_PROVIDER_CHAIN``.

    Includes only providers whose API key is configured (others are skipped).
    Returns ``None`` when no provider is configured (caller surfaces a 503).

    Otherwise ALWAYS wraps the chain in a :class:`FallbackTranslator` — even a
    single provider — so a raw provider exception (e.g. ``httpx`` errors from
    ``OpenRouterTranslator``) is normalized to ``TranslatorUnavailableError``
    by the fallback's ``except`` handler rather than bubbling up as an
    unhandled 500.
    """
    seen: set[str] = set()
    translators: list[AbstractTranslator] = []
    for raw in (settings.TRANSLATION_PROVIDER_CHAIN or "").split(","):
        provider = raw.strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        if provider == "openrouter" and settings.OPENROUTER_API_KEY:
            translators.append(
                OpenRouterTranslator(
                    api_key=settings.OPENROUTER_API_KEY,
                    model=settings.OPENROUTER_TRANSLATION_MODEL,
                    base_url=settings.OPENROUTER_BASE_URL,
                ),
            )
        elif provider == "anthropic" and settings.ANTHROPIC_API_KEY:
            translators.append(
                AnthropicTranslator(api_key=settings.ANTHROPIC_API_KEY),
            )

    if not translators:
        return None
    return FallbackTranslator(translators)


def _build_system_prompt(
    field_kind: str,
    target_locale: str,
    char_limit: int,
    brand_allowlist: list[str],
    overflow_by: int | None = None,
) -> str:
    """System prompt for one translation attempt.

    ``overflow_by`` is set only on the retry after an over-long first attempt;
    it adds an explicit "must be under N characters" instruction.
    """
    lines = [
        (
            f"You are a localization expert translating App Store metadata "
            f"to {target_locale}."
        ),
        f"Field type: {field_kind}. Hard character limit: {char_limit}.",
        ("Output ONLY the translated text — no quotes, explanations, or commentary."),
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
    if overflow_by is not None:
        lines.append(
            f"Your previous attempt was {overflow_by} character(s) too long. "
            f"The result MUST be under {char_limit + 1} characters "
            f"(at most {char_limit}, counting spaces and punctuation). "
            "Rewrite it shorter — do not cut a word in half.",
        )
    return "\n".join(lines)


def _post_process(text: str, field_kind: str) -> str:
    """Field-kind normalization applied to every raw completion."""
    if field_kind == "keywords":
        return _post_process_keywords(text)
    return text


def _post_process_keywords(text: str) -> str:
    """Normalize keyword field: lowercase, dedupe, comma-separated, no spaces.

    This is the ONE field allowed to truncate, because it is a comma-separated
    list: whole trailing terms are dropped until the value fits. A partial term
    is never emitted — half a keyword is a keyword nobody searches for.
    """
    limit = FIELD_CHAR_LIMITS["keywords"]
    # ``dict.fromkeys`` dedupes while preserving first-seen order.
    deduped = dict.fromkeys(t.strip().lower() for t in text.split(",") if t.strip())
    out = ""
    for token in deduped:
        candidate = f"{out},{token}" if out else token
        if len(candidate) > limit:
            break
        out = candidate
    return out


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
    TranslationOverflowError: when the translation still exceeds the field's
        char limit after the retry (nothing is billed or cached).
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

    # 2. Translate (outside the cap critical section — the network call is the
    #    slow part; we re-check the cap atomically just before billing/insert).
    translated = await translator.translate(
        text,
        source_locale,
        target_locale,
        field_kind,
        brand_allowlist,
    )

    # 3. Atomically re-check the cap and persist the billed row.
    #
    #    Concurrent translates on separate sessions could both read count==499
    #    and both insert, overrunning the cap. We serialize the count+insert
    #    per app:
    #      * PostgreSQL: a transaction-scoped advisory lock keyed on app_id.
    #        It is released when this row's transaction commits below, so the
    #        critical section is exactly count -> insert -> commit.
    #      * SQLite: the single-writer transaction already serializes writers;
    #        re-checking the count immediately before insert suffices.
    await _acquire_app_cap_lock(session, app_id)

    used = await _count_recent_translations(session, app_id)
    if used >= monthly_cap:
        raise TranslationQuotaExceededError(
            f"App {app_id} has used {used} translations in the last "
            f"30 days (cap: {monthly_cap})",
        )

    new_row = MetadataTranslationCache(
        app_id=app_id,
        source_locale=source_locale,
        target_locale=target_locale,
        source_hash=sh,
        field_kind=field_kind,
        translated_text=translated,
        model=translator.model_name[:64],
    )
    session.add(new_row)
    # Commit each successful, billed translation durably as it is produced so a
    # LATER batch item's failure cannot roll back already-billed rows (which
    # would both under-count the cap and re-bill the same translation). The
    # commit also releases the Postgres advisory lock acquired above.
    await session.commit()
    return translated, False


async def _count_recent_translations(
    session: AsyncSession,
    app_id: int,
    *,
    window_days: int = 30,
) -> int:
    """Count billed translations for ``app_id`` in the rolling window.

    Must run inside the cap critical section (after the advisory lock) so the
    count it returns is the value the cap check + insert are serialized against.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    stmt = (
        select(func.count())
        .select_from(MetadataTranslationCache)
        .where(
            MetadataTranslationCache.app_id == app_id,
            MetadataTranslationCache.created_at >= cutoff,
        )
    )
    return (await session.execute(stmt)).scalar_one()


def _app_cap_lock_key(app_id: int) -> int:
    """Stable signed 64-bit key for ``pg_advisory_xact_lock`` from an app id.

    ``pg_advisory_xact_lock(bigint)`` takes a signed 64-bit integer. App ids are
    small positive ints, so we namespace them into a fixed high band to avoid
    colliding with advisory locks taken elsewhere, and keep the result inside
    the signed 64-bit range.
    """
    _NAMESPACE = 0x4D455441  # "META"
    key = (_NAMESPACE << 31) | (app_id & 0x7FFFFFFF)
    return key - (1 << 63) if key >= (1 << 63) else key


async def _acquire_app_cap_lock(session: AsyncSession, app_id: int) -> None:
    """Serialize the translation cap critical section across sessions.

    PostgreSQL only — takes a transaction-scoped advisory lock that releases on
    the next commit/rollback. On SQLite (and any other dialect) this is a no-op
    because the single-writer transaction already serializes concurrent writers.
    """
    bind = session.bind
    dialect = bind.dialect.name if bind is not None else "sqlite"
    if dialect != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _app_cap_lock_key(app_id)},
    )
