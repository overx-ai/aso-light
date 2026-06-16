"""Async client for the RevenueCat v2 Developer REST API.

Only the endpoints needed for product/entitlement/offering/package
read+write are wrapped here. Schema and exact paths follow RevenueCat's
published reference at https://www.revenuecat.com/reference (verified
against the same surface area exposed by the MCP tools).

Mirrors the rate-limit / token-refresh / pagination patterns from
:class:`app.services.asc.client.ASCClient`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.services.revenuecat.errors import (
    RevenueCatAPIError,
    RevenueCatRateLimitError,
)

if TYPE_CHECKING:
    from app.models.revenuecat_credential import RevenueCatCredential

logger = logging.getLogger(__name__)

_MAX_RETRIES = 6
_BACKOFF_BASE = 1.0
_MIN_REQUEST_INTERVAL = 0.15


class RevenueCatClient:
    """Async wrapper around RevenueCat v2 Developer API."""

    BASE_URL = "https://api.revenuecat.com/v2"

    def __init__(self, secret_key: str, project_id: str):
        self.secret_key = secret_key
        self.project_id = project_id
        self._client: httpx.AsyncClient | None = None
        self._rate_lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._backoff_until: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.secret_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def _throttle(self) -> None:
        async with self._rate_lock:
            now = time.time()
            if now < self._backoff_until:
                await asyncio.sleep(self._backoff_until - now)
            elapsed = time.time() - self._last_request_at
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_at = time.time()

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict:
        client = await self._get_client()
        for attempt in range(_MAX_RETRIES):
            await self._throttle()
            response = await client.request(method, path, **kwargs)

            if response.status_code == 429:
                retry_after = float(
                    response.headers.get(
                        "Retry-After", _BACKOFF_BASE * (2 ** attempt),
                    )
                )
                self._backoff_until = time.time() + retry_after
                logger.warning(
                    "RevenueCat rate limited, backing off %.1fs (attempt %d/%d)",
                    retry_after, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text or ""
                raise RevenueCatAPIError(response.status_code, body)

            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

        try:
            body = response.json()  # type: ignore[possibly-undefined]
        except Exception:
            body = ""
        raise RevenueCatRateLimitError(body, retry_after=0)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        return await self._request("POST", path, json=json)

    async def _put(self, path: str, json: dict | None = None) -> dict:
        return await self._request("PUT", path, json=json)

    async def _delete(self, path: str) -> dict:
        return await self._request("DELETE", path)

    async def _list_all(
        self, path: str, params: dict | None = None,
    ) -> list[dict]:
        """Paginate a RevenueCat list endpoint via ``next_page`` cursor.

        RC list responses use ``{items: [...], next_page: "url-or-null"}``.
        """
        all_items: list[dict] = []
        current_params = dict(params) if params else {}
        current_params.setdefault("limit", 50)

        response = await self._get(path, params=current_params)
        all_items.extend(response.get("items", []))
        next_page = response.get("next_page")

        while next_page:
            client = await self._get_client()
            page = None
            # Cap 429 retries like ``_request`` so a persistently rate-
            # limited cursor page can't spin forever; surface a
            # RevenueCatRateLimitError once the budget is exhausted.
            for attempt in range(_MAX_RETRIES):
                await self._throttle()
                raw = await client.get(next_page)
                if raw.status_code == 429:
                    retry_after = float(
                        raw.headers.get(
                            "Retry-After", _BACKOFF_BASE * (2 ** attempt),
                        )
                    )
                    self._backoff_until = time.time() + retry_after
                    logger.warning(
                        "RevenueCat rate limited during pagination, backing "
                        "off %.1fs (attempt %d/%d)",
                        retry_after, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                if raw.status_code >= 400:
                    try:
                        body = raw.json()
                    except Exception:
                        body = raw.text or ""
                    raise RevenueCatAPIError(raw.status_code, body)
                page = raw.json()
                break
            else:
                try:
                    body = raw.json()
                except Exception:
                    body = raw.text or ""
                raise RevenueCatRateLimitError(body, retry_after=0)
            all_items.extend(page.get("items", []))
            next_page = page.get("next_page")

        return all_items

    # ------------------------------------------------------------------
    # Project / App
    # ------------------------------------------------------------------

    async def get_project(self) -> dict:
        return await self._get(f"/projects/{self.project_id}")

    async def list_apps(self) -> list[dict]:
        return await self._list_all(f"/projects/{self.project_id}/apps")

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def list_products(
        self,
        app_id: str | None = None,
        store_identifier: str | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if app_id:
            params["app_id"] = app_id
        if store_identifier:
            params["store_identifier"] = store_identifier
        return await self._list_all(
            f"/projects/{self.project_id}/products", params=params or None,
        )

    async def create_product(
        self,
        store_identifier: str,
        app_id: str,
        product_type: str = "subscription",
        display_name: str | None = None,
        subscription_period: str | None = None,
    ) -> dict:
        """``POST /projects/{p}/products`` — create a product mirror.

        ``product_type`` is one of ``subscription``, ``consumable``,
        ``non_consumable``, ``non_renewable``. ``subscription_period`` is
        only required for subscriptions (e.g. ``P1M``, ``P1Y``).
        """
        body: dict[str, Any] = {
            "store_identifier": store_identifier,
            "app_id": app_id,
            "type": product_type,
        }
        if display_name is not None:
            body["display_name"] = display_name
        if subscription_period is not None:
            body["subscription"] = {"duration": subscription_period}
        return await self._post(
            f"/projects/{self.project_id}/products", json=body,
        )

    async def archive_product(self, product_id: str) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/products/{product_id}/actions/archive",
        )

    # ------------------------------------------------------------------
    # Entitlements
    # ------------------------------------------------------------------

    async def list_entitlements(self) -> list[dict]:
        return await self._list_all(
            f"/projects/{self.project_id}/entitlements",
            params={"expand": "items.products"},
        )

    async def get_entitlement(self, entitlement_id: str) -> dict:
        return await self._get(
            f"/projects/{self.project_id}/entitlements/{entitlement_id}",
            params={"expand": "products"},
        )

    async def create_entitlement(
        self, lookup_key: str, display_name: str,
    ) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/entitlements",
            json={
                "lookup_key": lookup_key,
                "display_name": display_name,
            },
        )

    async def update_entitlement(
        self, entitlement_id: str, display_name: str,
    ) -> dict:
        return await self._put(
            f"/projects/{self.project_id}/entitlements/{entitlement_id}",
            json={"display_name": display_name},
        )

    async def archive_entitlement(self, entitlement_id: str) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/entitlements/"
            f"{entitlement_id}/actions/archive",
        )

    async def attach_products_to_entitlement(
        self, entitlement_id: str, product_ids: list[str],
    ) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/entitlements/"
            f"{entitlement_id}/actions/attach_products",
            json={"product_ids": product_ids},
        )

    async def detach_products_from_entitlement(
        self, entitlement_id: str, product_ids: list[str],
    ) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/entitlements/"
            f"{entitlement_id}/actions/detach_products",
            json={"product_ids": product_ids},
        )

    # ------------------------------------------------------------------
    # Offerings
    # ------------------------------------------------------------------

    async def list_offerings(self) -> list[dict]:
        return await self._list_all(
            f"/projects/{self.project_id}/offerings",
            params={"expand": "items.package"},
        )

    async def get_offering(self, offering_id: str) -> dict:
        return await self._get(
            f"/projects/{self.project_id}/offerings/{offering_id}",
        )

    async def create_offering(
        self,
        lookup_key: str,
        display_name: str,
        is_current: bool = False,
        metadata: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "lookup_key": lookup_key,
            "display_name": display_name,
            "is_current": is_current,
        }
        if metadata:
            body["metadata"] = metadata
        return await self._post(
            f"/projects/{self.project_id}/offerings", json=body,
        )

    async def update_offering(
        self,
        offering_id: str,
        display_name: str | None = None,
        is_current: bool | None = None,
        metadata: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if display_name is not None:
            body["display_name"] = display_name
        if is_current is not None:
            body["is_current"] = is_current
        if metadata is not None:
            body["metadata"] = metadata
        return await self._put(
            f"/projects/{self.project_id}/offerings/{offering_id}", json=body,
        )

    async def archive_offering(self, offering_id: str) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/offerings/"
            f"{offering_id}/actions/archive",
        )

    # ------------------------------------------------------------------
    # Packages
    # ------------------------------------------------------------------

    async def list_packages(self, offering_id: str) -> list[dict]:
        return await self._list_all(
            f"/projects/{self.project_id}/offerings/{offering_id}/packages",
            params={"expand": "items.products"},
        )

    async def create_package(
        self,
        offering_id: str,
        lookup_key: str,
        display_name: str,
        position: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "lookup_key": lookup_key,
            "display_name": display_name,
        }
        if position is not None:
            body["position"] = position
        return await self._post(
            f"/projects/{self.project_id}/offerings/{offering_id}/packages",
            json=body,
        )

    async def delete_package(
        self, offering_id: str, package_id: str,
    ) -> None:
        await self._delete(
            f"/projects/{self.project_id}/offerings/"
            f"{offering_id}/packages/{package_id}",
        )

    async def attach_products_to_package(
        self,
        offering_id: str,
        package_id: str,
        product_ids: list[str],
    ) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/offerings/"
            f"{offering_id}/packages/{package_id}/actions/attach_products",
            json={"product_ids": product_ids},
        )

    async def detach_products_from_package(
        self,
        offering_id: str,
        package_id: str,
        product_ids: list[str],
    ) -> dict:
        return await self._post(
            f"/projects/{self.project_id}/offerings/"
            f"{offering_id}/packages/{package_id}/actions/detach_products",
            json={"product_ids": product_ids},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> RevenueCatClient:
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        await self.close()

    @classmethod
    def from_credential(
        cls, credential: RevenueCatCredential,
    ) -> RevenueCatClient:
        from app.core.security import decrypt_value

        secret = decrypt_value(credential.secret_key_encrypted)
        return cls(secret_key=secret, project_id=credential.project_id)
