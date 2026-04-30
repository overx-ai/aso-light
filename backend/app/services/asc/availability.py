"""App Store Connect — App Availability service.

Apple's ``appAvailabilities`` resource is read-only after creation —
the API only allows ``CREATE`` (initial setup) and ``GET_INSTANCE``.
Updates are made **per territory** via
``PATCH /v1/territoryAvailabilities/{id}``, where ``id`` is the
base64-encoded ``{"s": <asc_app_id>, "t": <alpha3>}`` payload Apple
uses for nested-id resources (same encoding as price points).

This service translates alpha-2 (our DB) ↔ alpha-3 (Apple) at the
boundary, and exposes a single ``set_app_availability`` that diffs
the requested state against the current snapshot and PATCHes only
the territories that actually need to change.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING

from app.data.territories import ALPHA2_TO_ALPHA3
from app.services.asc.errors import ASCAPIError

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)

ALPHA3_TO_ALPHA2: dict[str, str] = {v: k for k, v in ALPHA2_TO_ALPHA3.items()}


def _encode_territory_availability_id(asc_app_id: str, alpha3: str) -> str:
    payload = json.dumps({"s": asc_app_id, "t": alpha3}, separators=(",", ":"))
    return base64.b64encode(payload.encode()).decode().rstrip("=")


def _decode_territory_availability_id(encoded: str) -> tuple[str, str] | None:
    """Return ``(asc_app_id, alpha3)`` or ``None`` on failure."""
    padded = encoded + "=" * (4 - len(encoded) % 4)
    try:
        payload = json.loads(base64.b64decode(padded))
        return payload.get("s", ""), payload.get("t", "")
    except Exception:
        return None


class ASCAvailabilityService:
    def __init__(self, client: "ASCClient") -> None:
        self.client = client

    async def get_app_availability(self, asc_app_id: str) -> dict:
        """Fetch the current availability snapshot.

        Returns ``{"available_in_new_territories": bool,
                    "territories": [{"territory_code": "US",
                                      "available": bool,
                                      "preorder_enabled": bool}, ...]}``
        """
        http = await self.client._get_client()

        # 1. Parent: gives availableInNewTerritories + the snapshot id.
        await self.client._throttle()
        parent_url = (
            f"{self.client.BASE_URL}/apps/{asc_app_id}/appAvailabilityV2"
        )
        raw = await http.get(parent_url)
        if raw.status_code >= 400:
            body = raw.json() if raw.content else {"errors": []}
            raise ASCAPIError(raw.status_code, body)
        parent = raw.json().get("data", {})
        availability_id = parent.get("id", "")
        attrs = parent.get("attributes", {}) or {}
        available_in_new = bool(attrs.get("availableInNewTerritories", True))

        # 2. Paginate the related territoryAvailabilities.
        base_v2 = self.client.BASE_URL.replace("/v1", "/v2")
        url: str | None = (
            f"{base_v2}/appAvailabilities/{availability_id}"
            "/territoryAvailabilities"
            "?fields[territoryAvailabilities]=available,preOrderEnabled,territory"
            "&limit=200"
        )
        territories: list[dict] = []
        while url:
            await self.client._throttle()
            page_raw = await http.get(url)
            if page_raw.status_code >= 400:
                err = page_raw.json() if page_raw.content else {"errors": []}
                raise ASCAPIError(page_raw.status_code, err)
            page = page_raw.json()
            for item in page.get("data", []):
                if item.get("type") != "territoryAvailabilities":
                    continue
                decoded = _decode_territory_availability_id(item.get("id", ""))
                if decoded is None:
                    continue
                _, alpha3 = decoded
                alpha2 = ALPHA3_TO_ALPHA2.get(alpha3)
                if not alpha2:
                    logger.debug("Unmapped alpha-3 in availability: %s", alpha3)
                    continue
                t_attrs = item.get("attributes", {}) or {}
                territories.append({
                    "territory_code": alpha2,
                    "available": bool(t_attrs.get("available", False)),
                    "preorder_enabled": bool(t_attrs.get("preOrderEnabled", False)),
                })
            url = page.get("links", {}).get("next")

        return {
            "available_in_new_territories": available_in_new,
            "territories": territories,
        }

    async def set_app_availability(
        self,
        asc_app_id: str,
        available_alpha2_codes: list[str],
        available_in_new_territories: bool,  # noqa: ARG002 — see docstring
    ) -> int:
        """Bring the app's per-territory availability to the desired state.

        Diffs the requested set against Apple's current snapshot and
        PATCHes only the territories that actually need to flip.
        Returns the number of territories patched.

        The ``availableInNewTerritories`` flag lives on the parent
        ``appAvailabilities`` resource which Apple marks read-only after
        creation, so this argument is currently accepted but not
        propagated. Surface it as a v1 limitation in the UI.
        """
        if not available_alpha2_codes:
            raise ValueError(
                "available_alpha2_codes cannot be empty — refusing to "
                "make the app globally unavailable"
            )

        unknown = [
            c for c in available_alpha2_codes if c not in ALPHA2_TO_ALPHA3
        ]
        if unknown:
            raise ValueError(
                f"Unknown alpha-2 territory codes: {sorted(unknown)}"
            )

        desired_available_alpha2 = set(available_alpha2_codes)

        # 1. Read current state so we only PATCH territories that need to
        #    flip — saves API calls and avoids touching unrelated rows.
        current = await self.get_app_availability(asc_app_id)
        current_state: dict[str, bool] = {
            t["territory_code"]: t["available"] for t in current["territories"]
        }

        # 2. Compute the diff. Skip territories Apple doesn't track for
        #    this app (those that didn't appear in the GET response) — we
        #    can't toggle availability on a territory that has no row.
        to_patch: list[tuple[str, bool]] = []  # (alpha3, target_available)
        for alpha2, _ in current_state.items():
            target = alpha2 in desired_available_alpha2
            if current_state[alpha2] != target:
                to_patch.append((ALPHA2_TO_ALPHA3[alpha2], target))

        if not to_patch:
            logger.info(
                "App availability for %s already matches desired state",
                asc_app_id,
            )
            return 0

        # 3. PATCH each diff with a small concurrency semaphore.
        http = await self.client._get_client()
        sem = asyncio.Semaphore(4)
        first_error: ASCAPIError | None = None

        async def _patch_one(alpha3: str, target: bool) -> None:
            nonlocal first_error
            async with sem:
                ta_id = _encode_territory_availability_id(asc_app_id, alpha3)
                body = {
                    "data": {
                        "type": "territoryAvailabilities",
                        "id": ta_id,
                        "attributes": {"available": target},
                    }
                }
                await self.client._throttle()
                raw = await http.patch(
                    f"{self.client.BASE_URL}/territoryAvailabilities/{ta_id}",
                    json=body,
                )
                if raw.status_code >= 400 and first_error is None:
                    err_body = raw.json() if raw.content else {"errors": []}
                    first_error = ASCAPIError(raw.status_code, err_body)

        await asyncio.gather(
            *[_patch_one(a, t) for a, t in to_patch],
            return_exceptions=False,
        )

        if first_error is not None:
            raise first_error

        logger.info(
            "App availability patched: app=%s flipped=%d",
            asc_app_id, len(to_patch),
        )
        return len(to_patch)
