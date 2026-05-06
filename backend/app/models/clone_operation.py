from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.app import App


class CloneOperation(TimestampMixin, Base):
    """Tracks a sub/IAP clone-and-version-bump run.

    Persists per-step status so the UI can poll progress and so partial
    failures can be retried without re-running successful steps. The new
    productId is the natural idempotency key — re-running a finished
    operation is a no-op.
    """

    __tablename__ = "clone_operations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(20))  # "subscription" | "iap"
    source_local_id: Mapped[int] = mapped_column()
    source_asc_id: Mapped[str] = mapped_column(String(255))
    source_product_id: Mapped[str] = mapped_column(String(255))
    target_product_id: Mapped[str] = mapped_column(String(255))
    target_asc_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope_json: Mapped[dict] = mapped_column(JSON)
    asc_steps_json: Mapped[dict] = mapped_column(JSON, default=dict)
    revenuecat_steps_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_log_json: Mapped[list] = mapped_column(JSON, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    app: Mapped[App] = relationship()

    def __repr__(self) -> str:
        return (
            f"<CloneOperation id={self.id} kind={self.source_kind} "
            f"target={self.target_product_id!r} status={self.status}>"
        )
