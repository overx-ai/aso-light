"""Client for the rate-cache-api exchange rate service."""

import logging

import httpx

logger = logging.getLogger(__name__)


class RateCacheError(Exception):
    """Error communicating with the rate-cache-api."""

    pass


class RateCacheClient:
    """Async client for the rate-cache-api (deployed at api.overx.ai)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_rates(self, base: str = "USD") -> dict[str, float]:
        """Fetch exchange rates for the given base currency.

        GET /api/v1/rates?base=USD
        Returns: {"EUR": 0.865, "JPY": 159.09, "KRW": 1501.22, ...}
        """
        url = f"{self.base_url}/api/v1/rates"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params={"base": base})
                response.raise_for_status()
                data = response.json()
                rates = data.get("rates", {})
                if not rates:
                    raise RateCacheError("No rates returned from rate-cache-api")
                logger.info(
                    "Fetched %d exchange rates (base=%s, stale=%s)",
                    len(rates),
                    base,
                    data.get("is_stale", "unknown"),
                )
                return rates
        except httpx.HTTPStatusError as exc:
            raise RateCacheError(
                f"Rate-cache-api returned {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RateCacheError(
                f"Failed to connect to rate-cache-api: {exc}"
            ) from exc
