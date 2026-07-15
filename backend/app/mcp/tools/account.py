"""MCP tools for inspecting the authenticated account context."""

from __future__ import annotations

from collections import defaultdict

from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.mcp.context import get_pat_id, get_user_id, session_scope
from app.mcp.server import mcp
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User
from app.schemas.account import (
    AccountAppSummary,
    AccountCredentialSummary,
    AccountPersonalAccessTokenSummary,
    AccountUserSummary,
    AccountWhoAmIResponse,
)


@mcp.tool(name="account_whoami")
async def whoami_tool() -> AccountWhoAmIResponse:
    """Show which user, PAT, ASC credentials, and apps the current MCP call can see."""
    async with session_scope() as session:
        user_id = get_user_id()
        pat_id = get_pat_id()

        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ToolError("Authenticated user no longer exists")

        personal_access_token = (
            await session.execute(
                select(PersonalAccessToken).where(
                    PersonalAccessToken.id == pat_id,
                    PersonalAccessToken.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if personal_access_token is None:
            raise ToolError("Authenticated personal access token no longer exists")

        credentials = list((
            await session.execute(
                select(ASCCredential)
                .where(ASCCredential.user_id == user_id)
                .order_by(ASCCredential.id)
            )
        ).scalars().all())
        credential_ids = [credential.id for credential in credentials]

        apps: list[App] = []
        if credential_ids:
            apps = list((
                await session.execute(
                    select(App)
                    .where(App.credential_id.in_(credential_ids))
                    .order_by(App.credential_id, App.id)
                )
            ).scalars().all())

        apps_count_by_credential: dict[int, int] = defaultdict(int)
        for app in apps:
            apps_count_by_credential[app.credential_id] += 1

        return AccountWhoAmIResponse(
            user=AccountUserSummary(
                id=user.id,
                email=user.email,
                name=user.name,
            ),
            personal_access_token=AccountPersonalAccessTokenSummary(
                id=personal_access_token.id,
                name=personal_access_token.name,
                created_at=personal_access_token.created_at,
                last_used_at=personal_access_token.last_used_at,
            ),
            credential_count=len(credentials),
            app_count=len(apps),
            asc_credentials=[
                AccountCredentialSummary(
                    id=credential.id,
                    name=credential.name,
                    issuer_id=credential.issuer_id,
                    key_id=credential.key_id,
                    apps_count=apps_count_by_credential.get(credential.id, 0),
                )
                for credential in credentials
            ],
            apps=[
                AccountAppSummary(
                    id=app.id,
                    name=app.name,
                    bundle_id=app.bundle_id,
                    asc_app_id=app.asc_app_id,
                    credential_id=app.credential_id,
                )
                for app in apps
            ],
        )
