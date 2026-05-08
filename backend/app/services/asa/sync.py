"""Sync orchestrator for the ASA vertical.

Pulls entities (orgs / campaigns / ad_groups / keywords / negatives /
search_terms — the latter implicitly via reports) and metrics
(performance reports at four grains) into the local DB. Idempotent
upserts; soft-deletes entities that disappeared from Apple. Tracks
per-step status in `ASASyncOperation` so partial failures are visible
and resumable.

The local-app ↔ ASA link is by `App.asc_app_id` == Apple's adam_id, which
is also the value denormalized into `ASAMetricDaily.app_adam_id`. When a
campaign references an adam_id we do not yet have a local App for, we
ingest with `app_id=NULL` (lazy-bind happens later when the user adds
that app via the ASC vertical).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App
from app.models.asa import (
    ASAAdGroup,
    ASACampaign,
    ASACredential,
    ASAKeyword,
    ASAMetricDaily,
    ASANegativeKeyword,
    ASAOrg,
    ASASearchTerm,
    ASASyncOperation,
)
from app.services.asa import campaigns as asa_campaigns
from app.services.asa import reports as asa_reports
from app.services.asa.client import ASAClient

logger = logging.getLogger(__name__)


def _dialect_insert(session: AsyncSession):
    """Pick the right dialect-specific insert for ON CONFLICT support."""
    name = session.bind.dialect.name if session.bind else "sqlite"
    return sqlite_insert if name == "sqlite" else pg_insert


async def _upsert_one(
    session: AsyncSession,
    table,
    values: dict,
    index_elements: list[str],
) -> None:
    insert = _dialect_insert(session)
    stmt = insert(table).values(values)
    update_cols = {
        c: getattr(stmt.excluded, c)
        for c in values.keys()
        if c not in index_elements
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_=update_cols,
    )
    await session.execute(stmt)


def _start_date(cred: ASACredential, full_backfill: bool) -> date:
    today = date.today()
    earliest = today - timedelta(days=90)
    if full_backfill or cred.last_synced_at is None:
        return earliest
    return max(cred.last_synced_at.date() - timedelta(days=1), earliest)


def _record_metric(
    *,
    dim_kind: str,
    dim_id: int,
    app_adam_id: str,
    granularity_row: dict[str, Any],
) -> dict[str, Any]:
    """Build one ASAMetricDaily upsert dict from a granularity entry."""
    return {
        "dim_kind": dim_kind,
        "dim_id": dim_id,
        "app_adam_id": app_adam_id,
        "date": date.fromisoformat(granularity_row["date"]),
        "storefront": granularity_row.get("countryOrRegion"),
        "impressions": int(granularity_row.get("impressions") or 0),
        "taps": int(granularity_row.get("taps") or 0),
        "installs": int(granularity_row.get("installs") or 0),
        "new_downloads": int(granularity_row.get("newDownloads") or 0),
        "redownloads": int(granularity_row.get("redownloads") or 0),
        "spend_amount": float((granularity_row.get("localSpend") or {}).get("amount") or 0),
        "spend_currency": (granularity_row.get("localSpend") or {}).get("currency") or "USD",
        "avg_cpa_amount": (granularity_row.get("avgCPA") or {}).get("amount"),
        "avg_cpt_amount": (granularity_row.get("avgCPT") or {}).get("amount"),
        "ttr": granularity_row.get("ttr"),
        "conversion_rate": granularity_row.get("conversionRate"),
    }


async def _upsert_metrics(
    session: AsyncSession, rows: list[dict[str, Any]],
) -> int:
    """Bulk-upsert metric rows. Returns count written.

    Chunks at 500 to bound statement size on PG.
    """
    if not rows:
        return 0
    insert = _dialect_insert(session)
    written = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = insert(ASAMetricDaily.__table__).values(chunk)
        update_cols = {
            c: getattr(stmt.excluded, c)
            for c in chunk[0].keys()
            if c not in {"dim_kind", "dim_id", "date", "storefront"}
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["dim_kind", "dim_id", "date", "storefront"],
            set_=update_cols,
        )
        await session.execute(stmt)
        written += len(chunk)
    return written


async def run_sync(
    *,
    session: AsyncSession,
    credential_id: int,
    user_id: int,
    full_backfill: bool = False,
) -> ASASyncOperation:
    """Execute one sync run; return the populated ASASyncOperation row."""
    cred = (
        await session.execute(
            select(ASACredential).where(
                ASACredential.id == credential_id,
                ASACredential.user_id == user_id,
            )
        )
    ).scalar_one()

    op = ASASyncOperation(
        credential_id=cred.id,
        user_id=user_id,
        status="running",
        full_backfill=full_backfill,
        steps=[],
        error_log=[],
        started_at=datetime.now(timezone.utc),
    )
    session.add(op)
    await session.flush()

    steps: list[dict] = []
    errors: list[str] = []

    client = await ASAClient.from_credential(cred)
    try:
        # ---- step: orgs ----
        steps.append({"name": "orgs", "status": "running"})
        try:
            orgs_data = await asa_campaigns.list_orgs_for_credential(client)
            for o in orgs_data:
                attrs = o.get("attributes", o)
                await _upsert_one(
                    session, ASAOrg.__table__,
                    {
                        "credential_id": cred.id,
                        "asa_org_id": o.get("orgId") or attrs.get("orgId"),
                        "name": attrs.get("orgName") or attrs.get("name", ""),
                        "currency": attrs.get("currency") or "USD",
                        "timezone": (
                            attrs.get("timeZone") or attrs.get("timezone") or "UTC"
                        ),
                        "role": (
                            (attrs.get("roleNames") or [None])[0]
                            if isinstance(attrs.get("roleNames"), list)
                            else None
                        ),
                    },
                    index_elements=["credential_id", "asa_org_id"],
                )
            await session.flush()
            steps[-1]["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            steps[-1]["status"] = "failed"
            steps[-1]["detail"] = str(exc)
            errors.append(f"orgs: {exc}")

        orgs = (
            await session.execute(
                select(ASAOrg).where(ASAOrg.credential_id == cred.id)
            )
        ).scalars().all()

        # ---- per-org: campaigns / ad_groups / keywords / negatives ----
        for org in orgs:
            steps.append({
                "name": f"org_{org.asa_org_id}_entities",
                "status": "running",
            })
            try:
                camps = await asa_campaigns.list_campaigns(
                    client, org_id=org.asa_org_id,
                )
                seen_camp_asa_ids: set[int] = set()
                for c in camps:
                    seen_camp_asa_ids.add(c["id"])
                    adam_id = str(
                        c.get("adamId")
                        or (c.get("app") or {}).get("adamId")
                        or ""
                    )
                    local_app = (
                        await session.execute(
                            select(App).where(App.asc_app_id == adam_id)
                        )
                    ).scalar_one_or_none()
                    await _upsert_one(
                        session, ASACampaign.__table__,
                        {
                            "org_id": org.id,
                            "asa_campaign_id": c["id"],
                            "app_id": local_app.id if local_app else None,
                            "app_adam_id": adam_id,
                            "name": c.get("name") or "",
                            "status": c.get("status") or "ENABLED",
                            "supply_sources": c.get("supplySources"),
                            "daily_budget_amount": (
                                (c.get("dailyBudgetAmount") or {}).get("amount")
                            ),
                            "daily_budget_currency": (
                                (c.get("dailyBudgetAmount") or {}).get("currency")
                            ),
                            "storefronts": c.get("countriesOrRegions"),
                            "archived_at": None,
                        },
                        index_elements=["org_id", "asa_campaign_id"],
                    )
                # archive missing campaigns
                local_camps = (
                    await session.execute(
                        select(ASACampaign).where(ASACampaign.org_id == org.id)
                    )
                ).scalars().all()
                for lc in local_camps:
                    if (
                        lc.asa_campaign_id not in seen_camp_asa_ids
                        and lc.archived_at is None
                    ):
                        lc.archived_at = datetime.now(timezone.utc)

                # ad groups + keywords + negatives per non-archived campaign
                for camp in [c for c in local_camps if c.archived_at is None]:
                    ags_data = await asa_campaigns.list_ad_groups(
                        client,
                        org_id=org.asa_org_id,
                        campaign_id=camp.asa_campaign_id,
                    )
                    seen_ag_asa_ids: set[int] = set()
                    for ag in ags_data:
                        seen_ag_asa_ids.add(ag["id"])
                        await _upsert_one(
                            session, ASAAdGroup.__table__,
                            {
                                "campaign_id": camp.id,
                                "asa_ad_group_id": ag["id"],
                                "name": ag.get("name") or "",
                                "status": ag.get("status") or "ENABLED",
                                "default_bid_amount": (
                                    (ag.get("defaultBidAmount") or {}).get("amount")
                                ),
                                "default_bid_currency": (
                                    (ag.get("defaultBidAmount") or {}).get("currency")
                                ),
                                "age_range": ag.get("automatedKeywordsOptIn"),
                                "gender": ag.get("gender"),
                                "device_class": ag.get("deviceClass"),
                                "archived_at": None,
                            },
                            index_elements=["campaign_id", "asa_ad_group_id"],
                        )
                    local_ags = (
                        await session.execute(
                            select(ASAAdGroup).where(
                                ASAAdGroup.campaign_id == camp.id
                            )
                        )
                    ).scalars().all()
                    for lag in local_ags:
                        if (
                            lag.asa_ad_group_id not in seen_ag_asa_ids
                            and lag.archived_at is None
                        ):
                            lag.archived_at = datetime.now(timezone.utc)

                    # keywords + negatives per non-archived ad group
                    for ag in [a for a in local_ags if a.archived_at is None]:
                        kws = await asa_campaigns.list_targeting_keywords(
                            client,
                            org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id,
                        )
                        for kw in kws:
                            await _upsert_one(
                                session, ASAKeyword.__table__,
                                {
                                    "ad_group_id": ag.id,
                                    "asa_keyword_id": kw["id"],
                                    "text": kw.get("text") or "",
                                    "match_type": kw.get("matchType") or "BROAD",
                                    "bid_amount": (
                                        (kw.get("bidAmount") or {}).get("amount")
                                    ),
                                    "bid_currency": (
                                        (kw.get("bidAmount") or {}).get("currency")
                                    ),
                                    "status": kw.get("status") or "ENABLED",
                                    "archived_at": None,
                                },
                                index_elements=["ad_group_id", "asa_keyword_id"],
                            )

                        # negatives at ad-group level
                        ag_negs = (
                            await asa_campaigns.list_negative_keywords_ad_group(
                                client,
                                org_id=org.asa_org_id,
                                campaign_id=camp.asa_campaign_id,
                                ad_group_id=ag.asa_ad_group_id,
                            )
                        )
                        for n in ag_negs:
                            await _upsert_one(
                                session, ASANegativeKeyword.__table__,
                                {
                                    "ad_group_id": ag.id,
                                    "campaign_id": None,
                                    "asa_negative_keyword_id": n["id"],
                                    "text": n.get("text") or "",
                                    "match_type": n.get("matchType") or "EXACT",
                                },
                                index_elements=["asa_negative_keyword_id"],
                            )

                    # negatives at campaign level
                    camp_negs = await asa_campaigns.list_negative_keywords_campaign(
                        client,
                        org_id=org.asa_org_id,
                        campaign_id=camp.asa_campaign_id,
                    )
                    for n in camp_negs:
                        await _upsert_one(
                            session, ASANegativeKeyword.__table__,
                            {
                                "ad_group_id": None,
                                "campaign_id": camp.id,
                                "asa_negative_keyword_id": n["id"],
                                "text": n.get("text") or "",
                                "match_type": n.get("matchType") or "EXACT",
                            },
                            index_elements=["asa_negative_keyword_id"],
                        )

                steps[-1]["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                steps[-1]["status"] = "failed"
                steps[-1]["detail"] = str(exc)
                errors.append(f"org {org.asa_org_id} entities: {exc}")

        # ---- step: metrics ----
        steps.append({"name": "metrics", "status": "running"})
        start = _start_date(cred, full_backfill)
        end = date.today()
        metrics_total = 0

        for org in orgs:
            local_camps = (
                await session.execute(
                    select(ASACampaign).where(
                        ASACampaign.org_id == org.id,
                        ASACampaign.archived_at.is_(None),
                    )
                )
            ).scalars().all()
            try:
                # campaign-level
                rows = await asa_reports.campaign_report(
                    client, org_id=org.asa_org_id, start=start, end=end,
                )
                payload: list[dict] = []
                for r in rows:
                    meta = r.get("metadata") or {}
                    asa_camp_id = meta.get("campaignId") or meta.get("id")
                    local = next(
                        (c for c in local_camps if c.asa_campaign_id == asa_camp_id),
                        None,
                    )
                    if local is None:
                        continue
                    for gp in (r.get("granularity") or []):
                        payload.append(_record_metric(
                            dim_kind="CAMPAIGN",
                            dim_id=local.id,
                            app_adam_id=local.app_adam_id,
                            granularity_row=gp,
                        ))
                metrics_total += await _upsert_metrics(session, payload)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"campaign report org={org.asa_org_id}: {exc}")

            for camp in local_camps:
                local_ags = (
                    await session.execute(
                        select(ASAAdGroup).where(
                            ASAAdGroup.campaign_id == camp.id
                        )
                    )
                ).scalars().all()
                # ad-group-level
                try:
                    rows = await asa_reports.ad_group_report(
                        client,
                        org_id=org.asa_org_id,
                        campaign_id=camp.asa_campaign_id,
                        start=start, end=end,
                    )
                    payload = []
                    for r in rows:
                        meta = r.get("metadata") or {}
                        asa_ag_id = meta.get("adGroupId") or meta.get("id")
                        local = next(
                            (a for a in local_ags if a.asa_ad_group_id == asa_ag_id),
                            None,
                        )
                        if local is None:
                            continue
                        for gp in (r.get("granularity") or []):
                            payload.append(_record_metric(
                                dim_kind="AD_GROUP",
                                dim_id=local.id,
                                app_adam_id=camp.app_adam_id,
                                granularity_row=gp,
                            ))
                    metrics_total += await _upsert_metrics(session, payload)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"ad_group report camp={camp.asa_campaign_id}: {exc}",
                    )

                # keyword + search-term reports per ad group
                for ag in [a for a in local_ags if a.archived_at is None]:
                    try:
                        krs = await asa_reports.keyword_report(
                            client,
                            org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id,
                            start=start, end=end,
                        )
                        local_kws = (
                            await session.execute(
                                select(ASAKeyword).where(
                                    ASAKeyword.ad_group_id == ag.id
                                )
                            )
                        ).scalars().all()
                        payload = []
                        for r in krs:
                            meta = r.get("metadata") or {}
                            asa_kw_id = meta.get("keywordId") or meta.get("id")
                            local = next(
                                (k for k in local_kws if k.asa_keyword_id == asa_kw_id),
                                None,
                            )
                            if local is None:
                                continue
                            for gp in (r.get("granularity") or []):
                                payload.append(_record_metric(
                                    dim_kind="KEYWORD",
                                    dim_id=local.id,
                                    app_adam_id=camp.app_adam_id,
                                    granularity_row=gp,
                                ))
                        metrics_total += await _upsert_metrics(session, payload)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(
                            f"keyword report ag={ag.asa_ad_group_id}: {exc}",
                        )

                    try:
                        srs = await asa_reports.search_term_report(
                            client,
                            org_id=org.asa_org_id,
                            campaign_id=camp.asa_campaign_id,
                            ad_group_id=ag.asa_ad_group_id,
                            start=start, end=end,
                        )
                        for r in srs:
                            meta = r.get("metadata") or {}
                            text = meta.get("searchTermText") or ""
                            match = meta.get("searchTermType") or "BROAD"
                            if not text:
                                continue
                            existing = (
                                await session.execute(
                                    select(ASASearchTerm).where(
                                        ASASearchTerm.ad_group_id == ag.id,
                                        ASASearchTerm.text == text,
                                        ASASearchTerm.match_type == match,
                                    )
                                )
                            ).scalar_one_or_none()
                            if existing is None:
                                existing = ASASearchTerm(
                                    ad_group_id=ag.id,
                                    text=text,
                                    match_type=match,
                                    source="SEARCHTERM",
                                )
                                session.add(existing)
                                await session.flush()
                            payload = [
                                _record_metric(
                                    dim_kind="SEARCH_TERM",
                                    dim_id=existing.id,
                                    app_adam_id=camp.app_adam_id,
                                    granularity_row=gp,
                                )
                                for gp in (r.get("granularity") or [])
                            ]
                            metrics_total += await _upsert_metrics(session, payload)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(
                            f"search term report ag={ag.asa_ad_group_id}: {exc}",
                        )

        steps[-1]["status"] = "done"
        steps[-1]["detail"] = f"{metrics_total} metric rows upserted"

        cred.last_synced_at = datetime.now(timezone.utc)
        op.completed_at = datetime.now(timezone.utc)
        op.status = "partial" if errors else "done"

    except Exception as exc:  # noqa: BLE001 — top-level fatal
        op.status = "failed"
        errors.append(f"fatal: {exc}")
    finally:
        await client.aclose()
        op.steps = steps
        op.error_log = errors
        await session.flush()

    return op
