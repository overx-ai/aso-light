"""Tokenizer shared by title mining, listing extraction, difficulty and the field packer."""

from __future__ import annotations

import re
from collections.abc import Collection

# N-grams never span these: "Calm: Sleep & Meditation" must not yield "calm sleep".
_SEGMENT_BREAK = re.compile(r"[:|,&.()/+!?;·•–—]+|\s-\s")
_WORD = re.compile(r"\w+")


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
    brand_token = brand.casefold() if brand else None
    out: list[str] = []
    for tokens in segments(text):
        for n in range(1, max_n + 1):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i:i + n]
                if brand_token in gram:
                    continue
                if gram[0] in stopwords or gram[-1] in stopwords:
                    continue
                out.append(" ".join(gram))
    return out


def contains_phrase(text: str | None, phrase: str) -> bool:
    needle = words(phrase)
    if not needle:
        return False
    n = len(needle)
    return any(
        tokens[i:i + n] == needle
        for tokens in segments(text)
        for i in range(len(tokens) - n + 1)
    )
