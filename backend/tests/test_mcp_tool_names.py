"""Guard: every registered MCP name must satisfy the Anthropic tool-name regex.

The Anthropic API rejects any tool name containing a character outside
``^[a-zA-Z0-9_-]{1,64}$`` — notably a **dot**. A single dotted name makes the
API reject the *entire* MCP server (``tools.<n>.FrontendRemoteMcpToolDefinition
.name: String should match pattern '^[a-zA-Z0-9_-]{1,64}$'``), which is why the
server's tools use underscores, not dots (see ``docs/000-changelog.md``).

Claude Code hides the problem by rewriting ``.``→``_`` before the API call, so a
dotted name can regress unnoticed there — but Claude Desktop passes names
through verbatim and breaks. This test is the tripwire: it enumerates the
*actually registered* names and fails the build if any name violates the regex.
"""
from __future__ import annotations

import re

from app.mcp.server import mcp
from tests._async_harness import run_async

# The exact pattern the Anthropic API enforces on tool/prompt names.
ANTHROPIC_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _registered_names(kind: str) -> list[str]:
    """Return the registered names for ``kind`` ('tools' or 'prompts').

    FastMCP's public ``list_tools`` / ``list_prompts`` coroutines are not
    reliably attribute-accessible in every import context (they resolve under a
    plain ``python -c`` import but not under pytest collection in fastmcp 3.x),
    so this falls back to the underscore-prefixed variant — using
    ``getattr(..., None)`` so a missing public method never raises before the
    fallback is tried. Both return objects carrying a ``.name``.
    """
    getter = getattr(mcp, f"list_{kind}", None) or getattr(mcp, f"_list_{kind}")
    return [item.name for item in run_async(getter())]


def test_all_mcp_tool_names_match_anthropic_pattern():
    names = _registered_names("tools")
    # Sanity: the registry must be populated, else the guard passes vacuously.
    assert len(names) > 100, f"expected the full tool registry, got {len(names)}"

    invalid = [n for n in names if not ANTHROPIC_NAME_PATTERN.fullmatch(n)]
    assert not invalid, (
        "MCP tool names must match "
        f"{ANTHROPIC_NAME_PATTERN.pattern} (no dots) — offending names: {invalid}"
    )


def test_all_mcp_prompt_names_match_anthropic_pattern():
    names = _registered_names("prompts")
    invalid = [n for n in names if not ANTHROPIC_NAME_PATTERN.fullmatch(n)]
    assert not invalid, (
        "MCP prompt names must match "
        f"{ANTHROPIC_NAME_PATTERN.pattern} (no dots) — offending names: {invalid}"
    )
