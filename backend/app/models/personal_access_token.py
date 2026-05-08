from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PersonalAccessToken(TimestampMixin, Base):
    """Long-lived bearer token for headless / MCP clients.

    The plaintext token is shown to the user once at creation; only the sha256
    hash is persisted. Lookup on auth is by hash, with constant-time compare.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<PersonalAccessToken id={self.id} user_id={self.user_id} name={self.name!r}>"
