from pathlib import Path
import tomllib

from app.core.security import hash_password, verify_password


def test_backend_packaging_has_no_passlib_dependency() -> None:
    backend_dir = Path(__file__).resolve().parents[1]

    with (backend_dir / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    dependencies = pyproject["project"]["dependencies"]
    assert any(dependency == "bcrypt" for dependency in dependencies)
    assert all(not dependency.startswith("passlib") for dependency in dependencies)

    lockfile_text = (backend_dir / "uv.lock").read_text()
    assert 'name = "bcrypt"' in lockfile_text
    assert "passlib[bcrypt]" not in lockfile_text
    assert 'name = "passlib"' not in lockfile_text


def test_password_hash_round_trip_uses_bcrypt() -> None:
    password = "correct horse battery staple"
    hashed_password = hash_password(password)

    assert hashed_password != password
    assert hashed_password.startswith("$2")
    assert verify_password(password, hashed_password) is True
    assert verify_password("wrong-password", hashed_password) is False
