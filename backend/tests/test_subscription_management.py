"""Tests for subscription / group / intro-offer write paths.

These tests exercise:

- ``ASCPricingService`` builds the correct JSON:API request bodies for
  the new create/update endpoints.
- ``IntroOfferCreate`` schema validators reject invalid combinations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.pricing import IntroOfferCreate, SubscriptionUpdate
from app.services.asc.pricing import ASCPricingService


class _RecordingClient:
    """Minimal stub of ``ASCClient`` that captures the request payload."""

    def __init__(self, response: dict | None = None):
        self.response = response or {"data": {"id": "stub"}}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, json))
        return self.response

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("PATCH", path, json))
        return self.response

    async def _delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))


# ---------------------------------------------------------------------------
# ASCPricingService — JSON:API body shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_subscription_group_body_shape():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_group("APP_123", "premium_group")

    method, path, body = client.calls[0]
    assert method == "POST"
    assert path == "/subscriptionGroups"
    data = body["data"]
    assert data["type"] == "subscriptionGroups"
    assert data["attributes"] == {"referenceName": "premium_group"}
    assert data["relationships"]["app"]["data"] == {
        "type": "apps", "id": "APP_123",
    }


@pytest.mark.asyncio
async def test_update_subscription_group_body_shape():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.update_subscription_group("GRP_1", "renamed")

    method, path, body = client.calls[0]
    assert method == "PATCH"
    assert path == "/subscriptionGroups/GRP_1"
    assert body["data"]["id"] == "GRP_1"
    assert body["data"]["attributes"] == {"referenceName": "renamed"}


@pytest.mark.asyncio
async def test_create_subscription_group_localization_includes_custom_app_name():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_group_localization(
        "GRP_1", "en-US", "Pro", custom_app_name="My Pro App"
    )

    _, path, body = client.calls[0]
    assert path == "/subscriptionGroupLocalizations"
    attrs = body["data"]["attributes"]
    assert attrs == {
        "locale": "en-US",
        "name": "Pro",
        "customAppName": "My Pro App",
    }


@pytest.mark.asyncio
async def test_create_subscription_group_localization_omits_custom_app_name_when_none():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_group_localization("GRP_1", "ru", "Премиум")

    _, _, body = client.calls[0]
    attrs = body["data"]["attributes"]
    assert "customAppName" not in attrs


@pytest.mark.asyncio
async def test_create_subscription_body_shape():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription(
        group_id="GRP_1",
        product_id="com.example.app.monthly",
        name="Monthly",
        period="ONE_MONTH",
        family_sharable=True,
        available_in_all_territories=True,
        group_level=1,
        review_note="Tester notes",
    )

    method, path, body = client.calls[0]
    assert method == "POST"
    assert path == "/subscriptions"
    attrs = body["data"]["attributes"]
    assert attrs["productId"] == "com.example.app.monthly"
    assert attrs["subscriptionPeriod"] == "ONE_MONTH"
    assert attrs["familySharable"] is True
    # ``availableInAllTerritories`` is intentionally omitted — Apple rejects it
    # on the subscriptions resource (per-territory availability is set via the
    # separate subscriptionAvailabilities resource after creation).
    assert "availableInAllTerritories" not in attrs
    assert attrs["groupLevel"] == 1
    assert attrs["reviewNote"] == "Tester notes"
    rel = body["data"]["relationships"]["group"]["data"]
    assert rel == {"type": "subscriptionGroups", "id": "GRP_1"}


@pytest.mark.asyncio
async def test_update_subscription_only_includes_provided_fields():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.update_subscription("SUB_1", name="Renamed")

    _, path, body = client.calls[0]
    assert path == "/subscriptions/SUB_1"
    assert body["data"]["attributes"] == {"name": "Renamed"}


@pytest.mark.asyncio
async def test_update_subscription_with_no_fields_raises():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await svc.update_subscription("SUB_1")


@pytest.mark.asyncio
async def test_create_intro_offer_free_trial_omits_price_point():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_introductory_offer(
        subscription_id="SUB_1",
        offer_mode="FREE_TRIAL",
        duration="ONE_MONTH",
        number_of_periods=1,
        territory_id="USA",
    )

    _, path, body = client.calls[0]
    assert path == "/subscriptionIntroductoryOffers"
    rel = body["data"]["relationships"]
    assert rel["territory"]["data"] == {"type": "territories", "id": "USA"}
    assert "subscriptionPricePoint" not in rel
    assert body["data"]["attributes"]["offerMode"] == "FREE_TRIAL"


@pytest.mark.asyncio
async def test_create_intro_offer_pay_as_you_go_includes_price_point():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_introductory_offer(
        subscription_id="SUB_1",
        offer_mode="PAY_AS_YOU_GO",
        duration="ONE_MONTH",
        number_of_periods=3,
        territory_id="USA",
        price_point_id="PP_42",
    )

    _, _, body = client.calls[0]
    rel = body["data"]["relationships"]
    assert rel["subscriptionPricePoint"]["data"] == {
        "type": "subscriptionPricePoints", "id": "PP_42",
    }


@pytest.mark.asyncio
async def test_create_intro_offer_worldwide_omits_territory():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_introductory_offer(
        subscription_id="SUB_1",
        offer_mode="FREE_TRIAL",
        duration="ONE_WEEK",
        number_of_periods=1,
    )

    _, _, body = client.calls[0]
    assert "territory" not in body["data"]["relationships"]


@pytest.mark.asyncio
async def test_create_subscription_availability_body_shape():
    """Apple ships new subs with zero territories — POST availability after create."""
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_subscription_availability(
        subscription_id="SUB_1",
        available_alpha3_codes=["USA", "GBR", "DEU"],
        available_in_new_territories=True,
    )

    method, path, body = client.calls[0]
    assert method == "POST"
    assert path == "/subscriptionAvailabilities"
    data = body["data"]
    assert data["type"] == "subscriptionAvailabilities"
    assert data["attributes"] == {"availableInNewTerritories": True}
    assert data["relationships"]["subscription"]["data"] == {
        "type": "subscriptions", "id": "SUB_1",
    }
    assert data["relationships"]["availableTerritories"]["data"] == [
        {"type": "territories", "id": "USA"},
        {"type": "territories", "id": "GBR"},
        {"type": "territories", "id": "DEU"},
    ]


@pytest.mark.asyncio
async def test_delete_intro_offer():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.delete_subscription_introductory_offer("OFFER_9")

    method, path, _ = client.calls[0]
    assert method == "DELETE"
    assert path == "/subscriptionIntroductoryOffers/OFFER_9"


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------


def test_intro_offer_free_trial_rejects_price_point_id():
    with pytest.raises(ValidationError):
        IntroOfferCreate(
            territory_code="US",
            offer_mode="FREE_TRIAL",
            duration="ONE_MONTH",
            number_of_periods=1,
            price_point_id="PP_1",
        )


def test_intro_offer_pay_as_you_go_requires_price_point_id():
    with pytest.raises(ValidationError):
        IntroOfferCreate(
            territory_code="US",
            offer_mode="PAY_AS_YOU_GO",
            duration="ONE_MONTH",
            number_of_periods=3,
        )


def test_intro_offer_pay_up_front_forces_one_period():
    with pytest.raises(ValidationError):
        IntroOfferCreate(
            territory_code="US",
            offer_mode="PAY_UP_FRONT",
            duration="ONE_MONTH",
            number_of_periods=2,
            price_point_id="PP_1",
        )


def test_intro_offer_normalizes_alpha2_uppercase():
    offer = IntroOfferCreate(
        offer_mode="FREE_TRIAL",
        duration="ONE_MONTH",
        number_of_periods=1,
        territory_code="us",
    )
    assert offer.territory_code == "US"


def test_intro_offer_requires_territory_code():
    """Apple has no worldwide intro offer — territory is mandatory."""
    with pytest.raises(ValidationError):
        IntroOfferCreate(
            offer_mode="FREE_TRIAL",
            duration="ONE_MONTH",
            number_of_periods=1,
        )


def test_subscription_update_requires_at_least_one_field():
    with pytest.raises(ValidationError):
        SubscriptionUpdate()


def test_subscription_update_accepts_single_field():
    body = SubscriptionUpdate(name="x")
    assert body.name == "x"


# ---------------------------------------------------------------------------
# Bulk localization — locale canonicalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_matches_canonicalized_locale():
    """Apple stores 'th' as 'th-TH'; resubmitting 'th' must PATCH not POST."""
    from app.api.v1.pricing import _bulk_sync_localizations
    from app.schemas.pricing import LocalizationCreate

    existing = [
        {
            "id": "L1",
            "type": "subscriptionLocalizations",
            "attributes": {
                "locale": "th-TH",
                "name": "Thai existing",
                "description": "Thai desc",
            },
        }
    ]

    create_calls: list = []
    update_calls: list = []

    async def fake_create(locale: str, name: str, desc: str) -> dict:
        create_calls.append((locale, name, desc))
        return {
            "data": {
                "id": "NEW",
                "attributes": {
                    "locale": locale, "name": name, "description": desc,
                },
            }
        }

    async def fake_update(loc_id: str, name: str, desc: str) -> dict:
        update_calls.append((loc_id, name, desc))
        return {
            "data": {
                "id": loc_id,
                "attributes": {
                    "locale": "th-TH", "name": name, "description": desc,
                },
            }
        }

    requested = [
        LocalizationCreate(locale="th", name="Thai new", description="d"),
    ]
    resp = await _bulk_sync_localizations(
        existing=existing,
        requested=requested,
        create_fn=fake_create,
        update_fn=fake_update,
    )

    assert create_calls == []
    assert update_calls == [("L1", "Thai new", "d")]
    assert resp.created == 0
    assert resp.updated == 1
    assert len(resp.localizations) == 1


@pytest.mark.asyncio
async def test_bulk_sync_exact_match_wins_over_prefix():
    """When both pt-PT and pt-BR exist, requesting pt-BR matches pt-BR."""
    from app.api.v1.pricing import _bulk_sync_localizations
    from app.schemas.pricing import LocalizationCreate

    existing = [
        {
            "id": "L_PT",
            "attributes": {
                "locale": "pt-PT", "name": "PT", "description": "d",
            },
        },
        {
            "id": "L_BR",
            "attributes": {
                "locale": "pt-BR", "name": "BR", "description": "d",
            },
        },
    ]
    update_calls: list = []

    async def fake_create(*args):
        raise AssertionError("create should not be called")

    async def fake_update(loc_id: str, name: str, desc: str) -> dict:
        update_calls.append((loc_id, name, desc))
        return {
            "data": {
                "id": loc_id,
                "attributes": {
                    "locale": "pt-BR", "name": name, "description": desc,
                },
            }
        }

    requested = [
        LocalizationCreate(locale="pt-BR", name="BR new", description="d"),
    ]
    resp = await _bulk_sync_localizations(
        existing=existing,
        requested=requested,
        create_fn=fake_create,
        update_fn=fake_update,
    )

    assert update_calls == [("L_BR", "BR new", "d")]
    assert resp.updated == 1


def test_normalize_locale_strips_region():
    from app.api.v1.pricing import _normalize_locale
    assert _normalize_locale("th-TH") == "th"
    assert _normalize_locale("uk-UA") == "uk"
    assert _normalize_locale("EN-US") == "en"
    assert _normalize_locale("ru") == "ru"


# ---------------------------------------------------------------------------
# PriceApplyRequest.intro_offer
# ---------------------------------------------------------------------------


def test_price_apply_request_accepts_intro_offer():
    from app.schemas.pricing import (
        IntroOfferApplyConfig, PriceApplyItem, PriceApplyRequest,
    )

    req = PriceApplyRequest(
        items=[PriceApplyItem(territory_code="US", price_point_id="PP_1")],
        intro_offer=IntroOfferApplyConfig(
            duration="ONE_MONTH", number_of_periods=1,
        ),
    )
    assert req.intro_offer is not None
    assert req.intro_offer.duration == "ONE_MONTH"


def test_price_apply_request_intro_offer_optional():
    from app.schemas.pricing import PriceApplyItem, PriceApplyRequest

    req = PriceApplyRequest(
        items=[PriceApplyItem(territory_code="US", price_point_id="PP_1")],
    )
    assert req.intro_offer is None


def test_intro_offer_apply_config_rejects_invalid_duration():
    from app.schemas.pricing import IntroOfferApplyConfig

    with pytest.raises(ValidationError):
        IntroOfferApplyConfig(duration="LIFETIME", number_of_periods=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Subscription availability sources from the app, not the full alpha-3 map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_app_target_territories_uses_app_availability():
    """A sub's availability mirrors the app's available territories, never
    the full ALPHA2_TO_ALPHA3 map (which was silently truncated by Apple
    and previously dropped HKG, blocking subs at MISSING_METADATA)."""
    from app.api.v1.pricing import _resolve_app_target_territories

    class _StubClient:
        BASE_URL = "https://api.appstoreconnect.apple.com/v1"

        async def _throttle(self) -> None:
            return None

        async def _get_client(self):  # noqa: ANN101
            return self

        async def get(self, url: str):  # noqa: ARG002
            class _Resp:
                status_code = 200

                @staticmethod
                def json():
                    if url.endswith("/appAvailabilityV2"):
                        return {
                            "data": {
                                "id": "AV_1",
                                "attributes": {"availableInNewTerritories": True},
                            }
                        }
                    # /v2/appAvailabilities/AV_1/territoryAvailabilities
                    # Two territoryAvailabilities encoded with payload
                    # {"s":"APP_1","t":"<alpha3>"}; only USA is available.
                    import base64, json
                    items = []
                    for alpha3, available in [("USA", True), ("DEU", False)]:
                        payload = json.dumps(
                            {"s": "APP_1", "t": alpha3},
                            separators=(",", ":"),
                        )
                        ta_id = base64.b64encode(payload.encode()).decode().rstrip("=")
                        items.append({
                            "type": "territoryAvailabilities",
                            "id": ta_id,
                            "attributes": {
                                "available": available,
                                "preOrderEnabled": False,
                            },
                        })
                    return {"data": items, "links": {}}

            return _Resp()

    alpha3, avail_in_new = await _resolve_app_target_territories(
        _StubClient(),  # type: ignore[arg-type]
        "APP_1",
    )

    assert alpha3 == ["USA"]
    assert avail_in_new is True


@pytest.mark.asyncio
async def test_list_subscription_availability_returns_alpha3():
    """Service returns alpha-3 ids from
    /v1/subscriptionAvailabilities/{id}/availableTerritories.

    Stubs the client's _get_all_pages to return Apple's JSON:API shape;
    asserts the method returns just the ``id`` strings filtered to type
    ``territories``.
    """

    class _PaginatingClient:
        async def _get_all_pages(self, path: str, params=None):  # noqa: ARG002
            assert path == (
                "/subscriptionAvailabilities/SUB_1/availableTerritories"
            )
            return [
                {"type": "territories", "id": "USA"},
                {"type": "territories", "id": "GBR"},
                {"type": "territories", "id": "HKG"},
                # Stray non-territory item — must be ignored
                {"type": "appPrices", "id": "PP_1"},
                # Empty id — must be skipped
                {"type": "territories", "id": ""},
            ]

    svc = ASCPricingService(_PaginatingClient())  # type: ignore[arg-type]
    result = await svc.list_subscription_availability("SUB_1")
    assert result == ["USA", "GBR", "HKG"]


@pytest.mark.asyncio
async def test_resolve_app_target_territories_falls_back_to_full_catalog():
    """When appAvailabilityV2 returns 404 (Mushtra-style), fall back to
    Apple's canonical /v1/territories list."""
    from app.api.v1.pricing import _resolve_app_target_territories
    from app.services.asc.errors import ASCAPIError

    class _StubAvailService:
        async def get_app_availability(self, _: str):
            raise ASCAPIError(
                404,
                {"errors": [{"detail": "There is no resource"}]},
            )

    class _StubClient:
        BASE_URL = "https://api.appstoreconnect.apple.com/v1"

        async def _get_all_pages(self, path: str, params=None):  # noqa: ARG002
            assert path == "/territories"
            return [
                {"id": "USA"},
                {"id": "GBR"},
                {"id": "HKG"},
            ]

    # Patch ASCAvailabilityService inside the helper for this call only.
    import app.api.v1.pricing as pricing_module
    original = pricing_module.ASCAvailabilityService
    pricing_module.ASCAvailabilityService = lambda _client: _StubAvailService()  # type: ignore[assignment]
    try:
        alpha3, avail_in_new = await _resolve_app_target_territories(
            _StubClient(),  # type: ignore[arg-type]
            "APP_1",
        )
    finally:
        pricing_module.ASCAvailabilityService = original

    assert alpha3 == ["GBR", "HKG", "USA"]
    assert avail_in_new is True


# ---------------------------------------------------------------------------
# Territory seed parity with the alpha-2/alpha-3 map
# ---------------------------------------------------------------------------


def test_seed_covers_every_apple_territory():
    """Every alpha-2 in ALPHA2_TO_ALPHA3 must have a Territory seed entry.

    Why: the price-preview pipeline iterates the Territory table. If a code
    is in the alpha map but missing from the seed, that territory silently
    drops out of preview/apply (which is exactly how Afghanistan was
    invisible for months and stuck two subs at MISSING_METADATA).
    """
    from app.data.territories import ALPHA2_TO_ALPHA3, TERRITORIES

    seed_codes = {t["code"] for t in TERRITORIES}
    map_codes = set(ALPHA2_TO_ALPHA3.keys())
    missing = map_codes - seed_codes
    assert missing == set(), (
        f"Territories in ALPHA2_TO_ALPHA3 but missing from TERRITORIES seed: "
        f"{sorted(missing)}"
    )
