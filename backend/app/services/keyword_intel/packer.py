"""Pure keyword-field packer: candidates -> comma-separated ≤100-char fields per locale."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.services.keyword_intel.tokenize import words
from app.services.metadata.validation import FIELD_CHAR_LIMITS

KEYWORDS_FIELD = "keywords"
KEYWORD_SEPARATOR = ","


@dataclass(frozen=True, slots=True)
class PackCandidate:
    term: str
    popularity: int
    difficulty: int

    @property
    def value(self) -> float:
        return self.popularity * (1 - self.difficulty / 100)


def pack_keywords(
    candidates: Iterable[PackCandidate],
    *,
    exclude: set[str],
    limit: int = FIELD_CHAR_LIMITS[KEYWORDS_FIELD],
) -> list[str]:
    """Greedy by value per character. Words, not phrases: Apple combines field words itself."""
    word_value: dict[str, float] = {}
    for candidate in candidates:
        for word in words(candidate.term):
            if word not in exclude:
                word_value[word] = max(word_value.get(word, 0.0), candidate.value)

    ranked = sorted(word_value.items(), key=lambda kv: (-kv[1] / len(kv[0]), kv[0]))
    packed: list[str] = []
    length = 0
    for word, value in ranked:
        if value <= 0:
            continue
        added = len(word) + (len(KEYWORD_SEPARATOR) if packed else 0)
        if length + added > limit:
            continue
        packed.append(word)
        length += added
    return packed


def pack_storefront(
    candidates_by_locale: Mapping[str, Iterable[PackCandidate]],
    *,
    names_by_locale: Mapping[str, str | None],
    subtitles_by_locale: Mapping[str, str | None],
    limit: int = FIELD_CHAR_LIMITS[KEYWORDS_FIELD],
) -> dict[str, str]:
    """Locales in order; every locale indexed in one storefront shares one word budget,
    so a word in any name/subtitle or an earlier locale's field is never repeated."""
    used: set[str] = set()
    for text in (*names_by_locale.values(), *subtitles_by_locale.values()):
        used.update(words(text))

    fields: dict[str, str] = {}
    for locale, candidates in candidates_by_locale.items():
        packed = pack_keywords(candidates, exclude=used, limit=limit)
        used.update(packed)
        fields[locale] = KEYWORD_SEPARATOR.join(packed)
    return fields


def bulk_preview_payload(fields: Mapping[str, str]) -> dict:
    """Arguments for ``metadata_bulk_preview``; empty fields are left out."""
    values = {locale: value for locale, value in fields.items() if value}
    return {
        "field": KEYWORDS_FIELD,
        "target_locales": list(values),
        "values_by_locale": values,
    }
