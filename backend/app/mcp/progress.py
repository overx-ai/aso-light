"""MCP progress for long-running tools.

A client drops a tool call that sends nothing for a while (Claude Code:
300 s), and the server then finishes the work for nobody. ``ProgressReporter``
reports each finished unit and, while one unit runs long, repeats the last
state every ``PROGRESS_HEARTBEAT_SECONDS``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import TracebackType
from typing import Protocol

logger = logging.getLogger(__name__)

PROGRESS_HEARTBEAT_SECONDS = 30.0


class ProgressSink(Protocol):
    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None: ...


class ProgressReporter:
    """Reports ``done/total`` through a FastMCP ``Context``; a no-op without one.

    ``Context.report_progress`` itself does nothing when the client sent no
    ``progressToken``.
    """

    def __init__(self, ctx: ProgressSink | None, total: int) -> None:
        self._ctx = ctx
        self._total = total
        self._done = 0
        self._message = "starting"
        self._since = time.monotonic()
        self._heartbeat: asyncio.Task[None] | None = None

    async def __aenter__(self) -> ProgressReporter:
        if self._ctx is not None:
            await self._send(self._message)
            self._heartbeat = asyncio.create_task(self._beat())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            # gather, not suppress(CancelledError): a cancel aimed at the
            # caller while it waits here must still propagate.
            await asyncio.gather(self._heartbeat, return_exceptions=True)

    async def advance(self, message: str) -> None:
        self._done += 1
        self._message = message
        self._since = time.monotonic()
        await self._send(message)

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(PROGRESS_HEARTBEAT_SECONDS)
            elapsed = int(time.monotonic() - self._since)
            await self._send(f"still working, {elapsed}s after: {self._message}")

    async def _send(self, message: str) -> None:
        if self._ctx is None:
            return
        try:
            await self._ctx.report_progress(self._done, self._total, message)
        except Exception:
            # Progress is advisory: a client that went away must not abort a
            # half-applied write run between a delete and its upload.
            logger.warning(
                "progress report failed at %s/%s",
                self._done,
                self._total,
                exc_info=True,
            )
