"""Pure-function listing audit: walk a metadata snapshot + tracked keywords
and emit a list of issues to fix.

This module does NOT read or write the database directly — the route layer
loads the inputs and feeds them in. Keeps the rules unit-testable without
fixtures.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

from app.models.metadata import AppMetadataLocalization
from app.services.metadata.validation import FIELD_CHAR_LIMITS, is_valid_url

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    locale: str | None  # None = global / cross-locale
    field: str | None
    code: str
    message: str
    suggestion: str | None = None


# A field is "underused" if it occupies < this fraction of its char limit.
# Underuse is informational, not a warning — short names can be intentional.
UNDERUSE_FRACTION = 0.5

# Approaching the cap is worth a warning so the operator can trim before
# Apple rejects on submit.
NEAR_LIMIT_FRACTION = 0.95


def _split_keywords(value: str | None) -> list[str]:
    if not value:
        return []
    return [k.strip().lower() for k in value.split(",") if k.strip()]


def _row_for(
    rows: Iterable[AppMetadataLocalization], kind: str, locale: str,
) -> AppMetadataLocalization | None:
    for r in rows:
        if r.kind == kind and r.locale == locale:
            return r
    return None


def audit(
    *,
    app_info: list[AppMetadataLocalization],
    versions: list[AppMetadataLocalization],
    tracked_coverage: list[tuple[str, str, str]] | None = None,
) -> list[Issue]:
    """Run every check and return the flat issue list.

    ``tracked_coverage`` is an optional list of ``(keyword, locale, placement)``
    triples (from the existing keyword-coverage endpoint). When supplied, we
    add a check for tracked keywords that aren't placed in any field of their
    locale.
    """
    issues: list[Issue] = []
    locales = sorted({r.locale for r in (*app_info, *versions)})

    if not locales:
        return [
            Issue(
                severity="error",
                locale=None,
                field=None,
                code="no-snapshot",
                message="No metadata snapshot available — sync from ASC first.",
                suggestion="POST /apps/{id}/metadata/sync",
            ),
        ]

    issues.extend(_check_per_locale_fields(locales, app_info, versions))
    issues.extend(_check_url_validity(app_info, versions))
    issues.extend(_check_keyword_quality(versions, app_info))
    issues.extend(_check_cross_locale_url_consistency(app_info, versions))
    if tracked_coverage:
        issues.extend(_check_unplaced_tracked_keywords(tracked_coverage))
    return issues


# ----------------------------------------------------------------------
# Per-locale field checks
# ----------------------------------------------------------------------


_REQUIRED_APP_INFO = ("name", "subtitle")
_REQUIRED_VERSION = ("description", "keywords")


def _check_per_locale_fields(
    locales: list[str],
    app_info: list[AppMetadataLocalization],
    versions: list[AppMetadataLocalization],
) -> list[Issue]:
    issues: list[Issue] = []
    for locale in locales:
        ai = _row_for(app_info, "app_info", locale)
        ver = _row_for(versions, "version", locale)

        for field in _REQUIRED_APP_INFO:
            value = getattr(ai, field, None) if ai else None
            issues.extend(_field_audit(locale, field, value))
        for field in _REQUIRED_VERSION:
            value = getattr(ver, field, None) if ver else None
            issues.extend(_field_audit(locale, field, value))

        # promotional_text is optional but worth a length check if present
        promo = getattr(ver, "promotional_text", None) if ver else None
        if promo:
            issues.extend(_field_audit(locale, "promotional_text", promo, optional=True))

    return issues


def _field_audit(
    locale: str, field: str, value: str | None, *, optional: bool = False,
) -> list[Issue]:
    out: list[Issue] = []
    limit = FIELD_CHAR_LIMITS.get(field)
    if limit is None:
        return out

    if not value:
        if not optional:
            out.append(
                Issue(
                    severity="error",
                    locale=locale,
                    field=field,
                    code="empty",
                    message=f"{field} is empty for {locale}.",
                    suggestion=(
                        "Use the metadata editor or 'Fix missing locales' to "
                        "translate from a source locale."
                    ),
                )
            )
        return out

    used = len(value)
    pct = used / limit
    if used > limit:
        out.append(
            Issue(
                severity="error",
                locale=locale,
                field=field,
                code="over-limit",
                message=(
                    f"{field} for {locale} is {used} chars; Apple's limit is {limit}."
                ),
                suggestion="Trim until under the cap; ASC will reject otherwise.",
            )
        )
    elif pct >= NEAR_LIMIT_FRACTION:
        out.append(
            Issue(
                severity="warning",
                locale=locale,
                field=field,
                code="near-limit",
                message=(
                    f"{field} for {locale} is {used}/{limit} chars "
                    f"({pct:.0%}). Edits may push it over."
                ),
                suggestion=None,
            )
        )
    elif pct < UNDERUSE_FRACTION and field in {"keywords", "subtitle", "name"}:
        out.append(
            Issue(
                severity="info",
                locale=locale,
                field=field,
                code="underused",
                message=(
                    f"{field} for {locale} uses only {used}/{limit} chars — "
                    "you may be leaving discoverability on the table."
                ),
                suggestion=None,
            )
        )
    return out


# ----------------------------------------------------------------------
# URL validity
# ----------------------------------------------------------------------


def _check_url_validity(
    app_info: list[AppMetadataLocalization],
    versions: list[AppMetadataLocalization],
) -> list[Issue]:
    out: list[Issue] = []
    for r in app_info:
        if r.privacy_policy_url and not is_valid_url(r.privacy_policy_url):
            out.append(
                Issue(
                    severity="error",
                    locale=r.locale,
                    field="privacy_policy_url",
                    code="bad-url",
                    message=f"privacy_policy_url for {r.locale} is malformed.",
                )
            )
    for r in versions:
        for field in ("marketing_url", "support_url"):
            value = getattr(r, field, None)
            if value and not is_valid_url(value):
                out.append(
                    Issue(
                        severity="error",
                        locale=r.locale,
                        field=field,
                        code="bad-url",
                        message=f"{field} for {r.locale} is malformed.",
                    )
                )
    return out


# ----------------------------------------------------------------------
# Keyword quality
# ----------------------------------------------------------------------


def _check_keyword_quality(
    versions: list[AppMetadataLocalization],
    app_info: list[AppMetadataLocalization],
) -> list[Issue]:
    """Flag keywords that:
    - duplicate within the keywords field itself
    - duplicate words already in name or subtitle (Apple already indexes those)
    """
    out: list[Issue] = []
    by_locale_ai = {r.locale: r for r in app_info}

    for ver in versions:
        kws = _split_keywords(ver.keywords)
        if not kws:
            continue

        counts = Counter(kws)
        for kw, n in counts.items():
            if n > 1:
                out.append(
                    Issue(
                        severity="warning",
                        locale=ver.locale,
                        field="keywords",
                        code="kw-duplicate",
                        message=(
                            f"Keyword '{kw}' appears {n}x in keywords for "
                            f"{ver.locale}."
                        ),
                        suggestion="Remove duplicates — Apple counts each only once.",
                    )
                )

        ai = by_locale_ai.get(ver.locale)
        if ai is None:
            continue
        name_words = set((ai.name or "").lower().split())
        subtitle_words = set((ai.subtitle or "").lower().split())
        already_indexed = name_words | subtitle_words
        for kw in counts:
            if kw in already_indexed:
                out.append(
                    Issue(
                        severity="info",
                        locale=ver.locale,
                        field="keywords",
                        code="kw-redundant",
                        message=(
                            f"Keyword '{kw}' is already in name/subtitle for "
                            f"{ver.locale}; the keywords slot is being wasted."
                        ),
                        suggestion=(
                            "Drop it from the keywords field and use the slot "
                            "for a new term."
                        ),
                    )
                )
    return out


# ----------------------------------------------------------------------
# Cross-locale URL consistency
# ----------------------------------------------------------------------


def _check_cross_locale_url_consistency(
    app_info: list[AppMetadataLocalization],
    versions: list[AppMetadataLocalization],
) -> list[Issue]:
    out: list[Issue] = []

    # Privacy URL: should be set on every locale that has app_info
    pp_locales_with = {r.locale for r in app_info if r.privacy_policy_url}
    pp_locales_without = {
        r.locale for r in app_info if not r.privacy_policy_url
    }
    if pp_locales_with and pp_locales_without:
        for loc in sorted(pp_locales_without):
            out.append(
                Issue(
                    severity="warning",
                    locale=loc,
                    field="privacy_policy_url",
                    code="pp-missing-here",
                    message=(
                        f"privacy_policy_url is set on other locales but missing "
                        f"on {loc}."
                    ),
                    suggestion="Copy from any filled locale (URLs need no translation).",
                )
            )

    # Same idea for marketing/support URLs on the version side
    for field in ("marketing_url", "support_url"):
        with_set = {r.locale for r in versions if getattr(r, field, None)}
        without = {r.locale for r in versions if not getattr(r, field, None)}
        if with_set and without:
            for loc in sorted(without):
                out.append(
                    Issue(
                        severity="info",
                        locale=loc,
                        field=field,
                        code=f"{field}-missing-here",
                        message=(
                            f"{field} is set on other locales but missing on {loc}."
                        ),
                        suggestion="Copy from any filled locale.",
                    )
                )
    return out


# ----------------------------------------------------------------------
# Unplaced tracked keywords
# ----------------------------------------------------------------------


def _check_unplaced_tracked_keywords(
    coverage: list[tuple[str, str, str]],
) -> list[Issue]:
    out: list[Issue] = []
    for keyword, locale, placement in coverage:
        if placement == "none":
            out.append(
                Issue(
                    severity="warning",
                    locale=locale,
                    field="keywords",
                    code="tracked-not-placed",
                    message=(
                        f"Tracked keyword '{keyword}' isn't placed in name, "
                        f"subtitle, or keywords for {locale}."
                    ),
                    suggestion=(
                        "If you care about ranking for this term, surface it in "
                        "one of those three fields."
                    ),
                )
            )
    return out
