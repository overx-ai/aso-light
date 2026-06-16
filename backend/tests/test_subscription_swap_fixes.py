"""Tests for the subscription/IAP + product-swap /code-pass fixes.

Covers:
  * B-I1 — child-resource IDOR: a localization / intro-offer id that is
    not a member of the verified parent must 404 (REST) / ToolError (MCP),
    even when the parent the caller authorized against is owned by them.
  * A-I1 — swap ``rc_swap_ok`` reflects ASC health: when the ASC archive
    fails but the RC swap succeeds, ``rc_swap_ok`` is False and the iOS
    checklist warns instead of claiming "no iOS change required".
  * A-I2 / A-I3 — archive-incomplete / IAP-still-live warnings surface.
  * B-I3 — ``set_iap_price`` uses the requested base territory (not the
    hardcoded USA) and that territory is present in ``manualPrices``.
  * B-M5 — an intro-offer payload with a missing/blank offerMode parses
    without a 500 ResponseValidationError.
"""

from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401,E402

from fastapi import HTTPException  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from _async_harness import run_async  # noqa: E402


# ----------------------------------------------------------------------
# Fake ASC client + context manager
# ----------------------------------------------------------------------


class _FakeASCClient:
    """Stub ASCClient exposing only the methods the pricing service calls.

    Acts as its own async context manager so the handler's
    ``async with await _get_asc_client_for_app(...) as client`` works.
    Records DELETE/PATCH calls so a test can assert a mutation never ran
    when the membership check should have short-circuited it.
    """

    def __init__(
        self,
        *,
        localizations_by_sub: dict[str, list[dict]] | None = None,
        intro_offers_by_sub: dict[str, dict] | None = None,
        group_localizations: list[dict] | None = None,
    ) -> None:
        # Keyed by the ASC subscription id so the fake mirrors Apple's
        # per-parent scoping: each parent only lists its OWN children.
        self._localizations_by_sub = localizations_by_sub or {}
        self._intro_offers_by_sub = intro_offers_by_sub or {}
        self._group_localizations = group_localizations or []
        self.deleted: list[str] = []
        self.patched: list[tuple[str, dict | None]] = []

    @staticmethod
    def _sub_id_from_path(path: str) -> str:
        # ``/subscriptions/{id}/...`` -> {id}
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "subscriptions":
            return parts[1]
        return ""

    async def _get_all_pages(self, path: str, params=None) -> list[dict]:
        if "subscriptionGroupLocalizations" in path:
            return self._group_localizations
        sub_id = self._sub_id_from_path(path)
        return self._localizations_by_sub.get(sub_id, [])

    async def _get(self, path: str, params=None) -> dict:
        if "introductoryOffers" in path:
            sub_id = self._sub_id_from_path(path)
            return self._intro_offers_by_sub.get(
                sub_id, {"data": [], "included": []},
            )
        return {"data": [], "included": []}

    async def _patch(self, path: str, json=None) -> dict:
        self.patched.append((path, json))
        return {"data": {"id": path.rsplit("/", 1)[-1], "attributes": {}}}

    async def _delete(self, path: str) -> None:
        self.deleted.append(path)

    async def __aenter__(self) -> "_FakeASCClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def close(self) -> None:
        return None


def _localization(loc_id: str, locale: str = "en-US") -> dict:
    return {
        "id": loc_id,
        "type": "subscriptionLocalizations",
        "attributes": {
            "locale": locale, "name": "N", "description": "D",
            "state": "PREPARE_FOR_SUBMISSION",
        },
    }


def _intro_offer_resource(offer_id: str) -> dict:
    return {
        "id": offer_id,
        "type": "subscriptionIntroductoryOffers",
        "attributes": {
            "offerMode": "FREE_TRIAL",
            "duration": "ONE_WEEK",
            "numberOfPeriods": 1,
        },
    }


# ----------------------------------------------------------------------
# Service-layer membership assertions (the centralized helpers)
# ----------------------------------------------------------------------


