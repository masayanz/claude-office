"""Low-overhead event-loop diagnostics for the single-worker Backend.

The watchdog deliberately keeps only timing counters.  It never reads the
database, filesystem, request body, or WebSocket payloads, so it is safe to
run independently of application readiness.
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import logging
import sys
import threading
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)


class EventLoopDiagnostics:
    """Measure event-loop lag and emit a thread-stack dump on a real stall."""

    def __init__(self, *, interval: float = 0.5, stall_threshold: float = 3.0) -> None:
        self.interval = max(0.1, interval)
        self.stall_threshold = max(self.interval, stall_threshold)
        self._task: asyncio.Task[None] | None = None
        self._stop_thread = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_thread_reported_heartbeat: float | None = None
        self._heartbeat_at = monotonic()
        self._last_lag = 0.0
        self._max_lag = 0.0
        self._stall_count = 0

    def start(self) -> None:
        """Start the watchdog once the Backend event loop exists."""
        if self._task is None or self._task.done():
            self._heartbeat_at = monotonic()
            self._stop_thread.clear()
            self._last_thread_reported_heartbeat = None
            self._thread = threading.Thread(
                target=self._thread_watchdog,
                name="event-loop-watchdog-thread",
                daemon=True,
            )
            self._thread.start()
            self._task = asyncio.create_task(self._run(), name="event-loop-watchdog")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._stop_thread.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, self.stall_threshold))

    def status(self) -> dict[str, Any]:
        now = monotonic()
        return {
            "heartbeat_age_ms": round(max(0.0, now - self._heartbeat_at) * 1000, 1),
            "last_lag_ms": round(self._last_lag * 1000, 1),
            "max_lag_ms": round(self._max_lag * 1000, 1),
            "stall_count": self._stall_count,
            "interval_ms": round(self.interval * 1000, 1),
            "stall_threshold_ms": round(self.stall_threshold * 1000, 1),
        }

    async def _run(self) -> None:
        expected = monotonic() + self.interval
        while True:
            await asyncio.sleep(self.interval)
            now = monotonic()
            lag = max(0.0, now - expected)
            self._heartbeat_at = now
            self._last_lag = lag
            self._max_lag = max(self._max_lag, lag)
            expected = now + self.interval

    def _thread_watchdog(self) -> None:
        """Observe the loop heartbeat without depending on that loop running."""
        check_interval = max(0.1, min(self.interval, 0.5))
        while not self._stop_thread.wait(check_interval):
            heartbeat = self._heartbeat_at
            lag = monotonic() - heartbeat
            if lag < self.stall_threshold or heartbeat == self._last_thread_reported_heartbeat:
                continue
            self._last_thread_reported_heartbeat = heartbeat
            self._stall_count += 1
            self._max_lag = max(self._max_lag, lag)
            logger.warning(
                "event_loop_stall lag_ms=%.1f stall_count=%d source=thread_watchdog",
                lag * 1000,
                self._stall_count,
            )
            # faulthandler prints only thread/file/line stacks, not local
            # variables or request bodies. It is safe for this diagnostic path.
            try:
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            except (OSError, RuntimeError):
                logger.warning("event_loop_stall stack_dump_failed")


event_loop_diagnostics = EventLoopDiagnostics()
