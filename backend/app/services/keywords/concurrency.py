"""Bounded-concurrency fan-out helper for iTunes batch work.

The competitor SERP comparison and the ranking refresh both fan out a list of
iTunes calls over a SINGLE shared client, capped so the backend never opens an
unbounded number of parallel sockets to Apple. They share the exact same shape
(``Semaphore`` + ``asyncio.gather`` over a per-item coroutine), so that shape
lives here once. Pairs with the process-global min-interval throttle in
``throttle.py`` — this bounds in-flight count, the throttle bounds burst rate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def gather_bounded(
    items: Sequence[T],
    run: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
) -> list[R]:
    """Run ``run(item)`` for each item with at most ``concurrency`` in flight.

    Order is preserved: result ``i`` corresponds to ``items[i]``. ``concurrency``
    is floored at 1. The caller owns the shared client (if any) and the cap on
    ``items`` — this helper only bounds how many run at once.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _guarded(item: T) -> R:
        async with semaphore:
            return await run(item)

    return list(await asyncio.gather(*(_guarded(item) for item in items)))