def test_assert_subscription_localization_membership():
    from app.services.asc.errors import ChildResourceNotFoundError
    from app.services.asc.pricing import ASCPricingService

    async def go() -> None:
        client = _FakeASCClient(
            localizations_by_sub={"sub-A": [_localization("loc-A1")]},
        )
        svc = ASCPricingService(client)  # type: ignore[arg-type]

        # Member id passes.
        await svc.assert_subscription_localization("sub-A", "loc-A1")

        # Foreign id raises.
        with pytest.raises(ChildResourceNotFoundError):
            await svc.assert_subscription_localization("sub-A", "loc-B1")

    run_async(go())


def test_assert_subscription_intro_offer_membership():
    from app.services.asc.errors import ChildResourceNotFoundError
    from app.services.asc.pricing import ASCPricingService

    async def go() -> None:
        client = _FakeASCClient(
            intro_offers_by_sub={
                "sub-A": {
                    "data": [_intro_offer_resource("offer-A1")],
                    "included": [],
                },
            }
        )
        svc = ASCPricingService(client)  # type: ignore[arg-type]

        await svc.assert_subscription_intro_offer("sub-A", "offer-A1")

        with pytest.raises(ChildResourceNotFoundError):
            await svc.assert_subscription_intro_offer("sub-A", "offer-B1")

    run_async(go())


# ----------------------------------------------------------------------
# Seeding: two subscriptions under the SAME app/credential
# ----------------------------------------------------------------------


async def _seed_two_subscriptions() -> tuple[int, int, int]:
    """Return ``(user_id, app_id, sub_b_id)`` with sub A and sub B sharing
    one app/credential/group. The fake ASC client is wired to know only
    sub A's children, so passing sub B's id (verified) but sub A's child
    must 404."""
    from app.db.base import Base
    from app.db.session import async_session_factory, engine
    from app.models.app import App
    from app.models.credential import ASCCredential
    from app.models.subscription import Subscription, SubscriptionGroup
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        user = User(
            email=f"swap-{suffix}@example.com",
            password_hash="x",
            name="Swap Test",
        )
        session.add(user)
        await session.flush()

        cred = ASCCredential(
            user_id=user.id,
            name="ASC",
            issuer_id=f"iss-{suffix}",
            key_id=f"key-{suffix}",
            private_key_encrypted="fixture",
        )
        session.add(cred)
        await session.flush()

        app = App(
            credential_id=cred.id,
            asc_app_id=f"adam-{suffix}",
            bundle_id=f"com.example.swap.{suffix}",
            name="Swap App",
            platform="ios",
        )
        session.add(app)
        await session.flush()

        group = SubscriptionGroup(
            app_id=app.id,
            asc_group_id=f"grp-{suffix}",
            name="Premium",
        )
        session.add(group)
        await session.flush()

        sub_a = Subscription(
            group_id=group.id,
            asc_subscription_id="sub-A",
            name="Sub A",
            product_id=f"com.example.swap.{suffix}.a",
        )
        sub_b = Subscription(
            group_id=group.id,
            asc_subscription_id="sub-B",
            name="Sub B",
            product_id=f"com.example.swap.{suffix}.b",
        )
        session.add(sub_a)
        session.add(sub_b)
        await session.commit()
        return user.id, app.id, sub_b.id


# ----------------------------------------------------------------------
# B-I1: REST IDOR — localization + intro offer
# ----------------------------------------------------------------------


