from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.app import App


class SubscriptionGroup(TimestampMixin, Base):
    __tablename__ = "subscription_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    asc_group_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))

    app: Mapped[App] = relationship(back_populates="subscription_groups")
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<SubscriptionGroup id={self.id} name={self.name!r}>"


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("subscription_groups.id"), index=True,
    )
    asc_subscription_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[str] = mapped_column(String(255))

    group: Mapped[SubscriptionGroup] = relationship(back_populates="subscriptions")
    prices: Mapped[list[SubscriptionPrice]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} product_id={self.product_id!r}>"


class SubscriptionPrice(TimestampMixin, Base):
    __tablename__ = "subscription_prices"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "territory_id",
            name="uq_subscription_price_sub_territory",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id"), index=True,
    )
    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id"), index=True,
    )
    price_point_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    customer_price: Mapped[float] = mapped_column()
    proceeds: Mapped[float] = mapped_column()
    synced_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True,
    )

    subscription: Mapped[Subscription] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return (
            f"<SubscriptionPrice sub_id={self.subscription_id} "
            f"territory_id={self.territory_id} price={self.customer_price}>"
        )


class SubscriptionPricePoint(TimestampMixin, Base):
    """Cached Apple price point tiers for a subscription + territory."""

    __tablename__ = "subscription_price_points"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "price_point_id",
            name="uq_sub_price_point",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id"), index=True,
    )
    territory_code: Mapped[str] = mapped_column(String(10))
    currency_code: Mapped[str] = mapped_column(String(10))
    customer_price: Mapped[float] = mapped_column()
    proceeds: Mapped[float] = mapped_column()
    price_point_id: Mapped[str] = mapped_column(String(255))
    synced_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<SubscriptionPricePoint sub_id={self.subscription_id} "
            f"territory={self.territory_code} price={self.customer_price}>"
        )
