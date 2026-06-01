"""Keyword-intelligence subsystem.

Pluggable providers feed a single project-normalized signal shape
(:class:`KeywordIntel`) into the cache table ``keyword_intel_cache``.
Free providers backed by Apple Search Ads ship today; paid providers
(MobileAction, AppTweak, AppFigures) slot in behind the same ABC.
"""
from app.services.keyword_intel.base import (
    KeywordIntel,
    KeywordIntelProvider,
    upsert_intel,
)

__all__ = ["KeywordIntel", "KeywordIntelProvider", "upsert_intel"]
