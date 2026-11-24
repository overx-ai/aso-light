"""Tokenizer shared by title mining, listing extraction, difficulty and the field packer."""

from __future__ import annotations

import re
from collections.abc import Collection

# N-grams never span these: "Calm: Sleep & Meditation" must not yield "calm sleep".
_SEGMENT_BREAK = re.compile(r"[:|,&.()/+!?;·•–—]+|\s-\s")
_WORD = re.compile(r"\w+")
# Words a searcher adds that a competing title rarely carries: "sleep sounds free" is
# served by "Sleep Sounds" apps. English only.
SEARCH_MODIFIERS = frozenset({"free", "app", "apps", "best", "top", "the", "a", "an", "for", "with", "and", "by", "of"})


def words(text: str | None) -> list[str]:
    return _WORD.findall((text or "").casefold())


def segments(text: str | None) -> list[list[str]]:
    return [toks for seg in _SEGMENT_BREAK.split(text or "") if (toks := words(seg))]


def ngrams(
    text: str | None,
    max_n: int,
    stopwords: Collection[str],
    brand: str | None = None,
) -> list[str]:
    """1..max_n-grams; a gram is dropped if it contains the brand or starts/ends with a stopword."""
    folded_brand = brand.casefold() if brand else None
    out: list[str] = []
    for tokens in segments(text):
        for n in range(1, max_n + 1):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i:i + n]
                if folded_brand in gram:
                    continue
                if gram[0] in stopwords or gram[-1] in stopwords:
                    continue
                out.append(" ".join(gram))
    return out


def core_words(text: str | None) -> list[str]:
    tokens = words(text)
    return [w for w in tokens if w not in SEARCH_MODIFIERS] or tokens


def covers_core(title: str | None, core: Collection[str]) -> bool:
    return bool(core) and set(core) <= set(words(title))
