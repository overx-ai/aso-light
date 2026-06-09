"""Shared async harness for backend pytest modules.

Backend tests should keep the pytest entrypoint as a sync ``def`` and
drive async code with ``run_async(...)``. That avoids relying on
per-module ``pytest-asyncio`` markers or event-loop fixtures.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run_async(awaitable: Awaitable[T]) -> T:
    """Run one awaitable to completion inside a fresh event loop."""
    return asyncio.run(awaitable)
