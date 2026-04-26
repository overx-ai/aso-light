from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PricePreset(TimestampMixin, Base):
    __tablename__ = "price_presets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    base_territory_code: Mapped[str] = mapped_column(String(3), default="US")
    base_price: Mapped[float] = mapped_column()
    index_type: Mapped[str] = mapped_column(String(20))
    apply_vat: Mapped[bool] = mapped_column(default=False)
    charming_mode: Mapped[str] = mapped_column(String(10), default="none")

    user: Mapped[User] = relationship(back_populates="price_presets")

    def __repr__(self) -> str:
        return f"<PricePreset id={self.id} name={self.name!r}>"
