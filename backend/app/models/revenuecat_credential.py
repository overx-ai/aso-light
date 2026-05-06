from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class RevenueCatCredential(TimestampMixin, Base):
    __tablename__ = "revenuecat_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    project_id: Mapped[str] = mapped_column(String(255))
    rc_app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_key_encrypted: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:
        return (
            f"<RevenueCatCredential id={self.id} project_id={self.project_id!r}>"
        )
