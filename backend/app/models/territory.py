from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Territory(Base):
    __tablename__ = "territories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(3), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    currency_code: Mapped[str] = mapped_column(String(3))
    vat_rate: Mapped[float] = mapped_column(default=0.0)
    gdp_per_capita_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    apple_territory_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    def __repr__(self) -> str:
        return f"<Territory code={self.code!r} name={self.name!r}>"
