"""The consent gate must refuse every destructive tool call it has not approved.

Every other destructive test in this suite asserts that a delete *fires*. These
assert the opposite: that nothing destructive runs without a token, that a token
covers exactly one call, and that no destructive tool can be added without being
registered in the gate.
"""
from __future__ import annotations

import dataclasses
import time

import mcp.types as mt
import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext

from app.mcp import consent
from app.mcp.consent import CONFIRM_ARG, DESTRUCTIVE, ConsentGate, reset_consent_state
from app.mcp.server import mcp
from tests._async_harness import run_async

GATED = "screenshots_delete"
READ = "metadata_get_snapshot"


class _FakeToken:
    def __init__(self, user_id: str) -> None:
        self.claims = {"user_id": user_id}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_consent_state()
    monkeypatch.setattr(consent, "get_access_token", lambda: _FakeToken("2"))
    yield
    reset_consent_state()


def _ctx(tool: str, arguments: dict) -> MiddlewareContext:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=tool, arguments=arguments))


def _call(tool: str, arguments: dict) -> tuple[object, list]:
    """Drive the gate; return (result, calls-that-reached-the-tool)."""
    seen: list = []

    async def call_next(context):
        seen.append(dict(context.message.arguments or {}))
        return "EXECUTED"

    result = run_async(ConsentGate().on_call_tool(_ctx(tool, arguments), call_next))
    return result, seen


def _token_from(exc: ToolError) -> str:
    """Pull the minted token out of the challenge text."""
    text = str(exc)
    assert f'{CONFIRM_ARG}="' in text, text
    return text.split(f'{CONFIRM_ARG}="', 1)[1].split('"', 1)[0]


def _challenge_for(tool: str, arguments: dict) -> str:
    with pytest.raises(ToolError) as err:
        _call(tool, arguments)
    return _token_from(err.value)


# --------------------------------------------------------------- pass-through

def test_read_tool_is_untouched():
    result, seen = _call(READ, {"app_id": 3})
    assert result == "EXECUTED"
    assert seen == [{"app_id": 3}]


# ------------------------------------------------------------------ challenge

def test_destructive_call_without_consent_does_not_execute():
    seen: list = []

    async def call_next(context):
        seen.append(context)
        return "EXECUTED"

    with pytest.raises(ToolError) as err:
        run_async(ConsentGate().on_call_tool(
            _ctx(GATED, {"app_id": 3, "delete_all": True}), call_next))

    assert seen == [], "the tool ran despite having no consent"
    text = str(err.value)
    assert "CONSENT REQUIRED" in text
    # The impact statement, not just a generic refusal.
    assert "Apple does not" in text
    assert '"delete_all": true' in text


def test_consent_token_lets_the_call_through_and_strips_confirm():
    args = {"app_id": 3, "delete_all": True}
    token = _challenge_for(GATED, args)

    result, seen = _call(GATED, {**args, CONFIRM_ARG: token})
    assert result == "EXECUTED"
    # `confirm` is the gate's argument, never the tool's.
    assert seen == [args]


# ------------------------------------------------- consent is per-operation

def test_token_is_single_use():
    args = {"app_id": 3}
    token = _challenge_for(GATED, args)
    _call(GATED, {**args, CONFIRM_ARG: token})

    with pytest.raises(ToolError, match="unknown or already used"):
        _call(GATED, {**args, CONFIRM_ARG: token})


def test_repeat_of_an_approved_call_needs_a_fresh_token():
    """Consent is never a session unlock: the identical call must re-consent."""
    args = {"app_id": 3}
    _call(GATED, {**args, CONFIRM_ARG: _challenge_for(GATED, args)})

    seen: list = []

    async def call_next(context):
        seen.append(context)
        return "EXECUTED"

    with pytest.raises(ToolError):
        run_async(ConsentGate().on_call_tool(_ctx(GATED, args), call_next))
    assert seen == []


def test_token_is_bound_to_the_exact_arguments():
    token = _challenge_for(GATED, {"app_id": 3, "locale": "en-US"})
    with pytest.raises(ToolError, match="does not match this call"):
        _call(GATED, {"app_id": 3, "locale": "de-DE", CONFIRM_ARG: token})


def test_token_is_bound_to_the_tool():
    token = _challenge_for(GATED, {"app_id": 3})
    with pytest.raises(ToolError, match="does not match this call"):
        _call("cpp_delete", {"app_id": 3, CONFIRM_ARG: token})


def test_argument_order_does_not_change_the_fingerprint():
    token = _challenge_for(GATED, {"app_id": 3, "locale": "en-US"})
    result, _ = _call(GATED, {"locale": "en-US", "app_id": 3, CONFIRM_ARG: token})
    assert result == "EXECUTED"


def test_token_is_bound_to_the_user(monkeypatch):
    token = _challenge_for(GATED, {"app_id": 3})
    monkeypatch.setattr(consent, "get_access_token", lambda: _FakeToken("999"))
    with pytest.raises(ToolError, match="different user"):
        _call(GATED, {"app_id": 3, CONFIRM_ARG: token})


def test_expired_token_is_refused():
    token = _challenge_for(GATED, {"app_id": 3})
    # Age the record rather than patching time.monotonic — asyncio's event loop
    # calls it too, and patching it globally recurses.
    consent._pending[token] = dataclasses.replace(
        consent._pending[token], expires_at=time.monotonic() - 1)
    with pytest.raises(ToolError, match="expired"):
        _call(GATED, {"app_id": 3, CONFIRM_ARG: token})


def test_unknown_token_is_refused():
    with pytest.raises(ToolError, match="unknown or already used"):
        _call(GATED, {"app_id": 3, CONFIRM_ARG: "not-a-real-token"})


# ------------------------------------------------------------- registry guards

def _registered_names() -> set[str]:
    getter = getattr(mcp, "list_tools", None) or getattr(mcp, "_list_tools")
    return {tool.name for tool in run_async(getter())}


def test_every_gated_name_is_a_real_tool():
    """A typo in DESTRUCTIVE would silently gate nothing."""
    assert not DESTRUCTIVE.keys() - _registered_names()


def test_no_destructive_shaped_tool_escapes_the_gate():
    """Tripwire: a new delete/archive tool cannot ship ungated.

    If this fails, either add the tool to DESTRUCTIVE or, if it is genuinely
    safe, add it to REVIEWED_SAFE with the reason.
    """
    reviewed_safe = {
        "presets_delete",            # local preset row, trivially recreated
        "swap_suggest_new_product_id",  # returns advice, writes nothing
    }
    shaped = {
        name for name in _registered_names()
        if any(k in name for k in
               ("delete", "remove", "archive", "detach", "_apply", "bulk_sync"))
    }
    assert not shaped - DESTRUCTIVE.keys() - reviewed_safe


def test_gated_tools_are_annotated_destructive():
    """MCP clients read destructiveHint to decide whether to prompt a human."""
    async def call_next(context):
        getter = getattr(mcp, "list_tools", None) or getattr(mcp, "_list_tools")
        return await getter()

    tools = run_async(ConsentGate().on_list_tools(
        MiddlewareContext(message=None), call_next))
    by_name = {t.name: t for t in tools}

    assert by_name[GATED].annotations.destructiveHint is True
    assert by_name[GATED].annotations.readOnlyHint is False
    assert by_name[READ].annotations is None or \
        not by_name[READ].annotations.destructiveHint
