"""Cache of keyword-intelligence signals (volume, difficulty) per source.

The intel surface is multi-source by design: today we have two free signals
backed by Apple Search Ads (recommendations harvest, search-term-derived);
later we'll plug paid providers (MobileAction, AppTweak, AppFigures) behind
the same shape. Each row records the provider's native score plus a
project-normalized 0–100 ``volume_score`` / ``difficulty_score`` so consumers
can compare across sources.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class KeywordIntelCache(Base):
    __tablename__ = "keyword_intel_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Per-app since recommendations come from a specific account/ad-group; even
    # search-term aggregations are scoped to the user's campaigns. Setting
    # ondelete=CASCADE keeps rows from outliving the parent app.
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True,
    )
    keyword: Mapped[str] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(16))
    # Provider key, e.g. "asa_recommendations", "asa_search_terms",
    # "mobileaction", "apptweak". A row exists per (app, keyword, locale, source)
    # so we can compare and pick the best signal at read time.
    source: Mapped[str] = mapped_column(String(48))
    volume_score: Mapped[int | None] = mapped_column(nullable=True)
    difficulty_score: Mapped[int | None] = mapped_column(nullable=True)
    # Provider's native scale (e.g. ASA's 5–100 popularity). Useful for debugging
    # and for sources with non-linear normalization.
    raw_score: Mapped[int | None] = mapped_column(nullable=True)
    # Provider-specific extras: ad_group_id, impressions, etc. — kept as JSON
    # so we don't churn the schema for each new source.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "app_id", "keyword", "locale", "source",
            name="uq_keyword_intel_cache_app_kw_loc_src",
        ),
        Index(
            "ix_keyword_intel_cache_app_locale_keyword",
            "app_id", "locale", "keyword",
        ),
    )
