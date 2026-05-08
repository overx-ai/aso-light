"""Service for fetching and syncing apps from App Store Connect."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.app import App
from app.services.asc.errors import ASCAPIError, CredentialDecryptError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.credential import ASCCredential
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)


class ASCAppsService:
    """Service for fetching apps from the App Store Connect API."""

    def __init__(self, client: ASCClient):
        self.client = client

    async def list_apps(self) -> list[dict]:
        """Fetch all apps accessible by the credential from ASC API.

        Calls ``GET /v1/apps`` with pagination and returns the combined
        list of app data dictionaries.

        Each item has the shape::

            {
                "type": "apps",
                "id": "<asc_app_id>",
                "attributes": {
                    "name": "...",
                    "bundleId": "...",
                    "platform": "IOS" | "MAC_OS"
                }
            }
        """
        data = await self.client._get_all_pages(
            "/apps",
            params={
                "fields[apps]": "name,bundleId",
                "limit": 200,
            },
        )
        return data

    async def get_app(self, app_id: str) -> dict:
        """Fetch a single app by its ASC ID.

        Args:
            app_id: The App Store Connect numeric app identifier.

        Returns:
            App data dictionary (the ``data`` object from the response).
        """
        response = await self.client._get(
            f"/apps/{app_id}",
            params={
                "fields[apps]": "name,bundleId",
            },
        )
        return response.get("data", {})


async def sync_apps_for_credentials(
    session: AsyncSession,
    credentials: list[ASCCredential],
) -> list[App]:
    """Pull apps from ASC for each credential and upsert them into the DB.

    Shared between the REST ``POST /apps/sync`` endpoint and the
    ``apps.sync`` MCP tool. Continues past credentials that 4xx/5xx
    (each failure is logged) so partial sync still succeeds.

    The session is flushed but not committed — the caller owns the
    transaction boundary.
    """
    # Local import to avoid a top-level cycle with ``ASCClient``.
    from app.services.asc.client import ASCClient

    synced_apps: list[App] = []

    for cred in credentials:
        try:
            async with ASCClient.from_credential(cred) as client:
                apps_data = await ASCAppsService(client).list_apps()
        except CredentialDecryptError as exc:
            # Corrupt/legacy .p8 (wrong FERNET_KEY or non-PEM bytes). Skip this
            # credential so other healthy ones still sync; the user can fix it
            # by re-uploading via the credentials UI.
            logger.warning(
                "Skipping credential id=%s — cannot decrypt private key: %s",
                cred.id, exc,
            )
            continue
        except ASCAPIError as exc:
            logger.warning(
                "ASC API error syncing credential id=%s: %s", cred.id, exc.message,
            )
            continue

        for app_data in apps_data:
            asc_app_id = app_data["id"]
            attrs = app_data.get("attributes", {})
            platform = "ios" if attrs.get("platform", "IOS") == "IOS" else "macos"

            existing = await session.execute(
                select(App).where(
                    App.credential_id == cred.id,
                    App.asc_app_id == asc_app_id,
                )
            )
            app_record = existing.scalar_one_or_none()

            if app_record:
                app_record.name = attrs.get("name", app_record.name)
                app_record.bundle_id = attrs.get("bundleId", app_record.bundle_id)
                app_record.platform = platform
            else:
                app_record = App(
                    credential_id=cred.id,
                    asc_app_id=asc_app_id,
                    bundle_id=attrs.get("bundleId", ""),
                    name=attrs.get("name", "Unknown"),
                    platform=platform,
                )
                session.add(app_record)

            await session.flush()
            synced_apps.append(app_record)

    return synced_apps
