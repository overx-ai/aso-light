from datetime import date

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EconomicIndex(TimestampMixin, Base):
    __tablename__ = "economic_indices"
    __table_args__ = (
        UniqueConstraint("territory_id", "index_type", name="uq_economic_index_territory_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territories.id"), index=True,
    )
    index_type: Mapped[str] = mapped_column(String(20))
    value: Mapped[float] = mapped_column()
    reference_date: Mapped[date] = mapped_column()

    def __repr__(self) -> str:
        return (
            f"<EconomicIndex territory_id={self.territory_id} "
            f"type={self.index_type!r} value={self.value}>"
        )
