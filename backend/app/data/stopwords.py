"""Per-language stopwords for keyword mining, keyed by the locale's language subtag."""

from __future__ import annotations

from collections.abc import Iterable

# Words that fill App Store titles in every language but nobody searches for alone.
GENERIC_STORE_WORDS: frozenset[str] = frozenset({"app", "apps", "hd", "lite"})

STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "best",
        "by", "can", "do", "for", "from", "get", "has", "have", "how", "i", "in",
        "into", "is", "it", "its", "just", "more", "my", "new", "no", "not", "now",
        "of", "on", "one", "or", "our", "out", "so", "that", "the", "their", "them",
        "this", "to", "up", "us", "we", "what", "when", "with", "you", "your",
    }),
    "de": frozenset({
        "aber", "alle", "als", "am", "an", "auch", "auf", "aus", "bei", "das", "dass",
        "dein", "deine", "dem", "den", "der", "des", "die", "du", "ein", "eine",
        "einen", "für", "im", "in", "ist", "mit", "nach", "nicht", "oder", "und",
        "uns", "vom", "von", "zu", "zum", "zur",
    }),
    "fr": frozenset({
        "à", "au", "aux", "avec", "ce", "dans", "de", "des", "du", "en", "et", "la",
        "le", "les", "ma", "mon", "par", "pour", "sur", "ta", "ton", "un", "une",
        "vos", "votre",
    }),
    "es": frozenset({
        "a", "al", "con", "de", "del", "el", "en", "es", "la", "las", "los", "mi",
        "para", "por", "su", "tu", "un", "una", "y",
    }),
    "it": frozenset({
        "a", "al", "con", "da", "del", "della", "di", "e", "il", "in", "la", "le",
        "lo", "per", "su", "tuo", "un", "una",
    }),
    "pt": frozenset({
        "a", "ao", "com", "da", "de", "do", "e", "em", "na", "no", "o", "os", "para",
        "por", "seu", "sua", "um", "uma",
    }),
    "nl": frozenset({
        "de", "een", "en", "het", "in", "je", "jouw", "met", "van", "voor",
    }),
    "ru": frozenset({
        "в", "для", "и", "из", "к", "на", "о", "по", "с", "твой", "у",
    }),
}


def language_of(locale: str) -> str:
    return locale.replace("_", "-").split("-")[0].lower()


def stopwords_for_locales(locales: Iterable[str]) -> frozenset[str]:
    words: set[str] = set(GENERIC_STORE_WORDS)
    for locale in locales:
        words |= STOPWORDS.get(language_of(locale), frozenset())
    return frozenset(words)
