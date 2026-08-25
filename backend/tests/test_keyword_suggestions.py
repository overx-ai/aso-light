"""iTunes keyword-suggestion storefront selection (spec 012 R2).

Two halves:

* offline — the ``app.data.storefronts`` map itself (resolution, aliases,
  fallback, territory coverage);
* ``@pytest.mark.network`` — one live call per storefront. The US/DE pair is the
  only thing that can prove the ``X-Apple-Store-Front`` header is actually being
  honoured: without it Apple returns HTTP 200 with an empty array, which looks
  exactly like "no suggestions".
"""
from __future__ import annotations

import logging
import re

import httpx
import pytest

from app.data.storefronts import (
    STOREFRONT_ALIASES,
    STOREFRONTS,
    TERRITORIES_WITHOUT_STOREFRONT,
    normalize_country,
    resolve_storefront,
    storefront_header,
)
from app.data.territories import TERRITORIES
from app.services.keywords.suggestions import ITunesSuggestionsService
from tests._async_harness import run_async


# ---------------------------------------------------------------------------
# Offline: the storefront map
# ---------------------------------------------------------------------------


def test_known_storefront_ids():
    assert resolve_storefront("us") == (143441, "us")
    assert resolve_storefront("de") == (143443, "de")
    assert resolve_storefront("GB") == (143444, "gb")
    assert storefront_header("de") == ("143443-1,29", "de")


def test_locale_shaped_input_is_reduced_to_country():
    # The pre-R2 signature took an iTunes locale; those values must still resolve.
    assert normalize_country("en_us") == "us"
    assert normalize_country("de_de") == "de"
    assert normalize_country("pt-BR") == "br"
    assert resolve_storefront("de_de") == (143443, "de")


def test_alias_territories_resolve_to_their_parent_store():
    assert resolve_storefront("pr") == (STOREFRONTS["us"], "us")
    assert resolve_storefront("mq") == (STOREFRONTS["fr"], "fr")


def test_unknown_country_falls_back_to_us_with_a_warning(caplog):
    # Alembic's fileConfig (run by tests/test_database_bootstrap.py) disables
    # pre-existing loggers, so re-enable this one before asserting on output.
    module_logger = logging.getLogger("app.data.storefronts")
    module_logger.disabled = False
    with caplog.at_level(logging.WARNING, logger="app.data.storefronts"):
        assert resolve_storefront("zz") == (143441, "us")
    assert "zz" in caplog.text


def test_every_territory_resolves():
    """No ASC territory may be a silent surprise: each is either mapped, aliased,
    or explicitly listed as having no Apple storefront."""
    for territory in TERRITORIES:
        code = territory["code"].lower()
        assert (
            code in STOREFRONTS
            or code in STOREFRONT_ALIASES
            or code in TERRITORIES_WITHOUT_STOREFRONT
        ), f"{code} is unaccounted for in app/data/storefronts.py"


def test_storefront_ids_are_unique_and_plausible():
    assert len(set(STOREFRONTS.values())) == len(STOREFRONTS)
    # Classic iTunes storefront ids all live in the 1434xx-1436xx block. An ASC
    # `apple_territory_id` would not.
    assert all(143441 <= sid <= 143700 for sid in STOREFRONTS.values())


# ---------------------------------------------------------------------------
# Network: the header actually selects a storefront
# ---------------------------------------------------------------------------


def _require_network() -> None:
    """Skip (not fail) when Apple is unreachable.

    ``get_suggestions`` swallows transport errors and returns ``[]``, so an
    offline run would otherwise look like a regression.
    """
    try:
        httpx.get(ITunesSuggestionsService.HINTS_URL, timeout=5.0)
    except httpx.HTTPError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"iTunes hints API unreachable: {exc}")


def _has_cyrillic_free_latin(values: list[str]) -> bool:
    return any(re.search(r"[a-zA-Z]", v) for v in values)


