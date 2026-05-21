from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_BACKEND_URL = "http://localhost:8000"


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text()


def test_backend_dev_port_is_standardized_across_scripts_proxy_and_operator_docs():
    files_to_check = {
        "Makefile": ["--port 8000"],
        "CLAUDE.md": [":8000"],
        "backend/scripts/clone_all_subs.py": [CANONICAL_BACKEND_URL],
        "docs/007-mcp-integration.md": [CANONICAL_BACKEND_URL],
        "frontend/src/pages/SettingsPage.tsx": [f"{CANONICAL_BACKEND_URL}/mcp/"],
        "frontend/vite.config.ts": [CANONICAL_BACKEND_URL],
    }

    for rel_path, required_snippets in files_to_check.items():
        text = _read(rel_path)
        assert "8002" not in text, f"{rel_path} still references legacy dev port 8002"
        for snippet in required_snippets:
            assert snippet in text, f"{rel_path} is missing {snippet!r}"


def test_readme_documents_health_login_and_mcp_examples_on_canonical_url():
    readme = _read("README.md")

    assert "make dev          # backend :8000, frontend :5173" in readme
    assert f"curl -s {CANONICAL_BACKEND_URL}/health" in readme
    assert f"curl -s -X POST {CANONICAL_BACKEND_URL}/api/v1/auth/login" in readme
    assert f"{CANONICAL_BACKEND_URL}/mcp/" in readme
    assert "8002" not in readme


def test_main_preserves_documented_health_and_mcp_routes():
    main_py = _read("backend/app/main.py")

    assert "async with mcp_app.lifespan(app):" in main_py
    assert 'app.mount("/mcp", mcp_app)' in main_py
    assert '@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])' in main_py
    assert 'RedirectResponse(url="/mcp/", status_code=307)' in main_py
    assert '@app.get("/health")' in main_py
