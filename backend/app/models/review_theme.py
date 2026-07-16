"""LLM-classified theme cache for App Store reviews.

Reviews live only on App Store Connect — this project doesn't store them in
the DB, only pages them through. To avoid re-classifying the same review on
every fetch, we cache one row per ``(app_id, review_id)``: the LLM's chosen
theme, its severity score, the model that produced the call, and a timestamp
so a TTL re-classify is cheap to add later.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, UTCDateTime


class ReviewThemeCache(Base):
    __tablename__ = "review_theme_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True,
    )
    review_id: Mapped[str] = mapped_column(String(64))
    # one of: bug, feature_request, praise, pricing, ux, support, other
    theme: Mapped[str] = mapped_column(String(32))
    # 1 (low) – 5 (high). Used by the priority queue: high-severity bug + 1-star
    # rating + recent + un-replied = top of queue.
    severity: Mapped[int] = mapped_column()
    model: Mapped[str] = mapped_column(String(64))
    classified_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("app_id", "review_id", name="uq_review_theme_cache_app_review"),
        Index("ix_review_theme_cache_app_theme", "app_id", "theme"),
    )
