"""Guards the ``filter[appStoreState]`` vocabulary.

Apple maintains two different vocabularies for one concept. ``appVersionState``
(the attribute) gained ``READY_FOR_DISTRIBUTION``; ``filter[appStoreState]``
(the query param) never did. Sending it returns

    'READY_FOR_DISTRIBUTION' is not a valid filter value. Expected one of: ...

and because the snapshot's fallback fetch passed
``READ_ONLY_VERSION_STATES_PROMO_ONLY`` straight through as a filter, that one
bad value failed *every* ``metadata_sync`` for a live app — the exact state
every shipped app is in. The cache then silently served months-old metadata,
which is worse than an error: it looks like it worked.
"""
from __future__ import annotations

from app.services.metadata.client import (
    ASCMetadataService,
    EDITABLE_VERSION_STATES,
    FILTERABLE_VERSION_STATES,
    READ_ONLY_VERSION_STATES_PROMO_ONLY,
)
from tests._async_harness import run_async


class _RecordingClient:
    """Captures the params of the last ``_get_all_pages`` call."""

    def __init__(self) -> None:
        self.params: dict | None = None

    async def _get_all_pages(self, path: str, params: dict) -> list[dict]:
        self.params = params
        return []


def _call(filter_states: list[str] | None) -> dict:
    client = _RecordingClient()
    service = ASCMetadataService(client)  # type: ignore[arg-type]
    run_async(service.list_app_store_versions("123", filter_states=filter_states))
    assert client.params is not None
    return client.params


def test_unfilterable_state_is_dropped_not_sent():
    params = _call(["READY_FOR_SALE", "READY_FOR_DISTRIBUTION"])
    assert params["filter[appStoreState]"] == "READY_FOR_SALE"


def test_all_unfilterable_degrades_to_no_filter_not_an_error():
    """A broader query the caller narrows itself beats a 409 that fails sync."""
    params = _call(["READY_FOR_DISTRIBUTION"])
    assert "filter[appStoreState]" not in params
    assert params["limit"] == 200


def test_no_filter_states_sends_no_filter():
    assert "filter[appStoreState]" not in _call(None)


def test_editable_states_are_all_filterable():
    """The primary sync path must never be degraded by the intersection."""
    assert EDITABLE_VERSION_STATES <= FILTERABLE_VERSION_STATES


def test_promo_only_states_survive_the_intersection():
    """The fallback fetch must still filter — just without the bad value."""
    survivors = READ_ONLY_VERSION_STATES_PROMO_ONLY & FILTERABLE_VERSION_STATES
    assert "READY_FOR_SALE" in survivors
    assert "PENDING_DEVELOPER_RELEASE" in survivors
    # The one Apple rejects is the one that must not survive.
    assert "READY_FOR_DISTRIBUTION" not in survivors


def test_read_only_set_still_names_the_state_apple_reports():
    """Editability is a separate question from wire-safety.

    ``READY_FOR_DISTRIBUTION`` must stay in the editability set or a tenant
    reporting it would fail closed and lose ``promotional_text``.
    """
    assert "READY_FOR_DISTRIBUTION" in READ_ONLY_VERSION_STATES_PROMO_ONLY
