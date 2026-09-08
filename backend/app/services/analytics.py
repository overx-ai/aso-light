"""App Store analytics — segment parsing, ingest, and tenant-scoped reads.

Parsing uses only the standard library (``gzip`` + ``csv``): Apple ships report
segments as tab-delimited text inside a gzip envelope, which ``csv.DictReader``
reads directly. No new dependency earns its keep here.

Scoping invariant, mirroring :mod:`app.services.asa.analytics`: every read
filters on ``app_id`` **and** ``credential_id IN (credentials owned by
user_id)``. Two users can hold credentials for the same Apple app, so app id
alone is not a tenant boundary.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import (
    ACCESS_ONE_TIME,
    ACCESS_ONGOING,
    KIND_DOWNLOADS,
    KIND_ENGAGEMENT,
    AppAnalyticsDaily,
    AppAnalyticsReportRequest,
    dim_hash,
)
from app.models.app import App
from app.models.credential import ASCCredential
from app.services.asc import analytics as asc_analytics
from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Segment parsing
# ---------------------------------------------------------------------------
#
# Confirmed against live segments (tenant 7bfdb99d, 2026-08-29). Apple ships a
# LONG format, not wide metric columns -- the metric name is a cell value:
#
#   Engagement: Date, App Name, App Apple Identifier, Event, Page Type,
#               Page Title, Source Type, Source Info, Campaign,
#               Engagement Type, Device, Platform Version, Territory,
#               Counts, Unique Counts
#   Downloads:  Date, App Name, App Apple Identifier, Download Type,
#               App Version, Device, Platform Version, Source Type,
#               Source Info, Campaign, Page Type, Page Title, Pre-Order,
#               Territory, Counts
#
# So parsing is a pivot: Event/Download Type selects the target column and
# Counts carries the value.

# Engagement ``Event`` -> (total column, unique column | None).
_EVENT_METRICS = {
    "impression": ("impressions", "unique_impressions"),
    "page view": ("page_views", "unique_page_views"),
    "tap": ("taps", None),
}

# Downloads ``Download Type`` -> column. Update types are deliberately absent:
# "Auto-update" and "Manual update" are not downloads, and on a real segment
# they outnumbered first-time downloads 17 to 4. Summing every Counts row would
# have reported 21 downloads where there were 4, and inflated conversion rate
# with it.
_DOWNLOAD_TYPE_METRICS = {
    "first-time download": "downloads",
    "redownload": "redownloads",
}

_ALL_METRICS = (
    "impressions",
    "unique_impressions",
    "page_views",
    "unique_page_views",
    "taps",
    "downloads",
    "redownloads",
)

# TSV column -> our dimension. Platform Version is intentionally NOT a
# dimension: we aggregate over it, which is exactly why parsing must sum into
# a grain rather than emit one row per source line (see _accumulate).
_DIMENSION_COLUMNS = {
    "territory": "territory",
    "sourcetype": "source_type",
    "pagetype": "page_type",
    "pagetitle": "page_title",
    "device": "device",
    "appversion": "app_version",
}


def _norm(header: str) -> str:
    return header.replace(" ", "").replace("_", "").strip().lower()


def _as_int(raw: str | None) -> int:
    """Parse a Counts cell.

    Apple omits privacy-thresholded values and may render them as "" or "-";
    both mean "not reported", which is 0 for summing purposes.
    """
    if raw is None:
        return 0
    text = raw.strip().replace(",", "")
    if not text or text == "-":
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _row_dimensions(raw_row: dict[str, str]) -> dict[str, str]:
    dims = {name: "" for name in _DIMENSION_COLUMNS.values()}
    for header, value in raw_row.items():
        if header is None:
            continue
        target = _DIMENSION_COLUMNS.get(_norm(header))
        if target:
            dims[target] = (value or "").strip()
    return dims


def parse_segment(raw_gz: bytes, *, report_kind: str) -> list[dict[str, Any]]:
    """Parse one gzipped TSV segment into aggregated, fact-ready rows.

    Two things make this more than a row-for-row translation:

    * **Pivot.** The metric lives in ``Event`` (engagement) or ``Download
      Type`` (downloads); ``Counts`` is the value. One output row therefore
      merges the Impression, Page view and Tap lines that share a grain.
    * **Aggregation.** ``Platform Version`` is not one of our dimensions, so
      several source lines collapse onto one grain. They must be SUMMED here:
      the DB write is an upsert, so emitting them separately would make the
      last line silently overwrite the others instead of adding to them.

    Rows with no parseable date (footers/totals) and rows whose metrics are all
    zero are skipped -- Apple drops thresholded rows, and writing them as zeros
    would present "suppressed" as "measured none".
    """
    text = gzip.decompress(raw_gz).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")

    acc: dict[tuple, dict[str, Any]] = {}
    for raw_row in reader:
        normalized = {_norm(h): v for h, v in raw_row.items() if h is not None}
        row_date = _parse_date(normalized.get("date"))
        if row_date is None:
            continue

        counts = _as_int(normalized.get("counts"))
        unique = _as_int(normalized.get("uniquecounts"))

        if report_kind == KIND_ENGAGEMENT:
            event = (normalized.get("event") or "").strip().lower()
            mapped = _EVENT_METRICS.get(event)
            if mapped is None:
                continue
            total_col, unique_col = mapped
            metrics = {total_col: counts}
            if unique_col:
                metrics[unique_col] = unique
        else:
            dl_type = (normalized.get("downloadtype") or "").strip().lower()
            column = _DOWNLOAD_TYPE_METRICS.get(dl_type)
            if column is None:
                continue  # an update is not a download
            metrics = {column: counts}

        if not any(metrics.values()):
            continue

        dims = _row_dimensions(raw_row)
        key = (row_date, *(dims[name] for name in sorted(dims)))
        entry = acc.get(key)
        if entry is None:
            entry = {"date": row_date, **dims}
            entry.update({name: 0 for name in _ALL_METRICS})
            acc[key] = entry
        for column, value in metrics.items():
            entry[column] += value

    return [row for row in acc.values() if any(row[m] for m in _ALL_METRICS)]


def to_fact_rows(
    parsed: list[dict[str, Any]],
    *,
    app_id: int,
    credential_id: int,
    report_kind: str,
) -> list[dict[str, Any]]:
    """Turn parsed rows into AppAnalyticsDaily upsert dicts."""
    out: list[dict[str, Any]] = []
    for row in parsed:
        dims = {
            "territory": row.get("territory", ""),
            "source_type": row.get("source_type", ""),
            "page_type": row.get("page_type", ""),
            "page_title": row.get("page_title", ""),
            "device": row.get("device", ""),
            "app_version": row.get("app_version", ""),
        }
        out.append(
            {
                "app_id": app_id,
                "credential_id": credential_id,
                "report_kind": report_kind,
                "date": row["date"],
                **dims,
                "dim_hash": dim_hash(dims),
                **{name: int(row.get(name) or 0) for name in _ALL_METRICS},
            }
        )
    return out


def _dialect_insert(session: AsyncSession):
    """Pick the dialect-specific insert for ON CONFLICT support."""
    name = session.bind.dialect.name if session.bind else "sqlite"
    return sqlite_insert if name == "sqlite" else pg_insert


_GRAIN = ["app_id", "report_kind", "date", "dim_hash"]


async def upsert_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Bulk-upsert fact rows on the full, non-nullable grain.

    Every column in ``_GRAIN`` is NOT NULL, so the ON CONFLICT arbiter always
    matches an existing row and a re-sync updates rather than duplicating.
    That is the whole point — see tests/test_app_analytics_upsert_dedupe.py.
    """
    if not rows:
        return 0
    insert = _dialect_insert(session)
    written = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        stmt = insert(AppAnalyticsDaily.__table__).values(chunk)
        update_cols = {
            c: getattr(stmt.excluded, c)
            for c in chunk[0].keys()
            if c not in _GRAIN
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=_GRAIN, set_=update_cols
        )
        await session.execute(stmt)
        written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# Enrollment + sync
