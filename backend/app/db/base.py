from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) that guarantees tz-aware values on read.

    SQLite ignores ``timezone=True`` and hands back naive datetimes, which
    Pydantic then serialises without an offset — invalid RFC-3339 that strict
    ``format: date-time`` clients (e.g. some MCP clients) reject. All stored
    values are UTC (``func.now()`` / utcnow), so tag naive reads as UTC.
    Postgres already returns aware datetimes and is left untouched.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
