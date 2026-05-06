from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.competitor import CompetitorApp
    from app.models.credential import ASCCredential
    from app.models.iap import InAppPurchase
    from app.models.keyword import KeywordTracking
    from app.models.revenuecat_credential import RevenueCatCredential
    from app.models.subscription import SubscriptionGroup


class App(TimestampMixin, Base):
    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asc_credentials.id"), index=True,
    )
    revenuecat_credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("revenuecat_credentials.id"), index=True, nullable=True,
    )
    asc_app_id: Mapped[str] = mapped_column(String(255))
    bundle_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(10))
    icon_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    credential: Mapped[ASCCredential] = relationship(back_populates="apps")
    revenuecat_credential: Mapped[RevenueCatCredential | None] = relationship(
        foreign_keys=[revenuecat_credential_id],
    )
    subscription_groups: Mapped[list[SubscriptionGroup]] = relationship(
        back_populates="app",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    iaps: Mapped[list[InAppPurchase]] = relationship(
        back_populates="app",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    keyword_trackings: Mapped[list[KeywordTracking]] = relationship(
        back_populates="app",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    competitors: Mapped[list[CompetitorApp]] = relationship(
        back_populates="app",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<App id={self.id} name={self.name!r} bundle_id={self.bundle_id!r}>"
