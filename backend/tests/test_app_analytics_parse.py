"""Segment parsing, against Apple's real report format.

These fixtures are the actual columns and cell values from live segments
(tenant 7bfdb99d, 2026-08-29) -- not an invented shape. That matters: the first
version of this parser assumed wide metric columns ("Impressions",
"Product Page Views") and parsed *zero* rows from real data, while its tests
passed happily against the invented format.

Apple ships a LONG format. The metric name is a cell value in ``Event``
(engagement) or ``Download Type`` (downloads); ``Counts`` carries the number.
"""
from __future__ import annotations

import gzip
from decimal import Decimal

from app.models.analytics import KIND_DOWNLOADS, KIND_ENGAGEMENT
from app.services.analytics import conversion_rate, parse_segment

ENGAGEMENT_HEADER = (
    "Date\tApp Name\tApp Apple Identifier\tEvent\tPage Type\tPage Title\t"
    "Source Type\tSource Info\tCampaign\tEngagement Type\tDevice\t"
    "Platform Version\tTerritory\tCounts\tUnique Counts"
)

DOWNLOADS_HEADER = (
    "Date\tApp Name\tApp Apple Identifier\tDownload Type\tApp Version\tDevice\t"
    "Platform Version\tSource Type\tSource Info\tCampaign\tPage Type\t"
    "Page Title\tPre-Order\tTerritory\tCounts"
)


def _gz(header: str, *rows: str) -> bytes:
    return gzip.compress(("\n".join([header, *rows]) + "\n").encode("utf-8"))


def _eng(event, counts, unique, *, page_title="No page", page_type="No page",
         territory="MX", device="iPhone", platform="iOS 26.6", date="2026-08-24"):
    return (
        f"{date}\tRefresher\t6759679041\t{event}\t{page_type}\t{page_title}\t"
        f"App Store search\t\t\t\t{device}\t{platform}\t{territory}\t"
        f"{counts}\t{unique}"
    )


def _dl(dl_type, counts, *, date="2026-08-24", version="1.2", territory="MX",
        page_title="No page"):
    return (
        f"{date}\tRefresher\t6759679041\t{dl_type}\t{version}\tiPhone\t"
        f"iOS 26.6\tApp Store search\t\t\tNo page\t{page_title}\t\t"
        f"{territory}\t{counts}"
    )


def test_pivots_event_column_into_metric_columns() -> None:
    """Impression / Page view / Tap on one grain merge into a single row."""
    rows = parse_segment(
        _gz(
            ENGAGEMENT_HEADER,
            _eng("Impression", 100, 90),
            _eng("Page view", 20, 18),
            _eng("Tap", 5, 5),
        ),
        report_kind=KIND_ENGAGEMENT,
    )
    assert len(rows) == 1, "same grain must merge, not produce three rows"
    row = rows[0]
    assert row["impressions"] == 100
    assert row["unique_impressions"] == 90
    assert row["page_views"] == 20
    assert row["unique_page_views"] == 18
    assert row["taps"] == 5


def test_aggregates_over_platform_version() -> None:
    """Platform Version is not a dimension, so its rows must SUM.

    This is the one that would corrupt data silently: the DB write is an
    upsert on the grain, so if these were emitted as separate rows the last
    one would overwrite the first instead of adding to it -- reporting 40
    impressions where there were 140.
    """
    rows = parse_segment(
        _gz(
            ENGAGEMENT_HEADER,
            _eng("Impression", 100, 90, platform="iOS 26.6"),
            _eng("Impression", 40, 35, platform="iOS 18.2"),
        ),
        report_kind=KIND_ENGAGEMENT,
    )
    assert len(rows) == 1
    assert rows[0]["impressions"] == 140, "platform versions must sum, not overwrite"
    assert rows[0]["unique_impressions"] == 125


