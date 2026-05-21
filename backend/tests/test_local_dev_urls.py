from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_BACKEND_URL = "http://localhost:8000"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_local_dev_port_is_standardized_across_scripts_and_docs():
    expected_snippets = {
        "Makefile": ["--port 8000"],
        "README.md": [
            "make dev          # backend :8000, frontend :5173",
            "uv run uvicorn app.main:app --reload --port 8000",
        ],
        "CLAUDE.md": [
            "make dev` (starts backend on :8000, frontend on :5173)",
            "uv run uvicorn app.main:app --reload --port 8000",
        ],
        "frontend/vite.config.ts": [CANONICAL_BACKEND_URL],
        "backend/scripts/clone_all_subs.py": [CANONICAL_BACKEND_URL],
    }

    for relative_path, snippets in expected_snippets.items():
        text = _read(relative_path)
        for snippet in snippets:
            assert snippet in text, f"{relative_path} should contain {snippet!r}"
        assert "8002" not in text, f"{relative_path} still references port 8002"


def test_documented_health_login_and_mcp_examples_use_canonical_dev_url():
    readme = _read("README.md")
    assert f"curl -s {CANONICAL_BACKEND_URL}/health" in readme

    mcp_guide = _read("docs/007-mcp-integration.md")
    assert f"curl -s -X POST {CANONICAL_BACKEND_URL}/api/v1/auth/login" in mcp_guide
    assert f"curl -s -X POST {CANONICAL_BACKEND_URL}/api/v1/auth/tokens" in mcp_guide
    assert f"\"url\": \"{CANONICAL_BACKEND_URL}/mcp/\"" in mcp_guide
    assert "http://localhost:8002" not in mcp_guide

    settings_page = _read("frontend/src/pages/SettingsPage.tsx")
    assert f"\"url\": \"{CANONICAL_BACKEND_URL}/mcp/\"" in settings_page
