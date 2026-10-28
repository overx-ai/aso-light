"""Pure validation and scoring of name/subtitle/keywords proposal sets."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.schemas.listing import ListingProposal
from app.services.keyword_intel.packer import KEYWORD_SEPARATOR
from app.services.keyword_intel.tokenize import words
from app.services.metadata.translate import LISTING_PROPOSALS
from app.services.metadata.validation import validate_field

PROPOSAL_FIELDS: tuple[str, ...] = ("name", "subtitle", "keywords")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def keyword_tokens(keywords: str | None) -> list[str]:
    return [t for t in (tok.strip() for tok in (keywords or "").split(KEYWORD_SEPARATOR)) if t]


def proposal_words(proposal: ListingProposal) -> list[str]:
    keyword_words = [w for token in keyword_tokens(proposal.keywords) for w in words(token)]
    return [*words(proposal.name), *words(proposal.subtitle), *keyword_words]


def is_valid_proposal(proposal: ListingProposal) -> bool:
    if not proposal.name.strip():
        return False
    if not all(validate_field(f, getattr(proposal, f))[0] for f in PROPOSAL_FIELDS):
        return False
    found = proposal_words(proposal)
    return len(found) == len(set(found))


def parse_proposals(text: str) -> list[ListingProposal]:
    """Invalid sets are dropped, never repaired: a repaired set is one nobody proposed."""
    fenced = _FENCE.search(text)
    try:
        raw = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    proposals: list[ListingProposal] = []
    for item in raw:
        if not isinstance(item, dict) or not all(isinstance(item.get(f), str) for f in PROPOSAL_FIELDS):
            continue
        proposal = ListingProposal(**{f: item[f].strip() for f in PROPOSAL_FIELDS})
        if is_valid_proposal(proposal) and proposal not in proposals:
            proposals.append(proposal)
    return proposals[:LISTING_PROPOSALS]


def covered_popularity(proposal: ListingProposal, popularity_by_term: Mapping[str, int]) -> int:
    """A term counts when all its words appear somewhere in the set: Apple combines field words."""
    present = set(proposal_words(proposal))
    return sum(
        popularity
        for term, popularity in popularity_by_term.items()
        if (term_words := set(words(term))) and term_words <= present
    )
