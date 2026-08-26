"""Cross-app IDOR guard for reviews (bug 001).

ASC scopes ``/v1/customerReviews/*`` and ``/v1/customerReviewResponses/*`` to
the whole Apple team, not per app — a bare ``review_id`` or ``response_id``
proves nothing about which of our tenants' apps it belongs to, and Apple
exposes no reverse "which app owns this review" lookup. ``list_reviews`` is
the one entry point that IS app-scoped
(``GET /v1/apps/{asc_app_id}/customerReviews``), so it is the only place we
can learn the mapping — every other read/write against a bare id
(get/draft/translate/respond/edit/delete) must be checked against what
``list_reviews`` has already told us.

``ReviewAppMap`` records ``review_id -> app_id`` as ``list_reviews``
observes it. ``ReviewResponseMap`` records ``response_id -> review_id`` from
the same page (``list_reviews`` always requests ``include=response``, so a
review's response id is known at the same time). A review/response never
observed by any ``list_reviews`` call is unknown to us and must fail closed
(404 / ``ToolError``) rather than being treated as authorized — see
``app.services.reviews.ownership.assert_review_belongs_to_app``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, UTCDateTime


class ReviewAppMap(Base):
    """review_id -> app_id, as observed by list_reviews for that app."""

    __tablename__ = "review_app_map"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class ReviewResponseMap(Base):
    """response_id -> review_id, as observed by list_reviews / create_response.

    ``review_id`` is a plain indexed string, not a hard FK into
    ``ReviewAppMap`` — both rows are populated from the same page in the
    same session but are not required to be transactionally coupled beyond
    that, and decoupling avoids an FK-constraint failure if population
    order ever changes (e.g. create_response recording a response before a
    subsequent list_reviews page refreshes the review row).
    """

    __tablename__ = "review_response_map"

    response_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
