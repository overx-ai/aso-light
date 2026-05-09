import ast
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class LocalDevConfigTest(unittest.TestCase):
    def test_canonical_backend_port_is_used_across_dev_surfaces(self) -> None:
        files_to_check = {
            "Makefile": read_repo_file("Makefile"),
            "README.md": read_repo_file("README.md"),
            "frontend/vite.config.ts": read_repo_file("frontend/vite.config.ts"),
            "frontend/src/pages/SettingsPage.tsx": read_repo_file(
                "frontend/src/pages/SettingsPage.tsx"
            ),
            "backend/scripts/clone_all_subs.py": read_repo_file(
                "backend/scripts/clone_all_subs.py"
            ),
        }

        self.assertIn("DEV_BACKEND_PORT ?= 8000", files_to_check["Makefile"])
        self.assertIn(
            "--port $(DEV_BACKEND_PORT)",
            files_to_check["Makefile"],
        )

        self.assertIn(
            "Canonical local backend URL: `http://localhost:8000`",
            files_to_check["README.md"],
        )
        self.assertIn(
            "curl -X POST http://localhost:8000/api/v1/auth/register",
            files_to_check["README.md"],
        )
        self.assertIn(
            "curl -X POST http://localhost:8000/api/v1/auth/login",
            files_to_check["README.md"],
        )
        self.assertIn(
            "curl http://localhost:8000/health",
            files_to_check["README.md"],
        )
        self.assertIn(
            '"url": "http://localhost:8000/mcp"',
            files_to_check["README.md"],
        )

        self.assertIn(
            'target: "http://localhost:8000"',
            files_to_check["frontend/vite.config.ts"],
        )
        self.assertIn(
            '"url": "http://localhost:8000/mcp"',
            files_to_check["frontend/src/pages/SettingsPage.tsx"],
        )
        self.assertIn(
            'DEFAULT_BASE_URL = os.getenv("ASOLIGHT_BASE_URL", "http://localhost:8000")',
            files_to_check["backend/scripts/clone_all_subs.py"],
        )

        for relative_path, content in files_to_check.items():
            self.assertNotIn(
                "8002",
                content,
                msg=f"{relative_path} should not advertise the old backend port",
            )

    def test_mcp_mount_preserves_lifespan_and_slash_redirect(self) -> None:
        main_py = read_repo_file("backend/app/main.py")

        self.assertIn("async with mcp_app.lifespan(app):", main_py)
        self.assertIn("redirect_slashes=False", main_py)
        self.assertIn('"/mcp"', main_py)
        self.assertIn('RedirectResponse(url="/mcp/", status_code=307)', main_py)
        self.assertIn('app.mount("/mcp", mcp_app)', main_py)


def test_app_lifespan_wraps_mcp_lifespan() -> None:
    tree = ast.parse(read_repo_file("backend/app/main.py"))
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )

    wrapped_mcp_lifespan = False
    for async_with in (node for node in ast.walk(lifespan) if isinstance(node, ast.AsyncWith)):
        for item in async_with.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            if not isinstance(context_expr.func, ast.Attribute):
                continue
            if context_expr.func.attr != "lifespan":
                continue
            if not isinstance(context_expr.func.value, ast.Name):
                continue
            if context_expr.func.value.id != "mcp_app":
                continue
            if len(context_expr.args) != 1:
                continue
            if not isinstance(context_expr.args[0], ast.Name):
                continue
            if context_expr.args[0].id != "app":
                continue
            wrapped_mcp_lifespan = True
            break

    assert wrapped_mcp_lifespan


if __name__ == "__main__":
    unittest.main()
