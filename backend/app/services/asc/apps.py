"""Service for fetching and syncing apps from App Store Connect."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient


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
