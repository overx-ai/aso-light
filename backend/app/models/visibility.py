"""Models for the keyword visibility tracker (organic SERP snapshots)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class KeywordVisibilityWatch(TimestampMixin, Base):
    """A (keyword, country) pair an app owner is watching."""

    __tablename__ = "keyword_visibility_watches"
    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "text",
            "country",
            name="uq_kv_watch_app_text_country",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True,
    )
    text: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8))  # lowercase ISO-2 (e.g. "us")
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    snapshots: Mapped[list[KeywordVisibilitySnapshot]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="KeywordVisibilitySnapshot.polled_at.desc()",
    )


class KeywordVisibilitySnapshot(Base):
    """One poll of an iTunes search at a given time."""

    __tablename__ = "keyword_visibility_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    watch_id: Mapped[int] = mapped_column(
        ForeignKey("keyword_visibility_watches.id", ondelete="CASCADE"),
        index=True,
    )
    polled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    results_count: Mapped[int] = mapped_column(Integer, default=0)

    watch: Mapped[KeywordVisibilityWatch] = relationship(back_populates="snapshots")
    results: Mapped[list[KeywordVisibilityResult]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="KeywordVisibilityResult.position",
    )


class KeywordVisibilityResult(Base):
    """A single iTunes result row inside a snapshot."""

    __tablename__ = "keyword_visibility_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("keyword_visibility_snapshots.id", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    track_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    bundle_id: Mapped[str] = mapped_column(String(255))
    icon_url: Mapped[str] = mapped_column(String(512))

    snapshot: Mapped[KeywordVisibilitySnapshot] = relationship(back_populates="results")
