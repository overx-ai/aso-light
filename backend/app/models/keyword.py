from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.app import App


class Keyword(TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (
        UniqueConstraint("text", "locale", name="uq_keyword_text_locale"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(255), index=True)
    locale: Mapped[str] = mapped_column(String(10))
    popularity: Mapped[int | None] = mapped_column(nullable=True)
    popularity_updated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True,
    )

    trackings: Mapped[list[KeywordTracking]] = relationship(
        back_populates="keyword",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Keyword id={self.id} text={self.text!r} locale={self.locale!r}>"


class KeywordTracking(TimestampMixin, Base):
    __tablename__ = "keyword_trackings"
    __table_args__ = (
        UniqueConstraint("app_id", "keyword_id", name="uq_keyword_tracking_app_keyword"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    keyword_id: Mapped[int] = mapped_column(
        ForeignKey("keywords.id"), index=True,
    )

    app: Mapped[App] = relationship(back_populates="keyword_trackings")
    keyword: Mapped[Keyword] = relationship(back_populates="trackings")
    rankings: Mapped[list[KeywordRanking]] = relationship(
        back_populates="tracking",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<KeywordTracking app_id={self.app_id} keyword_id={self.keyword_id}>"


class KeywordRanking(Base):
    __tablename__ = "keyword_rankings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tracking_id: Mapped[int] = mapped_column(
        ForeignKey("keyword_trackings.id"), index=True,
    )
    territory_id: Mapped[int] = mapped_column(ForeignKey("territories.id"))
    rank: Mapped[int | None] = mapped_column(nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(),
    )

    tracking: Mapped[KeywordTracking] = relationship(back_populates="rankings")

    def __repr__(self) -> str:
        return f"<KeywordRanking tracking_id={self.tracking_id} rank={self.rank}>"


class KeywordLocaleIndex(Base):
    __tablename__ = "keyword_locale_indices"
    __table_args__ = (
        UniqueConstraint(
            "locale", "territory_code",
            name="uq_keyword_locale_index_locale_territory",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    locale: Mapped[str] = mapped_column(String(10))
    territory_code: Mapped[str] = mapped_column(String(3))
    is_indexed: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<KeywordLocaleIndex locale={self.locale!r} "
            f"territory={self.territory_code!r}>"
        )
