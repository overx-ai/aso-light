"""App Store Connect Analytics Reports — enrollment + daily fact table.

Apple's Analytics Reports API is asynchronous: you enroll an app once
(``analyticsReportRequests``), then walk
``reports -> instances -> segments -> signed URL`` to download gzipped TSV.
:class:`AppAnalyticsReportRequest` remembers the enrollment so it is created
exactly once per (app, accessType); :class:`AppAnalyticsDaily` holds the parsed
rows.

Two invariants are deliberate, both learned from ``ASAMetricDaily``:

**Tenant scoping.** ``credential_id`` is NOT NULL and every read filters on
credentials owned by the calling user. ASA originally scoped metrics by app
identifier alone, which leaked across tenants advertising the same Apple app.

**No nullable column in the unique grain.** ASA dedupes on
``(dim_kind, dim_id, date, storefront)`` where ``storefront`` is nullable —
and NULL never equals NULL in a unique index, so its ``ON CONFLICT`` arbiter
never matches and every re-sync silently duplicates rows (see
``tests/test_asa_metric_upsert_dedupe.py``). Here every dimension is NOT NULL
with an empty-string sentinel, and the grain keys off ``dim_hash`` so the index
stays narrow while remaining total.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime

# Apple's accessType values.
ACCESS_ONE_TIME = "ONE_TIME_SNAPSHOT"
ACCESS_ONGOING = "ONGOING"

# Our report_kind discriminator (mirrors ASAMetricDaily.dim_kind).
KIND_ENGAGEMENT = "ENGAGEMENT"
KIND_DOWNLOADS = "DOWNLOADS"

# The dimension columns, in the order hashed into dim_hash. Changing this
# tuple changes every hash, so it must stay stable (append, never reorder).
DIMENSIONS = (
    "territory",
    "source_type",
    "page_type",
    "page_title",
    "device",
    "app_version",
)


def dim_hash(values: dict[str, str]) -> str:
    """Stable sha256 over the dimension tuple.

    Missing dimensions normalize to ``""`` so a row that omits a dimension and
    a row that reports it empty collapse to the same grain -- the sentinel is
    the whole point, since a NULL here would break dedupe the way it does on
    ``ASAMetricDaily``.
    """
    joined = "\x1f".join((values.get(name) or "").strip() for name in DIMENSIONS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class AppAnalyticsReportRequest(TimestampMixin, Base):
    """One Apple ``analyticsReportRequests`` enrollment per (app, accessType).

    Persisted so enrollment is idempotent: Apple rejects (or silently
    duplicates) repeat requests for the same app + accessType, so we reuse the
    stored id instead of POSTing again.
    """

    __tablename__ = "app_analytics_report_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"),
        index=True,
    )
    access_type: Mapped[str] = mapped_column(String(24))
    asc_request_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "access_type",
            name="uq_app_analytics_request_app_access",
        ),
    )


class AppAnalyticsDaily(TimestampMixin, Base):
    """A parsed daily row from an analytics report segment.

    ``report_kind`` selects which metrics are meaningful: ENGAGEMENT rows carry
    impressions/page views/taps, DOWNLOADS rows carry downloads/redownloads.
    Unused metrics stay 0 rather than NULL so sums never need coalescing.

    Conversion rate is NOT stored -- it is derived at read time as
    ``downloads / page_views`` in Decimal, because the two live on rows of
    different ``report_kind`` and a stored ratio would drift from its inputs.
    """

    __tablename__ = "app_analytics_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"),
        index=True,
    )
    # Tenant scoping. NOT NULL by design: a row nobody owns would be a row
    # every scoped query has to special-case.
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("asc_credentials.id", ondelete="CASCADE"),
        index=True,
    )
    report_kind: Mapped[str] = mapped_column(String(16))
    date: Mapped[date] = mapped_column(Date)

    # Dimensions -- all NOT NULL, "" sentinel. See module docstring.
    territory: Mapped[str] = mapped_column(String(8), default="")
    source_type: Mapped[str] = mapped_column(String(128), default="")
    page_type: Mapped[str] = mapped_column(String(64), default="")
    # Apple reports the product page by TITLE, not id: "Default product
    # page", "Default custom product page", or a CPP's own name.
    page_title: Mapped[str] = mapped_column(String(255), default="")
    device: Mapped[str] = mapped_column(String(64), default="")
    app_version: Mapped[str] = mapped_column(String(64), default="")
    dim_hash: Mapped[str] = mapped_column(String(64))

    # Metrics.
    impressions: Mapped[int] = mapped_column(default=0)
    unique_impressions: Mapped[int] = mapped_column(default=0)
    page_views: Mapped[int] = mapped_column(default=0)
    unique_page_views: Mapped[int] = mapped_column(default=0)
    taps: Mapped[int] = mapped_column(default=0)
    downloads: Mapped[int] = mapped_column(default=0)
    redownloads: Mapped[int] = mapped_column(default=0)

    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "report_kind",
            "date",
            "dim_hash",
            name="uq_app_analytics_daily_grain",
        ),
        Index("ix_app_analytics_daily_app_date", "app_id", "date"),
    )
