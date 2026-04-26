"""Filesystem cache for Apple price points (subscriptions and IAPs)."""

from __future__ import annotations

import asyncio
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

# Only allow alphanumeric, hyphens, and underscores in path segments.
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_path_segment(value: str, label: str) -> None:
    """Raise ValueError if a value is unsafe for use in a filesystem path."""
    if not value or not _SAFE_PATH_RE.match(value):
        raise ValueError(
            f"Invalid {label} for cache path: {value!r}"
        )


class PricePointCache:
    """Filesystem cache for Apple price points (subscriptions and IAPs).

    Stores one JSON file per territory under:
        backend/.cache/price_points/{product_asc_id}/{alpha2}.json

    Each file contains ~80 price point entries (~5 KB).

    Args:
        product_asc_id: The ASC identifier for the subscription or IAP.
        product_type: Either ``"subscription"`` or ``"iap"``.
    """

    def __init__(
        self,
        product_asc_id: str,
        product_type: str = "subscription",
    ) -> None:
        _validate_path_segment(product_asc_id, "product_asc_id")
        if product_type not in ("subscription", "iap"):
            raise ValueError(
                f"Invalid product_type: {product_type!r}; "
                f"expected 'subscription' or 'iap'"
            )
        self.product_asc_id = product_asc_id
        self.product_type = product_type
        self._dir = _CACHE_ROOT / product_asc_id

    def _get_sync(self, territory_alpha2: str) -> list[dict] | None:
        """Synchronous read of cached price points."""
        _validate_path_segment(territory_alpha2, "territory_alpha2")
        path = self._dir / f"{territory_alpha2}.json"
        try:
            data = json.loads(path.read_text())
            return data.get("price_points", [])
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt cache file %s, removing", path)
            path.unlink(missing_ok=True)
            return None

    async def get(self, territory_alpha2: str) -> list[dict] | None:
        """Read cached price points for a territory. Returns None if not cached."""
        return await asyncio.to_thread(self._get_sync, territory_alpha2)

    async def fetch_and_cache(
        self,
        territory_alpha2: str,
        pricing_service: ASCPricingService,
    ) -> list[dict]:
        """Fetch price points for one territory from Apple and cache to disk."""
        alpha3 = ALPHA2_TO_ALPHA3.get(territory_alpha2)
        if not alpha3:
            logger.warning("No alpha-3 mapping for %s", territory_alpha2)
            return []

        if self.product_type == "iap":
            raw = await pricing_service.get_iap_price_points(
                self.product_asc_id, territory_code=alpha3
            )
        else:
            raw = await pricing_service.get_price_points(
                self.product_asc_id, territory_code=alpha3
            )

        price_points = [
            {
                "price_point_id": pp["price_point_id"],
                "customer_price": pp["customer_price"],
                "proceeds": pp["proceeds"],
                "currency_code": pp["currency_code"],
            }
            for pp in raw
        ]

        await asyncio.to_thread(self._write_sync, territory_alpha2, price_points)
        return price_points

    async def fetch_and_cache_all(
        self,
        territory_codes: list[str],
        pricing_service: ASCPricingService,
        concurrency: int = 2,
    ) -> int:
        """Fetch and cache price points for multiple territories in parallel.

        Returns total number of price points cached.
        """
        sem = asyncio.Semaphore(concurrency)
        total = 0

        async def _fetch_one(code: str) -> int:
            async with sem:
                pps = await self.fetch_and_cache(code, pricing_service)
                return len(pps)

        results = await asyncio.gather(
            *[_fetch_one(code) for code in territory_codes],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to cache price points for %s: %s",
                    territory_codes[i],
                    result,
                )
            else:
                total += result

        return total

    def _status_sync(self) -> dict:
        """Return cache status: territory count and oldest/newest timestamps."""
        if not self._dir.exists():
            return {"cached_territories": 0, "synced_at": None}

        files = list(self._dir.glob("*.json"))
        if not files:
            return {"cached_territories": 0, "synced_at": None}

        # Read the fetched_at from the first file as representative timestamp
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
        """Return cache status info."""
        return await asyncio.to_thread(self._status_sync)

    async def clear(self) -> None:
        """Delete all cached price points for this product."""
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        if self._dir.exists():
            shutil.rmtree(self._dir)

    def _write_sync(
        self, territory_alpha2: str, price_points: list[dict]
    ) -> None:
        """Write price points to a territory cache file."""
        _validate_path_segment(territory_alpha2, "territory_alpha2")
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{territory_alpha2}.json"
        data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "price_points": price_points,
        }
        path.write_text(json.dumps(data, indent=2))