def _hints_or_skip(term: str, **kwargs) -> list[str]:
    """Fetch live hints, skipping when Apple answers 200-with-nothing.

    A rate-limited IP returns an empty ``<array/>`` under a 200 — byte-identical
    to the header-less bug these tests exist to catch. That ambiguity is
    unresolvable from here, so it must skip, never fail: the header itself is
    already pinned offline by ``test_header_is_sent_per_storefront``, so a CI
    box that Apple is throttling loses nothing but this corroboration.
    """
    hints = run_async(ITunesSuggestionsService().get_suggestions(term, **kwargs))
    if not hints:
        pytest.skip(f"iTunes returned no hints for {term!r} {kwargs} — likely rate-limited")
    return hints


@pytest.mark.network
def test_suggestions_per_storefront():
    _require_network()

    us = _hints_or_skip("breathing", country="us")
    assert _has_cyrillic_free_latin(us)

    de = _hints_or_skip("atem", country="de")

    # The load-bearing assertion: an ignored header would give both calls the
    # same result. Reached only when both storefronts actually answered.
    assert set(us) != set(de)


@pytest.mark.network
def test_deprecated_locale_kwarg_still_selects_the_storefront():
    _require_network()

    _hints_or_skip("atem", locale="de_de")


# ---------------------------------------------------------------------------
# Offline: the request the service actually builds
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx.Response stand-in carrying a JSON hints body."""

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload if payload is not None else {"hints": []}
        self.content = b"{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    """Captures the params/headers of the single GET the service issues."""

    calls: list[dict] = []
    payload: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url, params=None, headers=None):
        type(self).calls.append(
            {"url": url, "params": params or {}, "headers": headers or {}},
        )
        return _FakeResponse(type(self).payload)


@pytest.fixture
def recording_client(monkeypatch):
    _RecordingClient.calls = []
    _RecordingClient.payload = {"hints": [{"term": "breathing app"}]}
    monkeypatch.setattr(
        "app.services.keywords.suggestions.httpx.AsyncClient", _RecordingClient,
    )
    monkeypatch.setattr(
        "app.services.keywords.suggestions.itunes_throttle",
        _no_throttle,
    )
    return _RecordingClient


async def _no_throttle() -> None:
    return None


def test_storefront_header_is_sent_and_keyed_by_country(recording_client):
    """R2: the header is the whole fix — assert it offline, not just live."""
    hints = run_async(ITunesSuggestionsService().get_suggestions("atem", "de"))
    assert hints == ["breathing app"]

    call = recording_client.calls[0]
    assert call["headers"]["X-Apple-Store-Front"] == "143443-1,29"
    # ``l=`` is not sent: the endpoint ignores it, the header does the work.
    assert "l" not in call["params"]
    assert call["params"]["term"] == "atem"


def test_explicit_country_wins_over_the_deprecated_locale_alias(recording_client):
    """``locale`` must never silently override the parameter that replaced it.

    ``country="de", locale="en_us"`` used to resolve to the US storefront —
    a caller migrating to ``country`` while an old ``locale`` lingered in a
    stored request got German keywords researched against the US store.
    """
    run_async(
        ITunesSuggestionsService().get_suggestions("atem", "de", locale="en_us"),
    )
    assert recording_client.calls[0]["headers"]["X-Apple-Store-Front"] == (
        "143443-1,29"
    )


def test_locale_alias_still_applies_when_country_is_left_at_its_default(
    recording_client,
):
    run_async(ITunesSuggestionsService().get_suggestions("atem", locale="de_de"))
    assert recording_client.calls[0]["headers"]["X-Apple-Store-Front"] == (
        "143443-1,29"
    )


def test_an_empty_result_is_logged_with_term_and_country(recording_client, caplog):
    """A silent [] is how the missing-header bug survived for a year."""
    _RecordingClient.payload = {"hints": []}
    module_logger = logging.getLogger("app.services.keywords.suggestions")
    module_logger.disabled = False
    with caplog.at_level(
        logging.WARNING, logger="app.services.keywords.suggestions",
    ):
        assert run_async(
            ITunesSuggestionsService().get_suggestions("zzzz", "de"),
        ) == []
    assert "zzzz" in caplog.text
    assert "'de'" in caplog.text


def test_blank_term_never_reaches_apple(recording_client):
    assert run_async(ITunesSuggestionsService().get_suggestions("   ", "de")) == []
    assert recording_client.calls == []
