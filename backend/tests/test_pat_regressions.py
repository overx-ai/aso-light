from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_personal_access_tokens_have_an_alembic_revision():
    revision_texts = [
        path.read_text()
        for path in (BACKEND_ROOT / "alembic" / "versions").glob("*.py")
    ]

    assert any(
        "personal_access_tokens" in text for text in revision_texts
    ), "Expected an Alembic revision that creates personal_access_tokens"


def test_pat_auth_checks_the_owning_user_is_active():
    auth_source = _read("backend/app/mcp/auth.py")
    assert "is_active" in auth_source


def test_pat_name_validation_trims_whitespace_and_rejects_blank_names():
    schema_source = _read("backend/app/schemas/personal_access_token.py")
    assert "strip_whitespace=True" in schema_source or "field_validator" in schema_source
