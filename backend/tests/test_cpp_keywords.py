"""CPP promotional text and search keywords (spec 017), through the MCP tools.

Drives ``cpp_ensure_localization``, ``cpp_keywords_get`` and ``cpp_keywords_set``
against the scripted ``FakeASCClient`` of ``test_cpp.py``: every call is
recorded with its body, so the tests pin what is sent to Apple and that a
refused call sends nothing.
"""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from app.mcp.tools import cpp as cpp_tools
from app.services.asc.cpp import crowded_terms
from tests._async_harness import run_async
from tests.test_cpp import FakeASCClient
from tests.test_mcp_screenshots import _fake_resolve_app, _fake_session_scope, _tool

CPP_ID = "cpp-1"
LOCALIZATION = "cloc-en"
LINKS = f"/appCustomProductPageLocalizations/{LOCALIZATION}/relationships/searchKeywords"


class _KeywordASC(FakeASCClient):
    """Unlinking sends a body, which the base fake's ``_delete`` does not take."""

    async def _delete(self, path: str, json: dict | None = None) -> None:
        self.calls.append(("DELETE", path, json))


def _client(
    current: list[str], offered: list[str], state: str = "PREPARE_FOR_SUBMISSION"
) -> _KeywordASC:
    return _KeywordASC({
        ("GET", f"/appCustomProductPages/{CPP_ID}/appCustomProductPageVersions"): {
            "data": [{"id": "cver-1", "attributes": {"state": state}}],
        },
        ("GET", "/appCustomProductPageVersions/cver-1/appCustomProductPageLocalizations"): {
            "data": [{"id": LOCALIZATION, "attributes": {"locale": "en-US"}}],
        },
        ("GET", LINKS): {"data": [{"type": "appKeywords", "id": k} for k in current]},
        ("GET", "/apps/asc-777/searchKeywords"): {
            "data": [{"type": "appKeywords", "id": k} for k in offered],
        },
        ("PATCH", f"/appCustomProductPageLocalizations/{LOCALIZATION}"): {
            "data": {"id": LOCALIZATION, "attributes": {"promotionalText": "Breathe in."}},
        },
    })


def _call(monkeypatch, client: FakeASCClient, name: str, **kwargs):
    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc) -> None:
            return None

    async def _client_for_app(app, session):
        return _Ctx()

    monkeypatch.setattr(cpp_tools, "session_scope", _fake_session_scope)
    monkeypatch.setattr(cpp_tools, "resolve_app", _fake_resolve_app)
    monkeypatch.setattr(cpp_tools, "_get_asc_client_for_app", _client_for_app)

    async def go():
        return await (await _tool(name)).fn(app_id=7, cpp_id=CPP_ID, locale="en-US", **kwargs)

    return run_async(go())


def _writes(client: FakeASCClient) -> list[tuple[str, str, dict | None]]:
    return [call for call in client.calls if call[0] in {"POST", "PATCH", "DELETE"}]


# ------------------------------------------------------------------
# Promotional text
# ------------------------------------------------------------------


def test_promotional_text_is_patched_onto_the_editable_localization(monkeypatch):
    client = _client([], [])

    result = _call(
        monkeypatch, client, "cpp_ensure_localization", promotional_text="Breathe in."
    )

    assert _writes(client) == [(
        "PATCH",
        f"/appCustomProductPageLocalizations/{LOCALIZATION}",
        {
            "data": {
                "type": "appCustomProductPageLocalizations",
                "id": LOCALIZATION,
                "attributes": {"promotionalText": "Breathe in."},
            }
        },
    )]
    assert result.localization_id == LOCALIZATION
    assert result.promotional_text == "Breathe in."


def test_promotional_text_over_170_characters_is_refused_before_any_call(monkeypatch):
    client = _client([], [])

    with pytest.raises(ToolError, match="171 characters"):
        _call(monkeypatch, client, "cpp_ensure_localization", promotional_text="x" * 171)
    assert client.calls == []


# ------------------------------------------------------------------
# Search keywords
# ------------------------------------------------------------------


def test_keywords_get_lists_the_assigned_and_the_offered_ids(monkeypatch):
    client = _client(["breathing"], ["breathing", "hrv"])

    result = _call(monkeypatch, client, "cpp_keywords_get")

    assert (result.keywords, result.available) == (["breathing"], ["breathing", "hrv"])


def test_keywords_get_reads_a_live_page_that_has_no_editable_version(monkeypatch):
    client = _client(["breathing"], ["breathing"], state="APPROVED")

    result = _call(monkeypatch, client, "cpp_keywords_get")

    assert (result.localization_id, result.keywords) == (LOCALIZATION, ["breathing"])


def test_keywords_set_diffs_warns_and_refuses_a_keyword_apple_does_not_offer(monkeypatch):
    client = _client(["breathing", "calm"], ["breathing", "calm", "meditation timer"])

    result = _call(
        monkeypatch,
        client,
        "cpp_keywords_set",
        keywords=["Breathing", "meditation timer", "sleep sounds"],
        apply=True,
    )

    rows = {row.keyword: row for row in result.rows}
    assert {keyword: row.action for keyword, row in rows.items()} == {
        "breathing": "keep",
        "meditation timer": "add",
        "sleep sounds": "error",
        "calm": "remove",
    }
    assert "timer" in rows["meditation timer"].warning
    assert (result.add, result.remove, result.keep) == (
        ["meditation timer"], ["calm"], ["breathing"],
    )
    assert result.applied is False
    assert _writes(client) == []


def test_keywords_set_is_a_dry_run_until_apply_then_links_and_unlinks(monkeypatch):
    client = _client(["breathing", "calm"], ["breathing", "calm", "hrv"])

    dry = _call(monkeypatch, client, "cpp_keywords_set", keywords=["breathing", "hrv"])
    assert dry.applied is False and _writes(client) == []

    applied = _call(
        monkeypatch, client, "cpp_keywords_set", keywords=["breathing", "hrv"], apply=True
    )

    assert applied.applied is True
    assert _writes(client) == [
        ("POST", LINKS, {"data": [{"type": "appKeywords", "id": "hrv"}]}),
        ("DELETE", LINKS, {"data": [{"type": "appKeywords", "id": "calm"}]}),
    ]
    assert applied.keywords is not None


def test_crowded_terms_match_whole_words_only():
    assert crowded_terms("Tarot reading") == ["tarot"]
    assert crowded_terms("focus timers") == ["timer"]
    assert crowded_terms("free sound effects") == ["sound effects"]
    assert crowded_terms("timeline") == []