# ---------------------------------------------------------------------------


async def enroll_app(
    session: AsyncSession, *, app: App, client: ASCClient,
) -> dict[str, Any]:
    """Ensure both snapshot and ongoing enrollments exist for an app.

    Snapshot gives history immediately; ongoing keeps it current. Recorded
    locally so we never re-POST an enrollment Apple already has.
    """
    results: dict[str, Any] = {}
    for access_type in (ACCESS_ONE_TIME, ACCESS_ONGOING):
        request_id, created = await asc_analytics.ensure_enrolled(
            client, asc_app_id=app.asc_app_id, access_type=access_type
        )
        existing = (
            await session.execute(
                select(AppAnalyticsReportRequest).where(
                    AppAnalyticsReportRequest.app_id == app.id,
                    AppAnalyticsReportRequest.access_type == access_type,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AppAnalyticsReportRequest(
                    app_id=app.id,
                    access_type=access_type,
                    asc_request_id=request_id,
                )
            )
        else:
            existing.asc_request_id = request_id
        results[access_type] = {"request_id": request_id, "created": created}
    await session.commit()
    return results


async def enrollment_status(
    session: AsyncSession, *, user_id: int, app_id: int
) -> dict[str, Any]:
    """Enrollment state plus how much data is stored locally.

    Row counts go through the same tenant scope as every other read, so this
    can't be used to probe whether another user's app has data.
    """
    enrollments = (
        (
            await session.execute(
                select(AppAnalyticsReportRequest).where(
                    AppAnalyticsReportRequest.app_id == app_id
                )
            )
        )
        .scalars()
        .all()
    )
    stats = (
        await session.execute(
            select(
                AppAnalyticsDaily.report_kind,
                func.count().label("rows"),
                func.max(AppAnalyticsDaily.date).label("latest"),
                func.min(AppAnalyticsDaily.date).label("earliest"),
            )
            .where(
                AppAnalyticsDaily.app_id == app_id,
                AppAnalyticsDaily.credential_id.in_(owned_credential_ids(user_id)),
            )
            .group_by(AppAnalyticsDaily.report_kind)
        )
    ).all()

    return {
        "enrolled": bool(enrollments),
        "enrollments": [
            {
                "access_type": e.access_type,
                "asc_request_id": e.asc_request_id,
                "last_synced_at": e.last_synced_at,
            }
            for e in enrollments
        ],
        "data": [
            {
                "report_kind": s.report_kind,
                "rows": int(s.rows or 0),
                "earliest": s.earliest,
                "latest": s.latest,
            }
            for s in stats
        ],
    }


_REPORTS = (
    (
        KIND_ENGAGEMENT,
        asc_analytics.CATEGORY_ENGAGEMENT,
        asc_analytics.REPORTS_ENGAGEMENT,
    ),
    (
        KIND_DOWNLOADS,
        asc_analytics.CATEGORY_COMMERCE,
        asc_analytics.REPORTS_DOWNLOADS,
    ),
)


async def sync_app_analytics(
    session: AsyncSession,
    *,
    app: App,
    client: ASCClient,
    days: int = 30,
) -> dict[str, Any]:
    """Pull new report instances for an app and upsert their rows.

    Returns a summary rather than writing an operations-log row.
    # ponytail: no ASASyncOperation-style audit table; add one if sync failures
    # ever need forensics beyond this return value and the logs.
    """
    enrollments = (
        (
            await session.execute(
                select(AppAnalyticsReportRequest).where(
                    AppAnalyticsReportRequest.app_id == app.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not enrollments:
        return {"enrolled": False, "rows": 0, "reports": []}

    cutoff = date.today() - timedelta(days=days - 1)
    summary: list[dict[str, Any]] = []
    total = 0

    for enrollment in enrollments:
        for report_kind, category, report_names in _REPORTS:
            report = await asc_analytics.find_report(
                client,
                request_id=enrollment.asc_request_id,
                category=category,
                names=report_names,
            )
            if report is None:
                continue
            written = await _ingest_report(
                session,
                client=client,
                app=app,
                report_id=str(report["id"]),
                report_kind=report_kind,
                cutoff=cutoff,
            )
            total += written
            summary.append(
                {
                    "access_type": enrollment.access_type,
                    "report_kind": report_kind,
                    "rows": written,
                }
            )
        enrollment.last_synced_at = datetime.now(timezone.utc)

    await session.commit()
    return {"enrolled": True, "rows": total, "reports": summary}


async def _ingest_report(
    session: AsyncSession,
    *,
    client: ASCClient,
    app: App,
    report_id: str,
    report_kind: str,
    cutoff: date,
) -> int:
    """Download and upsert every in-window segment of one report."""
    written = 0
    for instance in await asc_analytics.list_instances(
        client, report_id=report_id
    ):
        attrs = instance.get("attributes") or {}
        processing_date = _parse_date(attrs.get("processingDate"))
        if processing_date is not None and processing_date < cutoff:
            continue
        for segment in await asc_analytics.list_segments(
            client, instance_id=str(instance["id"])
        ):
            seg_attrs = segment.get("attributes") or {}
            url = seg_attrs.get("url")
            if not url:
                continue
            raw = await asc_analytics.download_segment(client, url=url)
            expected = seg_attrs.get("checksum")
            if expected and not _checksum_ok(raw, expected):
                logger.warning(
                    "analytics segment checksum mismatch, skipping (report=%s)",
                    report_id,
                )
                continue
            rows = to_fact_rows(
                parse_segment(raw, report_kind=report_kind),
                app_id=app.id,
                credential_id=app.credential_id,
                report_kind=report_kind,
            )
            written += await upsert_rows(session, rows)
    return written


def _checksum_ok(raw: bytes, expected: str) -> bool:
    """Verify a segment against Apple's checksum.

    Apple does not document the digest, so accept a match on any of the common
    ones rather than guessing a single algorithm and rejecting good data.
    """
    candidate = expected.strip().lower()
    for algo in ("md5", "sha1", "sha256"):
        if hashlib.new(algo, raw).hexdigest() == candidate:
            return True
    return False


# ---------------------------------------------------------------------------
# Tenant-scoped reads
# ---------------------------------------------------------------------------


def owned_credential_ids(user_id: int):
    """Scalar subquery of ``asc_credentials.id`` owned by ``user_id``."""
    return (
        select(ASCCredential.id)
        .where(ASCCredential.user_id == user_id)
        .scalar_subquery()
    )


def window_cutoff(days: int) -> date:
    """Inclusive lower bound for a ``days``-long window ending today."""
    return date.today() - timedelta(days=days - 1)


def _scoped(user_id: int, app_id: int, report_kind: str, cutoff: date):
    return (
        AppAnalyticsDaily.app_id == app_id,
        AppAnalyticsDaily.report_kind == report_kind,
        AppAnalyticsDaily.date >= cutoff,
        AppAnalyticsDaily.credential_id.in_(owned_credential_ids(user_id)),
    )


async def engagement_rows(
    *,
    session: AsyncSession,
    user_id: int,
    app_id: int,
    days: int = 30,
    territory: str | None = None,
    source_type: str | None = None,
    page_title: str | None = None,
) -> tuple[date, list[dict[str, Any]]]:
    """Daily impressions / page views / taps for one app."""
    cutoff = window_cutoff(days)
    stmt = (
        select(
            AppAnalyticsDaily.date,
            func.sum(AppAnalyticsDaily.impressions).label("impressions"),
            func.sum(AppAnalyticsDaily.unique_impressions).label("unique_impressions"),
            func.sum(AppAnalyticsDaily.page_views).label("page_views"),
            func.sum(AppAnalyticsDaily.unique_page_views).label("unique_page_views"),
            func.sum(AppAnalyticsDaily.taps).label("taps"),
        )
        .where(*_scoped(user_id, app_id, KIND_ENGAGEMENT, cutoff))
        .group_by(AppAnalyticsDaily.date)
        .order_by(AppAnalyticsDaily.date.desc())
    )
    if territory:
        stmt = stmt.where(AppAnalyticsDaily.territory == territory.upper())
    if source_type:
        stmt = stmt.where(AppAnalyticsDaily.source_type == source_type)
    if page_title:
        stmt = stmt.where(AppAnalyticsDaily.page_title == page_title)

    rows = (await session.execute(stmt)).all()
    return cutoff, [
        {
            "date": r.date,
            "impressions": int(r.impressions or 0),
            "unique_impressions": int(r.unique_impressions or 0),
            "page_views": int(r.page_views or 0),
            "unique_page_views": int(r.unique_page_views or 0),
            "taps": int(r.taps or 0),
        }
        for r in rows
    ]


async def download_rows(
    *,
    session: AsyncSession,
    user_id: int,
    app_id: int,
    days: int = 30,
    territory: str | None = None,
) -> tuple[date, list[dict[str, Any]]]:
    """Daily downloads / redownloads for one app."""
    cutoff = window_cutoff(days)
    stmt = (
        select(
            AppAnalyticsDaily.date,
            func.sum(AppAnalyticsDaily.downloads).label("downloads"),
            func.sum(AppAnalyticsDaily.redownloads).label("redownloads"),
        )
        .where(*_scoped(user_id, app_id, KIND_DOWNLOADS, cutoff))
        .group_by(AppAnalyticsDaily.date)
        .order_by(AppAnalyticsDaily.date.desc())
    )
    if territory:
        stmt = stmt.where(AppAnalyticsDaily.territory == territory.upper())
    rows = (await session.execute(stmt)).all()
    return cutoff, [
        {
            "date": r.date,
            "downloads": int(r.downloads or 0),
            "redownloads": int(r.redownloads or 0),
        }
        for r in rows
    ]


def conversion_rate(downloads: int, page_views: int) -> Decimal | None:
    """Downloads per product page view, as a Decimal.

    Returns None when there are no page views — a 0/0 conversion rate is not
    "0%", it is "unknown", and rendering it as 0 would understate a new app.
    """
    if page_views <= 0:
        return None
    return (Decimal(downloads) / Decimal(page_views)).quantize(Decimal("0.0001"))


async def cpp_performance(
    *,
    session: AsyncSession,
    user_id: int,
    app_id: int,
    days: int = 30,
) -> tuple[date, list[dict[str, Any]]]:
    """Per-Custom-Product-Page rollup with a derived conversion rate.

    Engagement and downloads live on rows of different ``report_kind``, so they
    are summed separately and joined in Python on ``page_title`` — a SQL join here
    would multiply rows across the two grains.
    """
    cutoff = window_cutoff(days)

    eng_stmt = (
        select(
            AppAnalyticsDaily.page_title,
            func.sum(AppAnalyticsDaily.impressions).label("impressions"),
            func.sum(AppAnalyticsDaily.page_views).label("page_views"),
            func.sum(AppAnalyticsDaily.taps).label("taps"),
        )
        .where(*_scoped(user_id, app_id, KIND_ENGAGEMENT, cutoff))
        .group_by(AppAnalyticsDaily.page_title)
    )
    dl_stmt = (
        select(
            AppAnalyticsDaily.page_title,
            func.sum(AppAnalyticsDaily.downloads).label("downloads"),
        )
        .where(*_scoped(user_id, app_id, KIND_DOWNLOADS, cutoff))
        .group_by(AppAnalyticsDaily.page_title)
    )

    downloads_by_page = {
        r.page_title: int(r.downloads or 0)
        for r in (await session.execute(dl_stmt)).all()
    }

    out: list[dict[str, Any]] = []
    for r in (await session.execute(eng_stmt)).all():
        page_views = int(r.page_views or 0)
        downloads = downloads_by_page.get(r.page_title, 0)
        out.append(
            {
                # "" is the default product page, not a missing value.
                "page_title": r.page_title or None,
                "impressions": int(r.impressions or 0),
                "page_views": page_views,
                "taps": int(r.taps or 0),
                "downloads": downloads,
                "conversion_rate": conversion_rate(downloads, page_views),
            }
        )
    out.sort(key=lambda row: row["impressions"], reverse=True)
    return cutoff, out
