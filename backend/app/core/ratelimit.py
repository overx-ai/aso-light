"""Per-user fixed-window rate limiting for FastAPI endpoints.

Some public-facing endpoints (iTunes-backed keyword search / suggestions) are
authenticated but not app-scoped, and each call fetches Apple from the backend
IP. Without a limit, one user can loop them as a free proxy and get the shared
backend IP throttled, degrading ranking refresh for everyone.

This module exposes :func:`rate_limit`, a dependency factory that returns a
FastAPI dependency enforcing ``per_min`` calls per ``(user_id, name)`` window.
Exceeding the window raises HTTP 429.

In-memory, single-instance deployment assumption (matches the rest of the app's
in-process throttles). The bucket store is process-global and resettable via
:func:`reset_rate_limit_state` so tests can run deterministically.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, status

from app.core.security import get_current_user

# (name, user_id) -> (window_start_monotonic, count_in_window)
_buckets: dict[tuple[str, str], tuple[float, int]] = {}

_WINDOW_SECONDS = 60.0


def reset_rate_limit_state() -> None:
    """Clear all rate-limit buckets. Test-only hook."""
    _buckets.clear()


def _check_and_increment(name: str, user_id: str, per_min: int) -> bool:
    """Return True if the call is allowed; record it. Fixed 60s window."""
    now = time.monotonic()
    key = (name, user_id)
    window_start, count = _buckets.get(key, (now, 0))

    if now - window_start >= _WINDOW_SECONDS:
        # Window expired; start a fresh one.
        window_start, count = now, 0

    if count >= per_min:
        return False

    _buckets[key] = (window_start, count + 1)
    return True


def rate_limit(
    name: str,
    per_min: int = 30,
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Build a per-user rate-limit dependency.

    Args:
        name: Logical endpoint name; namespaces the bucket so different
            endpoints don't share a budget.
        per_min: Max calls per user per 60s window.

    Returns:
        An async FastAPI dependency that resolves to the current user and
        raises HTTP 429 when the user exceeds ``per_min`` within the window.
    """

    async def _dependency(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        user_id = str(current_user["user_id"])
        if not _check_and_increment(name, user_id, per_min):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down and retry shortly.",
            )
        return current_user

    return _dependency