def test_rest_update_localization_foreign_child_404(monkeypatch):
    """Updating sub-B's localization while passing sub-A's localization id
    (which the fake client reports under sub A only) must 404."""
    import app.api.v1.pricing as rest
    from app.schemas.pricing import LocalizationUpdate

    # sub A owns loc-A1; sub B owns nothing. Authorizing against sub B but
    # passing loc-A1 must 404.
    fake = _FakeASCClient(
        localizations_by_sub={"sub-A": [_localization("loc-A1")]},
    )

    async def go() -> tuple[int, str]:
        user_id, app_id, sub_b_id = await _seed_two_subscriptions()

        async def _fake_client(app, session):
            return fake

        monkeypatch.setattr(rest, "_get_asc_client_for_app", _fake_client)

        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            try:
                await rest.update_subscription_localization(
                    app_id=app_id,
                    subscription_id=sub_b_id,
                    localization_id="loc-A1",  # belongs to sub A, not sub B
                    body=LocalizationUpdate(name="x", description="y"),
                    current_user={"user_id": str(user_id)},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = run_async(go())
    assert status_code == 404, (status_code, detail)
    # The mutating PATCH must never have run.
    assert fake.patched == []


def test_rest_delete_intro_offer_foreign_child_404(monkeypatch):
    """Deleting an intro offer that is not a member of sub B must 404."""
    import app.api.v1.pricing as rest

    fake = _FakeASCClient(
        intro_offers_by_sub={
            "sub-A": {
                "data": [_intro_offer_resource("offer-A1")],
                "included": [],
            },
        }
    )

    async def go() -> tuple[int, str]:
        user_id, app_id, sub_b_id = await _seed_two_subscriptions()

        async def _fake_client(app, session):
            return fake

        monkeypatch.setattr(rest, "_get_asc_client_for_app", _fake_client)

        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            try:
                await rest.delete_subscription_intro_offer(
                    app_id=app_id,
                    subscription_id=sub_b_id,
                    offer_id="offer-A1",  # not a member of sub B
                    current_user={"user_id": str(user_id)},
                    session=session,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
        return 0, "no error raised"

    status_code, detail = run_async(go())
    assert status_code == 404, (status_code, detail)
    assert fake.deleted == []


# ----------------------------------------------------------------------
# B-I1: MCP mirror — delete_subscription_localization
# ----------------------------------------------------------------------


def test_mcp_delete_localization_foreign_child_toolerror(monkeypatch):
    import app.mcp.tools.pricing as mcp_pricing

    fake = _FakeASCClient(
        localizations_by_sub={"sub-A": [_localization("loc-A1")]},
    )

    async def go() -> None:
        user_id, app_id, sub_b_id = await _seed_two_subscriptions()

        from app.db.session import async_session_factory

        async def _fake_client(app, session):
            return fake

        @asynccontextmanager
        async def _scope():
            async with async_session_factory() as session:
                yield session

        async def _resolve_app(aid, session):
            from app.models.app import App
            from sqlalchemy import select

            res = await session.execute(select(App).where(App.id == aid))
            return res.scalar_one()

        monkeypatch.setattr(mcp_pricing, "_get_asc_client_for_app", _fake_client)
        monkeypatch.setattr(mcp_pricing, "session_scope", _scope)
        monkeypatch.setattr(mcp_pricing, "resolve_app", _resolve_app)

        with pytest.raises(ToolError):
            await mcp_pricing.delete_subscription_localization.fn(
                app_id=app_id,
                subscription_id=sub_b_id,
                localization_id="loc-A1",  # belongs to sub A
            )
        assert fake.deleted == []

    run_async(go())


# ----------------------------------------------------------------------
# A-I1 / A-I2 / A-I3: swap finalize + checklist
# ----------------------------------------------------------------------


def test_swap_warnings_archive_failed_subscription():
    from app.api.v1.clone import (
        ARCHIVE_INCOMPLETE_WARNING,
        _swap_warnings,
    )

    warnings = _swap_warnings("subscription", "failed")
    assert ARCHIVE_INCOMPLETE_WARNING in warnings

    # A clean archive produces no warning.
    assert _swap_warnings("subscription", "done") == []


def test_swap_warnings_iap_always_warns():
    from app.api.v1.clone import IAP_STILL_LIVE_WARNING, _swap_warnings

    # Apple can't archive an IAP; the old IAP always stays live.
    assert IAP_STILL_LIVE_WARNING in _swap_warnings("iap", "skipped")


def test_ios_checklist_archive_failure_does_not_claim_no_change():
    """A-I1: when ASC archive failed but RC swap 'succeeded', the checklist
    must NOT claim PATH 1 'no iOS change required' and must warn."""
    from app.api.v1.clone import ARCHIVE_INCOMPLETE_WARNING
    from app.mcp.tools.swap import _ios_checklist

    items = _ios_checklist(
        rc_connected=True,
        rc_swap_ok=False,  # archive failed -> not ok (A-I1)
        target_asc_id="asc-new",
        warnings=[ARCHIVE_INCOMPLETE_WARNING],
    )
    joined = "\n".join(items)
    assert ARCHIVE_INCOMPLETE_WARNING in items
    assert "no iOS code change required" not in joined


def test_finalize_swap_archive_error_marks_rc_swap_not_ok(monkeypatch):
    """End-to-end finalize: ASC archive error + RC swap success ->
    rc_swap_ok is False and the archive warning is in the op error log."""
    from app.api.v1.clone import (
        ARCHIVE_INCOMPLETE_WARNING,
        finalize_swap,
    )

    async def go() -> dict:
        from app.db.base import Base
        from app.db.session import async_session_factory, engine
        from app.models.app import App
        from app.models.clone_operation import CloneOperation
        from app.models.credential import ASCCredential
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        suffix = uuid.uuid4().hex[:8]
        async with async_session_factory() as session:
            user = User(
                email=f"fin-{suffix}@example.com",
                password_hash="x",
                name="Fin",
            )
            session.add(user)
            await session.flush()
            cred = ASCCredential(
                user_id=user.id, name="ASC",
                issuer_id=f"iss-{suffix}", key_id=f"key-{suffix}",
                private_key_encrypted="fixture",
            )
            session.add(cred)
            await session.flush()
            app = App(
                credential_id=cred.id, asc_app_id=f"adam-{suffix}",
                bundle_id=f"com.example.fin.{suffix}", name="Fin App",
                platform="ios",
            )
            session.add(app)
            await session.flush()
            op = CloneOperation(
                app_id=app.id, user_id=user.id, source_kind="subscription",
                source_local_id=1, source_asc_id="sub-A",
                source_product_id="com.example.a",
                target_product_id="com.example.a.v2",
                target_asc_id="sub-A-v2",
                scope_json={}, asc_steps_json=[], revenuecat_steps_json=[],
                status="pending",
                error_log_json=[],
            )
            session.add(op)
            await session.commit()
            op_id, app_id, user_id = op.id, app.id, user.id

        # No RC credential configured -> rc_connected is False, so the
        # RC swap is a no-op; the ASC archive error must still drive the
        # warning + rc_swap_ok=False.
        async with async_session_factory() as session:
            from sqlalchemy import select

            op = (
                await session.execute(
                    select(CloneOperation).where(CloneOperation.id == op_id)
                )
            ).scalar_one()
            app = (
                await session.execute(select(App).where(App.id == app_id))
            ).scalar_one()

            health = await finalize_swap(
                op=op,
                app=app,
                user_id=user_id,
                session=session,
                swap_revenuecat=True,
                old_product_id="com.example.a",
                new_product_id="com.example.a.v2",
                product_type="subscription",
                display_name="A",
                asc_errs=["archive_source: 500 boom"],
                source_kind="subscription",
                archive_status="failed",
            )
            return {
                "rc_swap_ok": health["rc_swap_ok"],
                "warnings": health["warnings"],
                "error_log": list(op.error_log_json),
                "status": op.status,
            }

    result = run_async(go())
    assert result["rc_swap_ok"] is False
    assert ARCHIVE_INCOMPLETE_WARNING in result["warnings"]
    assert ARCHIVE_INCOMPLETE_WARNING in result["error_log"]
    assert result["status"] == "partial"


# ----------------------------------------------------------------------
# B-I3: set_iap_price uses the requested base territory
# ----------------------------------------------------------------------


def test_set_iap_price_uses_requested_base_territory():
    from app.services.asc.pricing import ASCPricingService

    class _Recorder:
        def __init__(self):
            self.body: dict | None = None

        async def _post(self, path, json=None):
            self.body = json
            return {"data": {"id": "sched-1"}}

    async def go() -> dict:
        client = _Recorder()
        svc = ASCPricingService(client)  # type: ignore[arg-type]
        await svc.set_iap_price(
            iap_id="iap-1",
            price_entries=[
                {"territory_code": "DE", "price_point_id": "pp-de"},
                {"territory_code": "GB", "price_point_id": "pp-gb"},
            ],
            base_territory_alpha3="DEU",
        )
        return client.body

    body = run_async(go())
    rels = body["data"]["relationships"]
    # Requested base territory is used, NOT the hardcoded USA.
    assert rels["baseTerritory"]["data"]["id"] == "DEU"
    # And the base territory has a manual price in the submission.
    local_ids = {mp["id"] for mp in rels["manualPrices"]["data"]}
    assert "${DE}" in local_ids


def test_resolve_iap_base_territory_falls_back_to_first_priced():
    from app.api.v1.pricing import _resolve_iap_base_territory

    entries = [
        {"territory_code": "DE", "price_point_id": "pp-de"},
        {"territory_code": "GB", "price_point_id": "pp-gb"},
    ]
    # Requested US is not among the priced territories -> fall back to the
    # first priced territory (DE -> DEU) so the schedule baseTerritory has
    # a manual price.
    assert _resolve_iap_base_territory("US", entries) == "DEU"
    # Requested DE is present -> use it.
    assert _resolve_iap_base_territory("DE", entries) == "DEU"
    assert _resolve_iap_base_territory("GB", entries) == "GBR"


# ----------------------------------------------------------------------
# B-M5: intro-offer parse with missing/blank offerMode does not 500
# ----------------------------------------------------------------------


def test_parse_intro_offer_blank_offer_mode_does_not_raise():
    from app.api.v1.pricing import _parse_intro_offer

    item = {
        "resource": {
            "id": "offer-x",
            "attributes": {
                # offerMode entirely missing, duration unknown,
                # numberOfPeriods non-numeric.
                "duration": "WEIRD_DURATION",
                "numberOfPeriods": "not-a-number",
            },
            "relationships": {},
        },
        "included": [],
    }
    parsed = _parse_intro_offer(item)
    assert parsed.offer_mode is None
    assert parsed.duration is None
    assert parsed.number_of_periods == 1


def test_parse_intro_offer_valid_mode_preserved():
    from app.api.v1.pricing import _parse_intro_offer

    item = {
        "resource": {
            "id": "offer-y",
            "attributes": {
                "offerMode": "FREE_TRIAL",
                "duration": "ONE_WEEK",
                "numberOfPeriods": 1,
            },
            "relationships": {},
        },
        "included": [],
    }
    parsed = _parse_intro_offer(item)
    assert parsed.offer_mode == "FREE_TRIAL"
    assert parsed.duration == "ONE_WEEK"


# ----------------------------------------------------------------------
# B-M8: bulk localization sync isolates per-locale failures
# ----------------------------------------------------------------------


def test_bulk_sync_localizations_isolates_failures():
    from app.api.v1.pricing import _bulk_sync_localizations
    from app.schemas.pricing import LocalizationCreate
    from app.services.asc.errors import ASCAPIError

    async def go():
        async def create_fn(locale, name, desc):
            if locale == "de-DE":
                raise ASCAPIError(
                    409, {"errors": [{"detail": "rejected locale"}]}
                )
            return {"data": _localization(f"new-{locale}", locale)}

        async def update_fn(loc_id, name, desc):  # pragma: no cover
            return {"data": _localization(loc_id)}

        return await _bulk_sync_localizations(
            existing=[],
            requested=[
                LocalizationCreate(locale="en-US", name="A", description="x"),
                LocalizationCreate(locale="de-DE", name="B", description="y"),
                LocalizationCreate(locale="fr-FR", name="C", description="z"),
            ],
            create_fn=create_fn,
            update_fn=update_fn,
        )

    result = run_async(go())
    # en-US and fr-FR succeed; de-DE fails but does not abort the batch.
    assert result.created == 2
    assert result.failed == 1
    assert len(result.errors) == 1
    assert "de-DE" in result.errors[0]
    assert len(result.localizations) == 2
