from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.app import App
    from app.models.user import User


class ASCCredential(TimestampMixin, Base):
    __tablename__ = "asc_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    issuer_id: Mapped[str] = mapped_column(String(255))
    key_id: Mapped[str] = mapped_column(String(255))
    private_key_encrypted: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="credentials")
    apps: Mapped[list[App]] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ASCCredential id={self.id} name={self.name!r}>"
