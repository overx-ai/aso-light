"""Filesystem cache for Apple's per-territory tier ladder.

Apple's auto-renewable subscription tiers (and IAP tiers) are app-wide
universals: every subscription on the app shares the exact same set of
``(tier_num, customer_price, currency_code)`` rows per territory. The
only piece that varies between subscriptions of the same app is the
``price_point_id``, which is a base64-encoded
``{"s": <product_asc_id>, "t": <alpha3>, "p": <tier_num>}`` payload —
trivially computable client-side from the tier number.

So we cache the tier ladder **once per (app, product_type)** rather than
once per subscription, and compute price_point_ids on demand. Subsequent
syncs of additional subscriptions/IAPs become unnecessary.

Disk layout: ``backend/.cache/price_points/{app_asc_id}/{product_type}/{alpha2}.json``

Each file:
    {
      "fetched_at": "...",
      "tiers": [
        {"tier_num": "10001", "customer_price": 0.29,
         "currency_code": "USD", "proceeds": 0.20},
        ...
      ]
    }

``proceeds`` is captured from whichever subscription/IAP triggered the
sync. It is informational only (the apply path doesn't use it). For
subscriptions that recompute proceedsYear2 differently, the cached
value is approximate — refresh by syncing from that specific sub.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.data.territories import ALPHA2_TO_ALPHA3

if TYPE_CHECKING:
    from app.services.asc.pricing import ASCPricingService

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "price_points"
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_path_segment(value: str, label: str) -> None:
    if not value or not _SAFE_PATH_RE.match(value):
        raise ValueError(f"Invalid {label} for cache path: {value!r}")


def compute_price_point_id(
    product_asc_id: str, alpha3: str, tier_num: str,
) -> str:
    """Construct Apple's encoded price_point_id locally.

    Avoids round-tripping to Apple just to learn the ID for a tier we
    already know about app-wide.
    """
    payload = json.dumps(
        {"s": product_asc_id, "t": alpha3, "p": tier_num},
        separators=(",", ":"),
    )
    return base64.b64encode(payload.encode()).decode().rstrip("=")


def _decode_tier_num(price_point_id: str) -> str | None:
    """Extract the ``p`` field from one of Apple's encoded price_point_ids."""
    padded = price_point_id + "=" * (4 - len(price_point_id) % 4)
    try:
        return json.loads(base64.b64decode(padded)).get("p")
    except Exception:
        return None


