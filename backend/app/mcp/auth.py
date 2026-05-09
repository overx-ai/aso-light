from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from sqlalchemy import select

from app.db.session import async_session_factory
from app.mcp.context import set_user_id
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PATTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        token_hash = _hash_token(token)

        async with async_session_factory() as session:
            result = await session.execute(
                select(PersonalAccessToken)
                .join(PersonalAccessToken.user)
                .where(
                    PersonalAccessToken.token_hash == token_hash,
                    PersonalAccessToken.revoked_at.is_(None),
                    User.is_active.is_(True),
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None

            row.last_used_at = datetime.now(timezone.utc)
            await session.commit()

            set_user_id(row.user_id)
            return AccessToken(
                token=token,
                client_id=f"user-{row.user_id}",
                scopes=["mcp"],
            )
