from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.app import App


class CompetitorApp(TimestampMixin, Base):
    __tablename__ = "competitor_apps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    asc_app_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    bundle_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    app: Mapped[App] = relationship(back_populates="competitors")

    def __repr__(self) -> str:
        return f"<CompetitorApp id={self.id} name={self.name!r}>"
