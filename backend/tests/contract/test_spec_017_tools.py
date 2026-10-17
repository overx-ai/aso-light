"""Contract: the MCP tool schemas spec 017 publishes (consumed by hero-ai and agents).

A rename, a dropped parameter or a changed default here breaks callers that
pinned spec 017, so each published tool's inputs and key outputs are fixed.
"""
from __future__ import annotations

import pytest

from tests._async_harness import run_async
from tests.test_mcp_screenshots import _tool

SYNC_ARGS = {"app_id", "dir", "locales", "preview_types", "apply"}

PUBLISHED = {
    "cpp_keywords_get": ({"app_id", "cpp_id", "locale"}, set()),
    "cpp_keywords_set": ({"app_id", "cpp_id", "locale", "keywords"}, {"apply"}),
    "previews_sync": ({"app_id", "dir"}, SYNC_ARGS - {"app_id", "dir"}),
    "cpp_previews_sync": ({"app_id", "cpp_id", "dir"}, SYNC_ARGS - {"app_id", "dir"}),
    "experiment_previews_sync": (
        {"app_id", "experiment_id", "treatment_id", "dir"},
        SYNC_ARGS - {"app_id", "dir"},
    ),
}


@pytest.mark.parametrize("name", sorted(PUBLISHED))
def test_published_tool_inputs(name):
    required, optional = PUBLISHED[name]
    schema = run_async(_tool(name)).parameters

    assert set(schema.get("required", [])) == required
    assert set(schema["properties"]) == required | optional
    if "apply" in optional:
        assert schema["properties"]["apply"]["default"] is False


def test_cpp_ensure_localization_takes_promotional_text():
    properties = run_async(_tool("cpp_ensure_localization")).parameters["properties"]

    assert "promotional_text" in properties


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("cpp_keywords_set", {"add", "remove", "keep", "rows", "applied", "keywords"}),
        ("cpp_keywords_get", {"keywords", "available", "localization_id"}),
        ("previews_sync", {"rows", "applied", "untouched", "inventory", "version_id"}),
    ],
)
def test_published_tool_outputs(name, fields):
    output = run_async(_tool(name)).output_schema

    assert fields <= set(output["properties"])