class PricePointCache:
    """App-wide tier-ladder cache.

    Args:
        app_asc_id: The ASC identifier for the **app** (not the subscription).
        product_type: ``"subscription"`` or ``"iap"`` — separate ladders.
    """

    def __init__(
        self,
        app_asc_id: str,
        product_type: str = "subscription",
    ) -> None:
        _validate_path_segment(app_asc_id, "app_asc_id")
        if product_type not in ("subscription", "iap"):
            raise ValueError(
                f"Invalid product_type: {product_type!r}; "
                f"expected 'subscription' or 'iap'"
            )
        self.app_asc_id = app_asc_id
        self.product_type = product_type
        self._dir = _CACHE_ROOT / app_asc_id / product_type

    def _read_sync(self, alpha2: str) -> list[dict] | None:
        _validate_path_segment(alpha2, "alpha2")
        path = self._dir / f"{alpha2}.json"
        try:
            data = json.loads(path.read_text())
            return data.get("tiers", [])
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt cache file %s, removing", path)
            path.unlink(missing_ok=True)
            return None

    async def get(self, alpha2: str) -> list[dict] | None:
        """Return cached tier ladder for a territory, or None.

        Each entry is ``{"tier_num", "customer_price", "currency_code",
        "proceeds"}`` — without sub-specific price_point_ids.
        """
        return await asyncio.to_thread(self._read_sync, alpha2)

    async def get_with_price_point_ids(
        self, alpha2: str, product_asc_id: str,
    ) -> list[dict] | None:
        """Tier ladder enriched with computed ``price_point_id`` values
        for the given product (subscription or IAP).

        Used by callers that still expect the legacy
        ``{price_point_id, customer_price, proceeds, currency_code}``
        shape (e.g., ``_build_preview_item``'s nearest-match logic).
        """
        tiers = await self.get(alpha2)
        if tiers is None:
            return None
        alpha3 = ALPHA2_TO_ALPHA3.get(alpha2)
        if not alpha3:
            return None
        return [
            {
                "price_point_id": compute_price_point_id(
                    product_asc_id, alpha3, tier["tier_num"],
                ),
                "customer_price": tier["customer_price"],
                "proceeds": tier.get("proceeds", 0.0),
                "currency_code": tier.get("currency_code", ""),
            }
            for tier in tiers
        ]

    async def fetch_and_cache(
        self,
        alpha2: str,
        product_asc_id: str,
        pricing_service: ASCPricingService,
    ) -> list[dict]:
        """Hit Apple for one territory's tiers and store app-wide.

        ``product_asc_id`` is the subscription/IAP id used to make the API
        call (Apple's endpoint requires one); the cached payload strips
        it out so the same data serves every product on the app.
        """
        alpha3 = ALPHA2_TO_ALPHA3.get(alpha2)
        if not alpha3:
            logger.warning("No alpha-3 mapping for %s", alpha2)
            return []

        if self.product_type == "iap":
            raw = await pricing_service.get_iap_price_points(
                product_asc_id, territory_code=alpha3,
            )
        else:
            raw = await pricing_service.get_price_points(
                product_asc_id, territory_code=alpha3,
            )

        tiers: list[dict] = []
        for pp in raw:
            tier_num = _decode_tier_num(pp.get("price_point_id", ""))
            if not tier_num:
                continue
            tiers.append({
                "tier_num": tier_num,
                "customer_price": pp["customer_price"],
                "currency_code": pp.get("currency_code", ""),
                "proceeds": pp.get("proceeds", 0.0),
            })

        await asyncio.to_thread(self._write_sync, alpha2, tiers)
        return tiers

    async def fetch_and_cache_all(
        self,
        territory_codes: list[str],
        product_asc_id: str,
        pricing_service: ASCPricingService,
        concurrency: int = 2,
        skip_cached: bool = True,
    ) -> int:
        """Fetch and cache every territory; skip those already cached.

        Apple's price-points endpoint is rate-limited aggressively for
        IAPs. Retrying a sync should only re-fetch the territories that
        previously failed — not re-do the ones that already succeeded.
        Pass ``skip_cached=False`` to force a full refresh.
        """
        if skip_cached:
            cached_codes = {
                f.stem for f in self._dir.glob("*.json")
            } if self._dir.exists() else set()
            territory_codes = [
                code for code in territory_codes if code not in cached_codes
            ]

        sem = asyncio.Semaphore(concurrency)
        total = 0

        async def _fetch_one(code: str) -> int:
            async with sem:
                tiers = await self.fetch_and_cache(
                    code, product_asc_id, pricing_service,
                )
                return len(tiers)

        results = await asyncio.gather(
            *[_fetch_one(code) for code in territory_codes],
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to cache tiers for %s: %s",
                    territory_codes[i], result,
                )
            else:
                total += result
        return total

    def _status_sync(self) -> dict:
        if not self._dir.exists():
            return {"cached_territories": 0, "synced_at": None}
        files = list(self._dir.glob("*.json"))
        if not files:
            return {"cached_territories": 0, "synced_at": None}
        oldest_ts: str | None = None
        for f in files:
            try:
                data = json.loads(f.read_text())
                ts = data.get("fetched_at")
                if ts and (oldest_ts is None or ts < oldest_ts):
                    oldest_ts = ts
            except (json.JSONDecodeError, OSError):
                continue
        return {
            "cached_territories": len(files),
            "synced_at": oldest_ts,
        }

    async def status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        if self._dir.exists():
            shutil.rmtree(self._dir)

    def _write_sync(self, alpha2: str, tiers: list[dict]) -> None:
        _validate_path_segment(alpha2, "alpha2")
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{alpha2}.json"
        data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "tiers": tiers,
        }
        path.write_text(json.dumps(data, indent=2))
