"""Tests for the IAP create/update/delete lifecycle.

IAPs had only a create *service method* — no update, no delete, and nothing
exposed over REST or MCP — so six consumables had to be typed into the ASC web
UI by hand. These cover the contract of the paths that closed that gap:

- the immutables (``productId``, ``inAppPurchaseType``) stay unexpressible,
- an empty PATCH is refused locally instead of sent to Apple,
- the delete-localization guard rejects a child of another IAP,
- the local guards stay distinguishable from an upstream decode failure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.v1.pricing import _assert_family_sharing_supported
from app.services.asc.errors import (
    ASCAPIError,
    ASCRequestInvalidError,
    ChildResourceNotFoundError,
    IAPScheduleUnsyncedError,
)
from app.schemas.pricing import IAPCreate, IAPUpdate
from app.services.asc.pricing import ASCPricingService, _raise_for_asc_error


def _not_found_body(iap_id: str) -> dict:
    """Apple's real 404 for a missing price schedule, captured live.

    Byte-identical whether the IAP exists without a schedule or does not
    exist at all — which is why the code probes the IAP instead of reading
    this.
    """
    return {"errors": [{
        "id": "93ce4371-82a0-4b44-a088-c11f915a5496",
        "status": "404",
        "code": "NOT_FOUND",
        "title": "The specified resource does not exist",
        "detail": (
            "There is no resource of type 'inAppPurchasePriceSchedules' "
            f"with id '{iap_id}'"
        ),
    }]}


class _RecordingHTTP:
    """Stub of the raw httpx client used by the v2-only IAP methods."""

    def __init__(self, default_status: int = 200):
        self.default_status = default_status
        self.calls: list[tuple[str, str, dict | None]] = []
        # url-substring -> (status_code, body); unmatched urls get
        # default_status and a generic body.
        self.responses: dict[str, tuple[int, dict]] = {}

    class _Resp:
        def __init__(self, status_code: int, body: dict | None = None):
            self.status_code = status_code
            self.content = b"{}"
            self._body = body or {"data": {"id": "IAP_1", "attributes": {}}}

        def json(self) -> dict:
            return self._body

    def _resp_for(self, url: str):
        for fragment, (code, body) in self.responses.items():
            if fragment in url:
                return self._Resp(code, body)
        return self._Resp(self.default_status)

    async def get(self, url: str):
        self.calls.append(("GET", url, None))
        return self._resp_for(url)

    async def post(self, url: str, json: dict | None = None):
        self.calls.append(("POST", url, json))
        return self._resp_for(url)

    async def patch(self, url: str, json: dict | None = None):
        self.calls.append(("PATCH", url, json))
        return self._resp_for(url)

    async def delete(self, url: str):
        self.calls.append(("DELETE", url, None))
        return self._resp_for(url)


class _RecordingClient:
    """Stub ASCClient: ``http.calls`` are raw v2 calls, ``v1_calls`` go
    through the client's own v1 helpers."""

    BASE_URL = "https://api.appstoreconnect.apple.com/v1"

    def __init__(self):
        self.http = _RecordingHTTP()
        self.v1_calls: list[tuple[str, str, dict | None]] = []

    async def _get_client(self):
        return self.http

    async def _throttle(self) -> None:
        return None

    async def _delete(self, path: str) -> None:
        self.v1_calls.append(("DELETE", path, None))


# ---------------------------------------------------------------------------
# Schemas — the immutability contract
# ---------------------------------------------------------------------------


def test_iap_create_rejects_unknown_type():
    """An unknown type used to travel to ASC and come back as an opaque 400."""
    with pytest.raises(ValidationError):
        IAPCreate(
            product_id="com.x.coins", name="Coins", iap_type="SUBSCRIPTION",
        )


def test_iap_create_rejects_family_sharing_on_a_consumable():
    """create_iap silently drops it for non-NON_CONSUMABLE — don't accept it."""
    with pytest.raises(ValidationError):
        IAPCreate(
            product_id="com.x.coins",
            name="Coins",
            iap_type="CONSUMABLE",
            family_sharable=True,
        )
    # …and the type Apple does honour it on still passes.
    assert IAPCreate(
        product_id="com.x.pro",
        name="Pro",
        iap_type="NON_CONSUMABLE",
        family_sharable=True,
    ).family_sharable is True


