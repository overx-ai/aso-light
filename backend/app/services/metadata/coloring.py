"""Keyword-coverage classification for App Store metadata fields."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from app.schemas.metadata import KeywordCoverageItem

KeywordPlacement = Literal["title", "subtitle", "keywords", "none"]


def classify_keyword(
    keyword: str,
    name: str | None,
    subtitle: str | None,
    keywords_field: str | None,
) -> KeywordPlacement:
    """Determine where (if anywhere) `keyword` appears in the metadata.

    Precedence: title > subtitle > keywords > none.
    Matching:
      - Case-insensitive.
      - For title and subtitle: substring match against full string.
      - For the keywords field: split on comma, trim, exact-token match.
    Apple indexes single tokens from each field; multi-word keywords
    are matched by substring against the full title/subtitle but as
    exact comma-tokens within the keywords field (since users typically
    enter single words there per Apple guidance).
    """
    if keyword is None:
        return "none"

    needle = keyword.lower().strip()
    if not needle:
        return "none"

    name_norm = (name or "").lower().strip()
    subtitle_norm = (subtitle or "").lower().strip()
    keywords_norm = (keywords_field or "").lower().strip()

    if name_norm and needle in name_norm:
        return "title"

    if subtitle_norm and needle in subtitle_norm:
        return "subtitle"

    if keywords_norm:
        tokens = [token.strip() for token in keywords_norm.split(",")]
        if needle in tokens:
            return "keywords"

    return "none"


def build_coverage_items(
    keyword_texts: Iterable[str],
    by_locale: dict[str, dict[str, str | None]],
) -> list[KeywordCoverageItem]:
    """Classify each distinct keyword against each locale's metadata.

    Tracking the same keyword text in N locales yields N tracking rows that
    share ``keyword.text``; coverage depends only on the distinct text (matching
    is case-insensitive), so texts are deduped case-insensitively here — keeping
    the first-seen display casing — to emit exactly one item per
    ``(text, locale)``. ``by_locale`` maps a locale to its
    ``{"name", "subtitle", "keywords"}`` fields.
    """
    seen: dict[str, str] = {}
    for text in keyword_texts:
        seen.setdefault(text.strip().lower(), text)

    items: list[KeywordCoverageItem] = []
    for text in seen.values():
        for locale, fields in by_locale.items():
            placement = classify_keyword(
                text,
                fields["name"],
                fields["subtitle"],
                fields["keywords"],
            )
            items.append(
                KeywordCoverageItem(
                    keyword=text,
                    locale=locale,
                    placement=placement,
                )
            )
    return items