def test_distinct_dimensions_stay_separate() -> None:
    rows = parse_segment(
        _gz(
            ENGAGEMENT_HEADER,
            _eng("Impression", 100, 90, territory="MX"),
            _eng("Impression", 40, 35, territory="US"),
        ),
        report_kind=KIND_ENGAGEMENT,
    )
    assert len(rows) == 2
    assert {r["territory"] for r in rows} == {"MX", "US"}


def test_custom_product_page_comes_from_page_title() -> None:
    """Apple identifies the product page by title, not by a CPP id."""
    rows = parse_segment(
        _gz(
            ENGAGEMENT_HEADER,
            _eng("Page view", 12, 11,
                 page_title="Refresher — Calm in 2 minutes",
                 page_type="Product page"),
        ),
        report_kind=KIND_ENGAGEMENT,
    )
    assert rows[0]["page_title"] == "Refresher — Calm in 2 minutes"
    assert rows[0]["page_type"] == "Product page"


def test_updates_are_not_counted_as_downloads() -> None:
    """Auto-update / Manual update are not downloads.

    On the real segment these outnumbered first-time downloads 17 to 4.
    Counting them would report 21 downloads where there were 4, and inflate
    every conversion rate derived from them.
    """
    rows = parse_segment(
        _gz(
            DOWNLOADS_HEADER,
            _dl("Auto-update", 16),
            _dl("Manual update", 1),
            _dl("First-time download", 4),
        ),
        report_kind=KIND_DOWNLOADS,
    )
    assert len(rows) == 1
    assert rows[0]["downloads"] == 4, "updates leaked into the download count"
    assert rows[0]["redownloads"] == 0


def test_redownloads_tracked_separately() -> None:
    rows = parse_segment(
        _gz(DOWNLOADS_HEADER, _dl("Redownload", 7), _dl("First-time download", 3)),
        report_kind=KIND_DOWNLOADS,
    )
    assert len(rows) == 1
    assert rows[0]["downloads"] == 3
    assert rows[0]["redownloads"] == 7


def test_skips_rows_without_a_date() -> None:
    rows = parse_segment(
        _gz(
            ENGAGEMENT_HEADER,
            _eng("Impression", 10, 9),
            "\tTotals\t\tImpression\t\t\t\t\t\t\t\t\t\t9999\t9999",
        ),
        report_kind=KIND_ENGAGEMENT,
    )
    assert len(rows) == 1
    assert rows[0]["impressions"] == 10


def test_skips_unknown_event_types() -> None:
    """An event we don't map must be dropped, not silently counted as zero."""
    rows = parse_segment(
        _gz(ENGAGEMENT_HEADER, _eng("Some Future Event", 500, 400)),
        report_kind=KIND_ENGAGEMENT,
    )
    assert rows == []


def test_suppressed_cells_and_separators() -> None:
    rows = parse_segment(
        _gz(ENGAGEMENT_HEADER, _eng("Impression", "1,234", "-")),
        report_kind=KIND_ENGAGEMENT,
    )
    assert rows[0]["impressions"] == 1234
    assert rows[0]["unique_impressions"] == 0


def test_missing_dimensions_are_empty_string_not_none() -> None:
    """The sentinel is load-bearing: a None here would break dedupe."""
    rows = parse_segment(
        _gz(ENGAGEMENT_HEADER, _eng("Impression", 10, 9, territory="", device="")),
        report_kind=KIND_ENGAGEMENT,
    )
    assert rows[0]["territory"] == ""
    assert rows[0]["device"] == ""
    assert all(v is not None for v in rows[0].values())


def test_conversion_rate_is_decimal() -> None:
    rate = conversion_rate(downloads=25, page_views=100)
    assert isinstance(rate, Decimal)
    assert rate == Decimal("0.2500")


def test_conversion_rate_unknown_when_no_page_views() -> None:
    """0/0 is 'unknown', not '0%'."""
    assert conversion_rate(downloads=0, page_views=0) is None
    assert conversion_rate(downloads=5, page_views=0) is None
