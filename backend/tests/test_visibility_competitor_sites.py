"""Tests for the competitor developer-sites export.

Covers:
  * ``collect_competitor_sites`` — enrichment from a MOCKED iTunes lookup:
    de-dup across watches, keyword aggregation, sort-by-name, website mapping,
    and the empty-website fallback for apps with no ``sellerUrl``.
  * REST ``GET /visibility/competitors`` — returns items for an owned app and
    is ownership-gated (foreign app → 403/404; foreign ``watch_id`` → 404).
  * Empty case — watches but no snapshots → ``items: []`` and NO iTunes call.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.visibility import competitor_sites
from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.models.visibility import (
    KeywordVisibilityResult,
    KeywordVisibilitySnapshot,
    KeywordVisibilityWatch,
)
from app.services.visibility import competitors as competitors_module
from app.services.visibility.competitors import collect_competitor_sites


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_user_credential_app(*, asc_app_id: str = "adam-comp") -> dict[str, int]:
    """Seed one user → credential → app, returning their ids."""
    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        user = User(
            email=f"comp-{suffix}@example.com",
            password_hash=hash_password("password-123"),
            name="Comp Owner",
        )
        session.add(user)
        await session.flush()

        credential = ASCCredential(
            user_id=user.id,
            name="ASC",
            issuer_id=f"iss-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted=encrypt_value("fixture-private-key"),
        )
        session.add(credential)
        await session.flush()

        app = App(
            credential_id=credential.id,
            asc_app_id=asc_app_id,
            bundle_id=f"com.example.comp.{suffix}",
            name="Comp App",
            platform="ios",
        )
        session.add(app)
        await session.commit()
        return {
            "user_id": user.id,
            "credential_id": credential.id,
            "app_id": app.id,
        }


async def _add_watch_with_results(
    app_id: int,
    text: str,
    country: str,
    results: list[tuple[str, str]],
    *,
    with_snapshot: bool = True,
) -> int:
    """Add a watch and (optionally) one snapshot with ``(track_id, name)``
    result rows. Returns the watch id."""
    async with async_session_factory() as session:
        watch = KeywordVisibilityWatch(app_id=app_id, text=text, country=country)
        session.add(watch)
        await session.flush()

        if with_snapshot:
            snapshot = KeywordVisibilitySnapshot(
                watch_id=watch.id, results_count=len(results),
            )
            session.add(snapshot)
            await session.flush()
            for pos, (track_id, name) in enumerate(results, start=1):
                session.add(
                    KeywordVisibilityResult(
                        snapshot_id=snapshot.id,
                        position=pos,
                        track_id=track_id,
                        name=name,
                        bundle_id=f"com.x.{track_id}",
                        icon_url=f"https://icon/{track_id}.png",
                    )
                )
        await session.commit()
        return watch.id


class _FakeITunes:
    """Records the track ids it was asked to look up and returns canned rows.

    ``records_by_id`` maps a track id to the raw iTunes lookup dict to return;
    ids missing from the map are simply omitted (modelling iTunes "not found").
    """

    def __init__(self, records_by_id: dict[str, dict[str, Any]]) -> None:
        self.records_by_id = records_by_id
        self.calls: list[list[str]] = []

    async def lookup_apps(
        self, track_ids: list[str], country: str = "us", *, client: Any = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(list(track_ids))
        return [
            self.records_by_id[t]
            for t in track_ids
            if t in self.records_by_id
        ]


# ---------------------------------------------------------------------------
# Service: collect_competitor_sites
# ---------------------------------------------------------------------------


def test_collect_competitor_sites_dedup_and_enrichment():
    """Two watches share one app and each has a unique app. The shared app is
    de-duped, its keywords aggregated, rows sorted by name, websites mapped,
    and an app without ``sellerUrl`` falls back to an empty website."""

    async def go() -> list[dict[str, Any]]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        app_id = seeded["app_id"]

        # Shared app "100" appears in both watches; "200" only in watch A,
        # "300" only in watch B. "300" has no sellerUrl (empty-website case).
        await _add_watch_with_results(
            app_id, "coffee", "us",
            [("100", "Zephyr"), ("200", "Apex")],
        )
        await _add_watch_with_results(
            app_id, "tea", "gb",
            [("100", "Zephyr"), ("300", "Mid")],
        )

        fake = _FakeITunes(
            {
                "100": {
                    "trackId": 100,
                    "trackName": "Zephyr",
                    "sellerName": "Zephyr Inc",
                    "sellerUrl": "https://zephyr.example",
                    "trackViewUrl": "https://apps.apple.com/app/id100",
                },
                "200": {
                    "trackId": 200,
                    "trackName": "Apex",
                    "sellerName": "Apex LLC",
                    "sellerUrl": "https://apex.example",
                    "trackViewUrl": "https://apps.apple.com/app/id200",
                },
                "300": {
                    "trackId": 300,
                    "trackName": "Mid",
                    "sellerName": "Mid Co",
                    # no sellerUrl
                    "trackViewUrl": "https://apps.apple.com/app/id300",
                },
            }
        )

        async with async_session_factory() as session:
            stmt = select(KeywordVisibilityWatch).where(
                KeywordVisibilityWatch.app_id == app_id,
            )
            watches = list((await session.execute(stmt)).scalars().all())
            return await collect_competitor_sites(
                session, watches=watches, itunes=fake,
            )

    rows = asyncio.run(go())

    # Three distinct apps (100 de-duped to one row).
    assert [r["track_id"] for r in rows] == ["200", "300", "100"]  # by name
    # Sorted by name case-insensitively: Apex, Mid, Zephyr.
    assert [r["name"] for r in rows] == ["Apex", "Mid", "Zephyr"]

    by_id = {r["track_id"]: r for r in rows}
    # Shared app aggregates both watch labels.
    assert by_id["100"]["keywords"] == ["coffee (US)", "tea (GB)"]
    assert by_id["100"]["website"] == "https://zephyr.example"
    assert by_id["100"]["seller"] == "Zephyr Inc"
    assert by_id["100"]["app_store_url"] == "https://apps.apple.com/app/id100"
    # Unique apps carry only their own watch.
    assert by_id["200"]["keywords"] == ["coffee (US)"]
    assert by_id["300"]["keywords"] == ["tea (GB)"]
    # Empty-website fallback: no sellerUrl → "" but App Store URL is present.
    assert by_id["300"]["website"] == ""
    assert by_id["300"]["app_store_url"] == "https://apps.apple.com/app/id300"


def test_collect_competitor_sites_empty_when_no_snapshots():
    """A watch with no snapshot yields no rows and makes NO iTunes call."""

    async def go() -> tuple[list[dict[str, Any]], int]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        app_id = seeded["app_id"]
        await _add_watch_with_results(
            app_id, "lonely", "us", [], with_snapshot=False,
        )

        fake = _FakeITunes({})
        async with async_session_factory() as session:
            stmt = select(KeywordVisibilityWatch).where(
                KeywordVisibilityWatch.app_id == app_id,
            )
            watches = list((await session.execute(stmt)).scalars().all())
            rows = await collect_competitor_sites(
                session, watches=watches, itunes=fake,
            )
        return rows, len(fake.calls)

    rows, call_count = asyncio.run(go())
    assert rows == []
    assert call_count == 0


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_competitor_sites_rest_returns_items(monkeypatch):
    """The endpoint returns enriched rows for an owned app."""

    async def go() -> list[Any]:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        app_id = seeded["app_id"]
        await _add_watch_with_results(
            app_id, "coffee", "us", [("100", "Zephyr")],
        )

        fake = _FakeITunes(
            {
                "100": {
                    "trackId": 100,
                    "trackName": "Zephyr",
                    "sellerName": "Zephyr Inc",
                    "sellerUrl": "https://zephyr.example",
                    "trackViewUrl": "https://apps.apple.com/app/id100",
                },
            }
        )
        # Patch the service-level constructor so the endpoint's default
        # ITunesSearchService() is replaced with our fake.
        monkeypatch.setattr(
            competitors_module, "ITunesSearchService", lambda: fake,
        )

        async with async_session_factory() as session:
            result = await competitor_sites(
                app_id=app_id,
                watch_id=None,
                current_user={"user_id": str(seeded["user_id"])},
                session=session,
            )
        return result.items

    items = asyncio.run(go())
    assert len(items) == 1
    assert items[0].track_id == "100"
    assert items[0].website == "https://zephyr.example"
    assert items[0].keywords == ["coffee (US)"]


def test_competitor_sites_rest_rejects_foreign_app():
    """An app owned by a different user is not visible (403/404)."""

    async def go() -> int:
        await _ensure_schema()
        owner = await _seed_user_credential_app()
        intruder = await _seed_user_credential_app()
        async with async_session_factory() as session:
            try:
                await competitor_sites(
                    app_id=owner["app_id"],
                    watch_id=None,
                    current_user={"user_id": str(intruder["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code
        return 0

    status_code = asyncio.run(go())
    assert status_code in (403, 404), status_code


def test_competitor_sites_rest_rejects_foreign_watch_id():
    """A watch_id belonging to another app yields a 404."""

    async def go() -> int:
        await _ensure_schema()
        seeded = await _seed_user_credential_app()
        other = await _seed_user_credential_app()
        # Watch lives under `other`, not under `seeded`.
        foreign_watch = await _add_watch_with_results(
            other["app_id"], "coffee", "us", [("100", "Zephyr")],
        )
        async with async_session_factory() as session:
            try:
                await competitor_sites(
                    app_id=seeded["app_id"],
                    watch_id=foreign_watch,
                    current_user={"user_id": str(seeded["user_id"])},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code
        return 0

    status_code = asyncio.run(go())
    assert status_code == 404, status_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
