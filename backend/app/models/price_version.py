from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PriceVersion(TimestampMixin, Base):
    __tablename__ = "price_versions"
    __table_args__ = (
        UniqueConstraint(
            "app_id", "product_kind", "product_ref_id", "version",
            name="uq_price_version_product_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    product_kind: Mapped[str] = mapped_column(String(16))
    product_ref_id: Mapped[int] = mapped_column()
    product_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column()
    source: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    base_territory_code: Mapped[str | None] = mapped_column(
        String(3), nullable=True,
    )
    intro_offer: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
    )
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PriceVersion {self.product_kind}:{self.product_ref_id} "
            f"v{self.version}>"
        )