def test_iap_update_cannot_express_the_immutables():
    """productId / iap_type are immutable in ASC once the IAP exists."""
    assert "product_id" not in IAPUpdate.model_fields
    assert "iap_type" not in IAPUpdate.model_fields


# ---------------------------------------------------------------------------
# Service — request shape and guards
# ---------------------------------------------------------------------------


async def test_update_iap_with_no_fields_never_reaches_apple():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    with pytest.raises(ASCRequestInvalidError):
        await svc.update_iap("IAP_1")
    assert client.http.calls == []


def test_update_refuses_family_sharing_create_would_have_rejected():
    """``IAPCreate`` refuses it locally; update must not be the way around it.

    Apple honours ``familySharable`` only on NON_CONSUMABLE, so anywhere else
    it is a no-op the caller believes took effect.
    """
    consumable = SimpleNamespace(iap_type="CONSUMABLE")
    with pytest.raises(ASCRequestInvalidError):
        _assert_family_sharing_supported(consumable, True)
    with pytest.raises(ASCRequestInvalidError):
        _assert_family_sharing_supported(consumable, False)

    # Not asked for, or asked for on the one type Apple honours it on.
    _assert_family_sharing_supported(consumable, None)
    _assert_family_sharing_supported(
        SimpleNamespace(iap_type="NON_CONSUMABLE"), True,
    )


async def test_update_iap_body_shape_is_v2_and_partial():
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.update_iap("IAP_1", name="Coins")

    method, url, body = client.http.calls[0]
    assert method == "PATCH"
    assert url.endswith("/v2/inAppPurchases/IAP_1")
    data = body["data"]
    assert data["type"] == "inAppPurchases"
    assert data["id"] == "IAP_1"
    # Only what was passed — an unset field must not be nulled out at Apple.
    assert data["attributes"] == {"name": "Coins"}


async def test_family_sharing_uses_apples_spelling_everywhere():
    """Apple spells it ``familySharable`` — one 'e', not ``familyShareable``.

    We had the wrong spelling on create, update and the detail field list.
    Apple rejects the bad field name outright on reads
    (``'familyShareable' is not a valid field name``, seen live), and writes
    just never took effect — so IAP family sharing never worked.
    """
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.create_iap(
        app_id="APP_1", product_id="com.x.pro", name="Pro",
        iap_type="NON_CONSUMABLE", family_sharable=True,
    )
    await svc.update_iap("IAP_1", family_sharable=False)
    await svc.get_iap_detail("IAP_1")

    for method, url, body in client.http.calls:
        haystack = url if body is None else f"{url} {body}"
        assert "familyShareable" not in haystack, (method, haystack)

    create_attrs = client.http.calls[0][2]["data"]["attributes"]
    assert create_attrs["familySharable"] is True
    assert client.http.calls[1][2]["data"]["attributes"] == {
        "familySharable": False,
    }
    assert "familySharable" in client.http.calls[2][1]


async def test_delete_iap_uses_v2_and_delete_localization_uses_v1():
    """The IAP is a v2 resource; its localizations are still v1."""
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    await svc.delete_iap("IAP_1")
    await svc.delete_iap_localization("LOC_1")

    assert client.http.calls[0][1].endswith("/v2/inAppPurchases/IAP_1")
    assert client.v1_calls[0] == (
        "DELETE", "/inAppPurchaseLocalizations/LOC_1", None,
    )


async def test_delete_localization_guard_rejects_another_iaps_child(monkeypatch):
    """The IDOR shape: deleting by bare child id must not cross parents."""
    client = _RecordingClient()
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    async def _own_children(iap_id: str) -> list[dict]:
        return [{"id": "LOC_MINE"}]

    monkeypatch.setattr(svc, "list_iap_localizations", _own_children)

    with pytest.raises(ChildResourceNotFoundError):
        await svc.assert_iap_localization("IAP_1", "LOC_SOMEONE_ELSES")
    # The guard runs before the delete, so nothing was sent.
    assert client.v1_calls == []

    await svc.assert_iap_localization("IAP_1", "LOC_MINE")


