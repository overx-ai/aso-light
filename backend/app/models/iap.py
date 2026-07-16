from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.app import App


class InAppPurchase(TimestampMixin, Base):
    __tablename__ = "in_app_purchases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    asc_iap_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[str] = mapped_column(String(255))
    iap_type: Mapped[str] = mapped_column(String(20))

    app: Mapped[App] = relationship(back_populates="iaps")
    prices: Mapped[list[IAPPrice]] = relationship(
        back_populates="iap",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<InAppPurchase id={self.id} product_id={self.product_id!r}>"


class IAPPrice(TimestampMixin, Base):
    __tablename__ = "iap_prices"
    __table_args__ = (
        UniqueConstraint("iap_id", "territory_id", name="uq_iap_price_iap_territory"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    iap_id: Mapped[int] = mapped_column(
        ForeignKey("in_app_purchases.id"), index=True,
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

    iap: Mapped[InAppPurchase] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return (
            f"<IAPPrice iap_id={self.iap_id} "
            f"territory_id={self.territory_id} price={self.customer_price}>"
        )
