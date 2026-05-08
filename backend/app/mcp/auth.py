"""PAT-based bearer auth for the MCP server.

Implements a :class:`fastmcp.server.auth.TokenVerifier` that looks the bearer
token up in the ``personal_access_tokens`` table by its sha256 hash and, on a
hit, returns an :class:`AccessToken` whose ``claims`` carry the resolved
``user_id`` so tools can scope their work without re-doing the lookup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from sqlalchemy import select

from app.core.security import hash_pat
from app.db.session import async_session_factory
from app.models.personal_access_token import PersonalAccessToken

logger = logging.getLogger(__name__)


class PATTokenVerifier(TokenVerifier):
    """Validate ``aso_pat_…`` tokens against the PersonalAccessToken table."""

    async def verify_token(self, token: str) -> AccessToken | None:
        token_hash = hash_pat(token)
        async with async_session_factory() as session:
            res = await session.execute(
                select(PersonalAccessToken).where(
                    PersonalAccessToken.token_hash == token_hash,
                )
            )
            pat = res.scalar_one_or_none()
            if pat is None or pat.revoked_at is not None:
                return None

            # Best-effort touch — never fail auth on a write hiccup.
            pat.last_used_at = datetime.now(timezone.utc)
            try:
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()
                logger.warning("Failed to update last_used_at for PAT id=%s", pat.id)

            return AccessToken(
                token=token,
                client_id=f"user:{pat.user_id}",
                scopes=[],
                expires_at=None,
                claims={"user_id": str(pat.user_id), "pat_id": str(pat.id)},
            )