# ---------------------------------------------------------------------------
# "Never priced" is a state, not an error
# ---------------------------------------------------------------------------


async def test_price_schedule_404_on_a_live_iap_reads_as_no_prices():
    """A fresh IAP has no schedule; that must not read as a failure.

    This is what made a product created through the API unpriceable: the
    404 propagated, so neither sync nor apply could get past it.
    """
    client = _RecordingClient()
    client.http.responses = {
        "/iapPriceSchedule": (404, _not_found_body("IAP_1")),
        # The IAP itself answers — so it exists, it just has no schedule.
        "/inAppPurchases/IAP_1?": (200, {"data": {"id": "IAP_1"}}),
    }
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    assert await svc.get_iap_price_schedule("IAP_1") == []


async def test_price_schedule_404_on_a_missing_iap_still_raises():
    """The same 404 body, but the IAP is gone — must stay loud.

    Apple returns an identical NOT_FOUND for both cases (verified live), so
    a stale local row pointing at a deleted IAP would otherwise silently
    report "no prices".
    """
    client = _RecordingClient()
    client.http.responses = {
        "/iapPriceSchedule": (404, _not_found_body("IAP_GONE")),
        "/inAppPurchases/IAP_GONE?": (404, _not_found_body("IAP_GONE")),
    }
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    with pytest.raises(ASCAPIError):
        await svc.get_iap_price_schedule("IAP_GONE")


async def test_price_schedule_non_404_errors_are_not_swallowed():
    """Only 404 means "no schedule" — a 403 is a real failure."""
    client = _RecordingClient()
    client.http.responses = {
        "/iapPriceSchedule": (403, {"errors": [{"detail": "Forbidden"}]}),
    }
    svc = ASCPricingService(client)  # type: ignore[arg-type]

    with pytest.raises(ASCAPIError) as err:
        await svc.get_iap_price_schedule("IAP_1")
    assert err.value.status_code == 403


# ---------------------------------------------------------------------------
# Local guards must not be confusable with an upstream decode failure
# ---------------------------------------------------------------------------


def test_local_guards_are_not_valueerror_subclasses():
    """Tripwire: ``json.JSONDecodeError`` IS a ``ValueError``.

    Both guards are raised from methods that also parse Apple's JSON. While
    they were plain ``ValueError``s, a malformed upstream body reached the
    caller as a confident 409/400 whose detail was a raw Python decode
    message — mislabelled, and the raw-error leak CLAUDE.md forbids.
    """
    assert not issubclass(IAPScheduleUnsyncedError, ValueError)
    assert not issubclass(ASCRequestInvalidError, ValueError)


async def test_a_non_json_error_body_still_becomes_an_asc_error():
    """A gateway's HTML 502 must not crash past every ``except ASCAPIError``."""
    class _HTMLResp:
        status_code = 502
        content = b"<html>Bad Gateway</html>"

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    with pytest.raises(ASCAPIError) as err:
        _raise_for_asc_error(_HTMLResp())  # type: ignore[arg-type]
    assert err.value.status_code == 502
    # No Python decode text leaks into the message users are shown.
    assert err.value.message == "ASC API error 502"


# ---------------------------------------------------------------------------
# Routes — the guard must become a 404, not a 500
# ---------------------------------------------------------------------------


def test_every_route_using_a_membership_guard_maps_it_to_404():
    """Tripwire for a guard that fires and escapes as an unhandled 500.

    ``delete_iap_localization`` shipped calling ``assert_iap_localization``
    outside its ``try``: the IDOR was correctly refused, but the caller got
    "Internal Server Error" instead of a 404 — and CLAUDE.md forbids leaking
    raw Python errors into responses.
    """
    import inspect

    from app.api.v1 import pricing as pricing_routes

    guarded = {
        name: inspect.getsource(fn)
        for name, fn in vars(pricing_routes).items()
        if inspect.iscoroutinefunction(fn)
        and "pricing_service.assert_" in inspect.getsource(fn)
    }
    assert guarded, "no guarded routes found — did the helper get renamed?"

    offenders = [
        name for name, src in guarded.items()
        if "ChildResourceNotFoundError" not in src
    ]
    assert not offenders, (
        f"routes calling a membership guard without catching it: {offenders}"
    )
