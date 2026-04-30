"""Service for managing subscription and IAP prices via ASC API."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient


class ASCPricingService:
    """Service for managing subscription and IAP prices via ASC API.

    Wraps the JSON:API calls for subscription groups, subscriptions,
    price points, IAPs, and price mutations.
    """

    def __init__(self, client: ASCClient):
        self.client = client

    # ------------------------------------------------------------------
    # Subscription Groups
    # ------------------------------------------------------------------

    async def list_subscription_groups(self, app_id: str) -> list[dict]:
        """Fetch subscription groups for an app.

        ``GET /v1/apps/{app_id}/subscriptionGroups``

        Returns:
            List of JSON:API resource objects with id and attributes.referenceName.
        """
        return await self.client._get_all_pages(
            f"/apps/{app_id}/subscriptionGroups",
            params={
                "fields[subscriptionGroups]": "referenceName",
                "limit": 200,
            },
        )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def list_subscriptions(self, group_id: str) -> list[dict]:
        """Fetch subscriptions within a group.

        ``GET /v1/subscriptionGroups/{group_id}/subscriptions``

        Returns:
            List of JSON:API resource objects with productId, name,
            state, subscriptionPeriod.
        """
        return await self.client._get_all_pages(
            f"/subscriptionGroups/{group_id}/subscriptions",
            params={
                "fields[subscriptions]": "productId,name,state,subscriptionPeriod",
                "limit": 200,
            },
        )

    # ------------------------------------------------------------------
    # Subscription Prices
    # ------------------------------------------------------------------

    async def get_subscription_prices(self, subscription_id: str) -> list[dict]:
        """Fetch current prices for a subscription.

        ``GET /v1/subscriptions/{subscription_id}/prices``

        Includes territory data so we can extract territory codes and
        price point details in a single call.

        Returns:
            List of JSON:API resource objects with included territory
            and subscriptionPricePoint data.
        """
        response = await self.client._get(
            f"/subscriptions/{subscription_id}/prices",
            params={
                "include": "subscriptionPricePoint,territory",
                "fields[subscriptionPricePoints]": "customerPrice,proceeds,proceedsYear2",
                "fields[territories]": "currency",
                "limit": 200,
            },
        )
        # Build lookup maps from included resources
        included = response.get("included", [])
        price_points_map: dict[str, dict] = {}
        territories_map: dict[str, dict] = {}
        for item in included:
            if item["type"] == "subscriptionPricePoints":
                price_points_map[item["id"]] = item
            elif item["type"] == "territories":
                territories_map[item["id"]] = item

        # Enrich each price entry with resolved territory and price point data
        result: list[dict] = []
        for price in response.get("data", []):
            relationships = price.get("relationships", {})

            pp_ref = (
                relationships.get("subscriptionPricePoint", {})
                .get("data", {})
            )
            territory_ref = (
                relationships.get("territory", {})
                .get("data", {})
            )

            pp_id = pp_ref.get("id") if pp_ref else None
            territory_id = territory_ref.get("id") if territory_ref else None

            pp_data = price_points_map.get(pp_id, {}) if pp_id else {}
            territory_data = territories_map.get(territory_id, {}) if territory_id else {}

            result.append({
                "id": price["id"],
                "price_point_id": pp_id,
                "territory_code": territory_id,
                "customer_price": float(
                    pp_data.get("attributes", {}).get("customerPrice", 0)
                ),
                "proceeds": float(
                    pp_data.get("attributes", {}).get("proceeds", 0)
                ),
                "currency_code": territory_data.get("attributes", {}).get(
                    "currency", ""
                ),
            })

        return result

    # ------------------------------------------------------------------
    # Price Points
    # ------------------------------------------------------------------

    async def get_price_points(
        self,
        subscription_id: str,
        territory_code: str | None = None,
    ) -> list[dict]:
        """Fetch available price points for a subscription.

        ``GET /v1/subscriptions/{subscription_id}/pricePoints``

        Args:
            subscription_id: The ASC subscription identifier.
            territory_code: Optional ISO territory code to filter by.

        Returns:
            List of enriched price point dicts with territory info.
        """
        params: dict[str, str | int] = {
            "include": "territory",
            # `territory` must be in the field spec or Apple omits the
            # relationship from the response (and we lose the currency).
            "fields[subscriptionPricePoints]": "customerPrice,proceeds,proceedsYear2,territory",
            "fields[territories]": "currency",
            "limit": 200,
        }
        if territory_code:
            params["filter[territory]"] = territory_code

        response = await self.client._get(
            f"/subscriptions/{subscription_id}/pricePoints",
            params=params,
        )

        # Build territory lookup from included
        included = response.get("included", [])
        territories_map: dict[str, dict] = {}
        for item in included:
            if item["type"] == "territories":
                territories_map[item["id"]] = item

        # Paginate manually since we need included data
        all_data = list(response.get("data", []))
        next_url = response.get("links", {}).get("next")
        while next_url:
            client = await self.client._get_client()
            raw = await client.get(next_url)
            if raw.status_code >= 400:
                break
            page = raw.json()
            all_data.extend(page.get("data", []))
            for item in page.get("included", []):
                if item["type"] == "territories":
                    territories_map[item["id"]] = item
            next_url = page.get("links", {}).get("next")

        result: list[dict] = []
        for pp in all_data:
            attrs = pp.get("attributes", {})
            territory_ref = (
                pp.get("relationships", {})
                .get("territory", {})
                .get("data", {})
            )
            territory_id = territory_ref.get("id") if territory_ref else None
            territory_data = territories_map.get(territory_id, {}) if territory_id else {}

            result.append({
                "price_point_id": pp["id"],
                "territory_code": territory_id,
                "customer_price": float(attrs.get("customerPrice", 0)),
                "proceeds": float(attrs.get("proceeds", 0)),
                "currency_code": territory_data.get("attributes", {}).get(
                    "currency", ""
                ),
            })

        return result

    # ------------------------------------------------------------------
    # Price Point Equalizations
    # ------------------------------------------------------------------

    async def get_price_point_equalizations(
        self, price_point_id: str
    ) -> list[dict]:
        """Get equalized price points for other territories.

        ``GET /v1/subscriptionPricePoints/{id}/equalizations``

        Given a reference price point, returns the Apple-equalized
        prices for all other territories.

        Returns:
            List of enriched equalization dicts with territory info.
        """
        response = await self.client._get(
            f"/subscriptionPricePoints/{price_point_id}/equalizations",
            params={
                "include": "territory",
                "fields[subscriptionPricePoints]": "customerPrice,proceeds,proceedsYear2,territory",
                "fields[territories]": "currency",
                "limit": 200,
            },
        )

        included = response.get("included", [])
        territories_map: dict[str, dict] = {}
        for item in included:
            if item["type"] == "territories":
                territories_map[item["id"]] = item

        all_data = list(response.get("data", []))
        next_url = response.get("links", {}).get("next")
        while next_url:
            client = await self.client._get_client()
            raw = await client.get(next_url)
            if raw.status_code >= 400:
                break
            page = raw.json()
            all_data.extend(page.get("data", []))
            for item in page.get("included", []):
                if item["type"] == "territories":
                    territories_map[item["id"]] = item
            next_url = page.get("links", {}).get("next")

        result: list[dict] = []
        for pp in all_data:
            attrs = pp.get("attributes", {})
            territory_ref = (
                pp.get("relationships", {})
                .get("territory", {})
                .get("data", {})
            )
            territory_id = territory_ref.get("id") if territory_ref else None
            territory_data = territories_map.get(territory_id, {}) if territory_id else {}

            result.append({
                "price_point_id": pp["id"],
                "territory_code": territory_id,
                "customer_price": float(attrs.get("customerPrice", 0)),
                "proceeds": float(attrs.get("proceeds", 0)),
                "currency_code": territory_data.get("attributes", {}).get(
                    "currency", ""
                ),
            })

        return result

    # ------------------------------------------------------------------
    # Create / Update Subscription Price
    # ------------------------------------------------------------------

    async def create_subscription_price(
        self,
        subscription_id: str,
        price_point_id: str,
        preserve_current_price: bool = False,
    ) -> dict:
        """Set a new price for a subscription territory.

        ``POST /v1/subscriptionPrices``

        Args:
            subscription_id: ASC subscription ID.
            price_point_id: ASC price point ID to set.
            preserve_current_price: Whether existing subscribers keep
                their current price.

        Returns:
            The created subscriptionPrice resource dict.
        """
        body = {
            "data": {
                "type": "subscriptionPrices",
                "attributes": {
                    "preserveCurrentPrice": preserve_current_price,
                },
                "relationships": {
                    "subscription": {
                        "data": {
                            "type": "subscriptions",
                            "id": subscription_id,
                        }
                    },
                    "subscriptionPricePoint": {
                        "data": {
                            "type": "subscriptionPricePoints",
                            "id": price_point_id,
                        }
                    },
                },
            }
        }
        return await self.client._post("/subscriptionPrices", json=body)

    # ------------------------------------------------------------------
    # In-App Purchases
    # ------------------------------------------------------------------

    async def list_iaps(self, app_id: str) -> list[dict]:
        """Fetch in-app purchases for an app.

        ``GET /v1/apps/{app_id}/inAppPurchasesV2``

        Returns:
            List of JSON:API resource objects with name, productId,
            inAppPurchaseType, state.
        """
        return await self.client._get_all_pages(
            f"/apps/{app_id}/inAppPurchasesV2",
            params={
                "fields[inAppPurchases]": "name,productId,inAppPurchaseType,state",
                "limit": 200,
            },
        )

    # ------------------------------------------------------------------
    # Subscription Localizations
    # ------------------------------------------------------------------

    async def list_subscription_localizations(
        self, subscription_id: str
    ) -> list[dict]:
        """Fetch localizations for a subscription.

        ``GET /v1/subscriptions/{subscription_id}/subscriptionLocalizations``

        Returns:
            List of JSON:API resource objects with locale, name, description.
        """
        return await self.client._get_all_pages(
            f"/subscriptions/{subscription_id}/subscriptionLocalizations",
            params={
                "fields[subscriptionLocalizations]": "locale,name,description",
                "limit": 200,
            },
        )

    async def create_subscription_localization(
        self,
        subscription_id: str,
        locale: str,
        name: str,
        description: str,
    ) -> dict:
        """Create a localization for a subscription.

        ``POST /v1/subscriptionLocalizations``

        Returns:
            The created subscriptionLocalization resource dict.
        """
        body = {
            "data": {
                "type": "subscriptionLocalizations",
                "attributes": {
                    "locale": locale,
                    "name": name,
                    "description": description,
                },
                "relationships": {
                    "subscription": {
                        "data": {
                            "type": "subscriptions",
                            "id": subscription_id,
                        }
                    },
                },
            }
        }
        return await self.client._post(
            "/subscriptionLocalizations", json=body
        )

    async def update_subscription_localization(
        self,
        localization_id: str,
        name: str,
        description: str,
    ) -> dict:
        """Update a subscription localization (locale is immutable).

        ``PATCH /v1/subscriptionLocalizations/{localization_id}``

        Returns:
            The updated subscriptionLocalization resource dict.
        """
        body = {
            "data": {
                "type": "subscriptionLocalizations",
                "id": localization_id,
                "attributes": {
                    "name": name,
                    "description": description,
                },
            }
        }
        return await self.client._patch(
            f"/subscriptionLocalizations/{localization_id}", json=body
        )

    # ------------------------------------------------------------------
    # IAP Localizations
    # ------------------------------------------------------------------

    async def list_iap_localizations(self, iap_id: str) -> list[dict]:
        """Fetch localizations for an in-app purchase.

        Uses the v2 API: ``GET /v2/inAppPurchases/{id}?include=inAppPurchaseLocalizations``
        because IAP localizations only exist on the v2 resource.

        Returns:
            List of JSON:API resource objects with locale, name, description.
        """
        # Must use /v2 — IAP localizations don't exist on the v1 resource
        http = await self.client._get_client()
        base = self.client.BASE_URL.replace("/v1", "/v2")
        url = (
            f"{base}/inAppPurchases/{iap_id}"
            f"?include=inAppPurchaseLocalizations"
            f"&fields[inAppPurchaseLocalizations]=locale,name,description"
            f"&fields[inAppPurchases]=name"
        )
        raw = await http.get(url)
        if raw.status_code >= 400:
            from app.services.asc.errors import ASCAPIError
            body = raw.json() if raw.content else {"errors": []}
            raise ASCAPIError(raw.status_code, body)

        data = raw.json()
        return [
            item
            for item in data.get("included", [])
            if item.get("type") == "inAppPurchaseLocalizations"
        ]

    async def create_iap_localization(
        self,
        iap_id: str,
        locale: str,
        name: str,
        description: str,
    ) -> dict:
        """Create a localization for an in-app purchase.

        ``POST /v1/inAppPurchaseLocalizations``

        Returns:
            The created inAppPurchaseLocalization resource dict.
        """
        body = {
            "data": {
                "type": "inAppPurchaseLocalizations",
                "attributes": {
                    "locale": locale,
                    "name": name,
                    "description": description,
                },
                "relationships": {
                    "inAppPurchaseV2": {
                        "data": {
                            "type": "inAppPurchases",
                            "id": iap_id,
                        }
                    },
                },
            }
        }
        return await self.client._post(
            "/inAppPurchaseLocalizations", json=body
        )

    async def update_iap_localization(
        self,
        localization_id: str,
        name: str,
        description: str,
    ) -> dict:
        """Update an IAP localization (locale is immutable).

        ``PATCH /v1/inAppPurchaseLocalizations/{localization_id}``

        Returns:
            The updated inAppPurchaseLocalization resource dict.
        """
        body = {
            "data": {
                "type": "inAppPurchaseLocalizations",
                "id": localization_id,
                "attributes": {
                    "name": name,
                    "description": description,
                },
            }
        }
        return await self.client._patch(
            f"/inAppPurchaseLocalizations/{localization_id}", json=body
        )

    # ------------------------------------------------------------------
    # Review Screenshots
    # ------------------------------------------------------------------

    async def get_subscription_review_screenshot(
        self, subscription_id: str
    ) -> dict | None:
        """Get the review screenshot for a subscription (or None)."""
        response = await self.client._get(
            f"/subscriptions/{subscription_id}/appStoreReviewScreenshot",
        )
        return response.get("data")

    async def delete_subscription_review_screenshot(
        self, screenshot_id: str
    ) -> None:
        """Delete a subscription review screenshot."""
        await self.client._delete(
            f"/subscriptionAppStoreReviewScreenshots/{screenshot_id}"
        )

    async def delete_iap_review_screenshot(
        self, screenshot_id: str
    ) -> None:
        """Delete an IAP review screenshot."""
        await self.client._delete(
            f"/inAppPurchaseAppStoreReviewScreenshots/{screenshot_id}"
        )

    async def upload_subscription_review_screenshot(
        self,
        subscription_id: str,
        file_name: str,
        file_bytes: bytes,
    ) -> dict:
        """Upload a review screenshot for a subscription (3-step flow).

        If an existing screenshot is stuck (AWAITING_UPLOAD), deletes it first.
        """
        import hashlib

        # Delete existing screenshot if present (stuck or completed)
        existing = await self.get_subscription_review_screenshot(subscription_id)
        if existing:
            await self.delete_subscription_review_screenshot(existing["id"])

        checksum = hashlib.md5(file_bytes).hexdigest()

        # Step 1: Reserve
        reserve_body = {
            "data": {
                "type": "subscriptionAppStoreReviewScreenshots",
                "attributes": {
                    "fileName": file_name,
                    "fileSize": len(file_bytes),
                },
                "relationships": {
                    "subscription": {
                        "data": {
                            "type": "subscriptions",
                            "id": subscription_id,
                        }
                    },
                },
            }
        }
        reservation = await self.client._post(
            "/subscriptionAppStoreReviewScreenshots", json=reserve_body
        )

        screenshot_id = reservation["data"]["id"]
        operations = reservation["data"]["attributes"].get(
            "uploadOperations", []
        )

        # Step 2: Upload binary (use Apple's requested content type)
        for op in operations:
            content_type = "application/octet-stream"
            for hdr in op.get("requestHeaders", []):
                if hdr.get("name", "").lower() == "content-type":
                    content_type = hdr["value"]
            offset = op.get("offset", 0)
            await self.client._put_binary(
                op["url"],
                file_bytes[offset:offset + op["length"]],
                content_type=content_type,
            )

        # Step 3: Commit
        commit_body = {
            "data": {
                "type": "subscriptionAppStoreReviewScreenshots",
                "id": screenshot_id,
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksum": checksum,
                },
            }
        }
        return await self.client._patch(
            f"/subscriptionAppStoreReviewScreenshots/{screenshot_id}",
            json=commit_body,
        )

    async def get_iap_review_screenshot(
        self, iap_id: str
    ) -> dict | None:
        """Get the review screenshot for an IAP (or None). Uses v2 API."""
        http = await self.client._get_client()
        base_v2 = self.client.BASE_URL.replace("/v1", "/v2")
        url = (
            f"{base_v2}/inAppPurchases/{iap_id}"
            f"?include=appStoreReviewScreenshot"
            f"&fields[inAppPurchaseAppStoreReviewScreenshots]="
            f"fileName,fileSize,sourceFileChecksum,imageAsset,assetToken"
            f"&fields[inAppPurchases]=name"
        )
        raw = await http.get(url)
        if raw.status_code >= 400:
            return None
        data = raw.json()
        for item in data.get("included", []):
            if item.get("type") == "inAppPurchaseAppStoreReviewScreenshots":
                return item
        return None

    async def upload_iap_review_screenshot(
        self,
        iap_id: str,
        file_name: str,
        file_bytes: bytes,
    ) -> dict:
        """Upload a review screenshot for an IAP (3-step flow).

        If an existing screenshot is present, deletes it first.
        """
        import hashlib

        # Delete existing screenshot if present
        existing = await self.get_iap_review_screenshot(iap_id)
        if existing:
            await self.delete_iap_review_screenshot(existing["id"])

        checksum = hashlib.md5(file_bytes).hexdigest()

        # Step 1: Reserve
        reserve_body = {
            "data": {
                "type": "inAppPurchaseAppStoreReviewScreenshots",
                "attributes": {
                    "fileName": file_name,
                    "fileSize": len(file_bytes),
                },
                "relationships": {
                    "inAppPurchaseV2": {
                        "data": {
                            "type": "inAppPurchases",
                            "id": iap_id,
                        }
                    },
                },
            }
        }
        reservation = await self.client._post(
            "/inAppPurchaseAppStoreReviewScreenshots", json=reserve_body
        )

        screenshot_id = reservation["data"]["id"]
        operations = reservation["data"]["attributes"].get(
            "uploadOperations", []
        )

        # Step 2: Upload binary (use Apple's requested content type)
        for op in operations:
            content_type = "application/octet-stream"
            for hdr in op.get("requestHeaders", []):
                if hdr.get("name", "").lower() == "content-type":
                    content_type = hdr["value"]
            offset = op.get("offset", 0)
            await self.client._put_binary(
                op["url"],
                file_bytes[offset:offset + op["length"]],
                content_type=content_type,
            )

        # Step 3: Commit
        commit_body = {
            "data": {
                "type": "inAppPurchaseAppStoreReviewScreenshots",
                "id": screenshot_id,
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksum": checksum,
                },
            }
        }
        return await self.client._patch(
            f"/inAppPurchaseAppStoreReviewScreenshots/{screenshot_id}",
            json=commit_body,
        )

    # ------------------------------------------------------------------
    # IAP Price Points (v2 API)
    # ------------------------------------------------------------------

    async def get_iap_price_points(
        self,
        iap_id: str,
        territory_code: str | None = None,
    ) -> list[dict]:
        """Fetch available price points for an IAP via v2 API.

        ``GET /v2/inAppPurchases/{id}/pricePoints``

        The v1 API does not support IAP price points; must use v2.

        Args:
            iap_id: The ASC in-app purchase identifier.
            territory_code: Optional alpha-3 territory code to filter by.

        Returns:
            List of enriched price point dicts with territory info.
        """
        http = await self.client._get_client()
        base_v2 = self.client.BASE_URL.replace("/v1", "/v2")

        params_parts = [
            "include=territory",
            "fields[inAppPurchasePricePoints]=customerPrice,proceeds,territory",
            "fields[territories]=currency",
            "limit=200",
        ]
        if territory_code:
            params_parts.append(f"filter[territory]={territory_code}")

        url = f"{base_v2}/inAppPurchases/{iap_id}/pricePoints?{'&'.join(params_parts)}"

        raw = await http.get(url)
        if raw.status_code >= 400:
            from app.services.asc.errors import ASCAPIError
            body = raw.json() if raw.content else {"errors": []}
            raise ASCAPIError(raw.status_code, body)

        response = raw.json()

        # Build territory lookup from included
        included = response.get("included", [])
        territories_map: dict[str, dict] = {}
        for item in included:
            if item["type"] == "territories":
                territories_map[item["id"]] = item

        # Paginate manually since we need included data
        all_data = list(response.get("data", []))
        next_url = response.get("links", {}).get("next")
        while next_url:
            raw = await http.get(next_url)
            if raw.status_code >= 400:
                break
            page = raw.json()
            all_data.extend(page.get("data", []))
            for item in page.get("included", []):
                if item["type"] == "territories":
                    territories_map[item["id"]] = item
            next_url = page.get("links", {}).get("next")

        result: list[dict] = []
        for pp in all_data:
            attrs = pp.get("attributes", {})
            territory_ref = (
                pp.get("relationships", {})
                .get("territory", {})
                .get("data", {})
            )
            territory_id = territory_ref.get("id") if territory_ref else None
            territory_data = territories_map.get(territory_id, {}) if territory_id else {}

            result.append({
                "price_point_id": pp["id"],
                "territory_code": territory_id,
                "customer_price": float(attrs.get("customerPrice", 0)),
                "proceeds": float(attrs.get("proceeds", 0)),
                "currency_code": territory_data.get("attributes", {}).get(
                    "currency", ""
                ),
            })

        return result

    # ------------------------------------------------------------------
    # Set IAP Prices (via inAppPurchasePriceSchedules)
    # ------------------------------------------------------------------

    async def set_iap_price(
        self,
        iap_id: str,
        price_entries: list[dict],
        base_territory_alpha3: str = "USA",
    ) -> dict:
        """Set manual prices on an IAP via price schedule.

        ``POST /v1/inAppPurchasePriceSchedules``

        Creates a new price schedule that replaces all manual prices.
        All territories must be submitted at once.

        Apple's JSON:API extension requires:
        * ``baseTerritory`` — the alpha-3 territory whose price acts as
          the master fallback for any territory the schedule omits.
        * ``manualPrices`` inline-created entries with **local-id**
          placeholders of the form ``${...}`` (curly braces, not bare).

        Args:
            iap_id: ASC in-app purchase ID.
            price_entries: List of dicts with keys:
                - territory_code: alpha-2 territory code (used in the
                  local id to keep entries unique within the request)
                - price_point_id: ASC price point ID to set
            base_territory_alpha3: alpha-3 (e.g. ``"USA"``) of the
                fallback territory.

        Returns:
            The created inAppPurchasePriceSchedule resource dict.
        """
        included: list[dict] = []
        manual_prices_data: list[dict] = []

        for entry in price_entries:
            # Curly-brace local-id format Apple requires for inline creation.
            local_id = "${" + entry["territory_code"] + "}"
            manual_prices_data.append({
                "type": "inAppPurchasePrices",
                "id": local_id,
            })
            included.append({
                "type": "inAppPurchasePrices",
                "id": local_id,
                "relationships": {
                    "inAppPurchasePricePoint": {
                        "data": {
                            "type": "inAppPurchasePricePoints",
                            "id": entry["price_point_id"],
                        }
                    },
                },
            })

        body = {
            "data": {
                "type": "inAppPurchasePriceSchedules",
                "relationships": {
                    "inAppPurchase": {
                        "data": {
                            "type": "inAppPurchases",
                            "id": iap_id,
                        }
                    },
                    "baseTerritory": {
                        "data": {
                            "type": "territories",
                            "id": base_territory_alpha3,
                        }
                    },
                    "manualPrices": {
                        "data": manual_prices_data,
                    },
                },
            },
            "included": included,
        }

        return await self.client._post(
            "/inAppPurchasePriceSchedules", json=body
        )

    # ------------------------------------------------------------------
    # IAP Price Schedule
    # ------------------------------------------------------------------

    async def get_iap_price_schedule(self, iap_id: str) -> list[dict]:
        """Fetch current prices for an IAP via the v2 price schedule.

        Apple's ``?include=manualPrices`` parameter silently caps the
        included resources at ~10 entries — for IAPs with more manual
        prices we have to follow the ``relationships.manualPrices.related``
        link and paginate it explicitly. We then fetch the matching
        price point for each manual price to resolve customerPrice,
        proceeds, and currency.

        Returns:
            List of enriched price dicts with territory_code, customer_price,
            proceeds, currency_code, and price_point_id.
        """
        import base64
        import json as _json

        http = await self.client._get_client()
        base_v2 = self.client.BASE_URL.replace("/v1", "/v2")

        # 1. Fetch the parent schedule to get the manualPrices related link.
        await self.client._throttle()
        raw = await http.get(
            f"{base_v2}/inAppPurchases/{iap_id}/iapPriceSchedule"
        )
        if raw.status_code >= 400:
            body = raw.json() if raw.content else {"errors": []}
            raise ASCAPIError(raw.status_code, body)
        related = (
            raw.json()
            .get("data", {})
            .get("relationships", {})
            .get("manualPrices", {})
            .get("links", {})
            .get("related")
        )

        # 2. Paginate the related endpoint to collect every manual price.
        manual_items: list[dict] = []
        next_url = f"{related}?limit=200" if related else None
        while next_url:
            await self.client._throttle()
            page_raw = await http.get(next_url)
            if page_raw.status_code >= 400:
                body = page_raw.json() if page_raw.content else {"errors": []}
                raise ASCAPIError(page_raw.status_code, body)
            page = page_raw.json()
            manual_items.extend(page.get("data", []))
            next_url = page.get("links", {}).get("next")

        # 3. Decode base64 ids → {t: territory_alpha3, p: price_point_num}
        prices_info: list[dict] = []
        for item in manual_items:
            if item.get("type") != "inAppPurchasePrices":
                continue
            pid = item["id"]
            padded = pid + "=" * (4 - len(pid) % 4)
            try:
                info = _json.loads(base64.b64decode(padded))
            except Exception:
                continue
            prices_info.append({
                "territory_alpha3": info.get("t", ""),
                "price_point_num": info.get("p", ""),
                "price_id": pid,
            })

        if not prices_info:
            return []

        # For each price, construct the price_point_id using the same
        # base64 encoding and fetch the specific territory's price points
        # to resolve customer_price and proceeds.
        result: list[dict] = []
        for pi in prices_info:
            terr_a3 = pi["territory_alpha3"]
            pp_num = pi["price_point_num"]

            # Build the canonical price_point_id
            pp_payload = _json.dumps(
                {"s": iap_id, "t": terr_a3, "p": pp_num},
                separators=(",", ":"),
            )
            pp_id = base64.b64encode(pp_payload.encode()).decode().rstrip("=")

            # Fetch all price points for this territory via v2
            # and find the one matching our pp_num
            await self.client._throttle()
            pp_url = (
                f"{base_v2}/inAppPurchases/{iap_id}/pricePoints"
                f"?filter[territory]={terr_a3}"
                f"&include=territory"
                f"&fields[inAppPurchasePricePoints]=customerPrice,proceeds,territory"
                f"&fields[territories]=currency"
                f"&limit=200"
            )

            # Paginate until we find our price point
            customer_price = 0.0
            proceeds = 0.0
            currency_code = ""
            found = False

            while pp_url and not found:
                pp_raw = await http.get(pp_url)
                if pp_raw.status_code >= 400:
                    break
                pp_data = pp_raw.json()

                # Extract currency from included territories
                if not currency_code:
                    for inc in pp_data.get("included", []):
                        if inc.get("type") == "territories":
                            currency_code = inc.get("attributes", {}).get(
                                "currency", ""
                            )
                            break

                for pp in pp_data.get("data", []):
                    if pp["id"] == pp_id:
                        attrs = pp.get("attributes", {})
                        customer_price = float(
                            attrs.get("customerPrice", 0)
                        )
                        proceeds = float(attrs.get("proceeds", 0))
                        found = True
                        break

                pp_url = pp_data.get("links", {}).get("next")
                if pp_url:
                    await self.client._throttle()

            result.append({
                "territory_code": terr_a3,
                "customer_price": customer_price,
                "proceeds": proceeds,
                "currency_code": currency_code,
                "price_point_id": pp_id,
            })

        return result
