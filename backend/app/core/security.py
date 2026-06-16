import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.user import User

PAT_PREFIX = "aso_pat_"

# App-JWT signing algorithm. Hardcoded (not a config tunable) to remove an
# operational foot-gun: the decode allow-list below is pinned to this single
# value. ASC tokens use ES256 via PyJWT on a completely separate path.
JWT_ALGORITHM = "HS256"

bearer_scheme = HTTPBearer()

# Pre-computed bcrypt hash of a throwaway password. Used by the login path to
# spend roughly the same time verifying when the email does not exist, so the
# no-user branch is not measurably faster (user-enumeration via timing).
_DUMMY_PASSWORD = b"timing-equalizer"
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(_DUMMY_PASSWORD, bcrypt.gensalt())


def dummy_verify_password() -> None:
    """Run a bcrypt verify against a dummy hash to equalize login timing.

    Called on the no-such-user branch so it costs the same as a real
    ``verify_password`` against a stored hash.
    """
    bcrypt.checkpw(_DUMMY_PASSWORD, _DUMMY_PASSWORD_HASH)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# Fernet encryption (for .p8 key storage)
# ---------------------------------------------------------------------------


def _get_fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="FERNET_KEY is not configured",
        )
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_value(plaintext: str) -> str:
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# FastAPI dependency: get current user from Authorization header
# ---------------------------------------------------------------------------

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def load_active_user(sub: Any, session: AsyncSession) -> User:
    """Resolve a JWT ``sub`` claim to an active ``User`` row.

    Parses ``sub`` to an int, loads the user, and enforces that it exists and
    ``is_active``. Raises 401 (never 500) on any failure so a deactivated or
    deleted user can no longer ride a still-valid token.
    """
    if sub is None or not isinstance(sub, str) or not sub.isdigit():
        raise _INVALID_CREDENTIALS
    user = (
        await session.execute(select(User).where(User.id == int(sub)))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _INVALID_CREDENTIALS
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
    # Enforce that the subject is a real, active user (not just a valid sig).
    user = await load_active_user(payload.get("sub"), session)
    # Preserve the historical return contract: callers depend on
    # ``current_user["user_id"]`` being the string-form subject, plus the raw
    # JWT claims spread alongside it.
    return {"user_id": str(user.id), **payload}


# ---------------------------------------------------------------------------
# Personal Access Tokens (long-lived bearer tokens for headless/MCP clients)
# ---------------------------------------------------------------------------


def generate_pat() -> tuple[str, str]:
    """Mint a new PAT. Returns (plaintext, hash)."""
    raw = secrets.token_urlsafe(32)
    plaintext = f"{PAT_PREFIX}{raw}"
    return plaintext, hash_pat(plaintext)


def hash_pat(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
