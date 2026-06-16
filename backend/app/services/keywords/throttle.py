"""Process-global min-interval throttle for outbound iTunes requests.

The iTunes Search / Hints / Lookup endpoints are unauthenticated and hit from
the backend's IP. A burst of requests (e.g. a ranking refresh or competitor
fan-out) risks getting that shared IP throttled by Apple, which would degrade
ranking data for every tenant. Mirroring ``ASCClient``'s min-interval idea, we
serialize a tiny gap between outbound iTunes calls across the whole process.

Single-instance deployment assumption: in-memory state is sufficient. This is a
floor on inter-request spacing, not a token bucket — it does not cap total
volume, only burst rate.
"""

from __future__ import annotations

import asyncio
import time

# ~50-100ms between outbound iTunes requests (mirrors ASCClient's 150ms idea,
# slightly looser since iTunes is more forgiving than ASC's authenticated API).
_MIN_ITUNES_INTERVAL = 0.08  # 80ms (~12 req/s ceiling)

_itunes_lock = asyncio.Lock()
_last_itunes_request_at = 0.0


async def itunes_throttle() -> None:
    """Block until at least ``_MIN_ITUNES_INTERVAL`` has elapsed since the last
    outbound iTunes request (process-global, async-safe).
    """
    global _last_itunes_request_at
    async with _itunes_lock:
        elapsed = time.monotonic() - _last_itunes_request_at
        if elapsed < _MIN_ITUNES_INTERVAL:
            await asyncio.sleep(_MIN_ITUNES_INTERVAL - elapsed)
        _last_itunes_request_at = time.monotonic()


def reset_itunes_throttle() -> None:
    """Reset the throttle clock. Test-only hook."""
    global _last_itunes_request_at
    _last_itunes_request_at = 0.0
