"""Pytest bootstrap: isolate tests from the dev DB.

Test modules import ``async_session_factory``/``engine`` at module load,
which binds them to whatever ``DATABASE_URL`` resolves to at that moment.
This conftest runs before any test module is imported, so it can rewrite
the relevant env vars to point at a throwaway SQLite file.

Why this matters: ``tests/test_asa_joins.py`` writes real ``ASCCredential``
rows with ``encrypt_value("k")`` as the .p8 placeholder. Without isolation,
those rows accumulate in the dev DB and crash any ASC-touching code path
(REST or MCP) that later tries to decrypt them as a private key.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "aso_light_pytest.db"
if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-prod")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-not-for-prod")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
