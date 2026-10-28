"""The last localized listing proposal per app, for ``metadata/export?source=proposal``."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, UTCDateTime


class ListingDraft(Base):
    __tablename__ = "listing_drafts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), unique=True,
    )
    proposal_hash: Mapped[str] = mapped_column(String(64))
    # locale -> {field: value}; merged across localize batches of the same proposal.
    values_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(),
    )
