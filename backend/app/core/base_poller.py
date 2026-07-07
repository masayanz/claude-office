"""Generic per-session polling loop shared by transcript/task-file/beads pollers.

The three concrete pollers each used to reimplement an identical skeleton
(per-key registry, asyncio task lifecycle, ``stop_all``) and drifted
independently — most notably, none of them awaited the cancelled tasks in
``stop_all`` (ARC-013). ``BasePoller`` owns that scaffolding once; subclasses
keep only their ``_check`` logic and any public ``start_polling`` signature
specific to their domain.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BasePoller[TState](ABC):
    """Shared per-key polling loop.

    Subclasses implement :meth:`_check` and :meth:`_get_poll_interval`, and
    expose a public ``start_polling`` whose signature matches the domain
    (e.g. ``(agent_id, session_id, transcript_path)`` for transcripts). The
    subclass ``start_polling`` builds the state object and delegates to
    :meth:`_register_polling`.

    The ``key`` parameter throughout this class is the registry identifier —
    for the task-file and beads pollers it is the session id; for the
    transcript poller it is the agent id (one poll task per subagent
    transcript).

    To terminate a single poll loop from inside ``_check`` (e.g. on an
    inactivity timeout or a zombie-agent emission), remove the entry from
    ``self._sessions`` and return; the loop notices the missing entry on its
    next iteration and exits cleanly.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _get_poll_interval(self) -> float:
        """Seconds to sleep between checks. Override per subclass if dynamic."""
        return 1.0

    async def _register_polling(
        self,
        key: str,
        state: TState,
        *,
        task_name: str | None = None,
    ) -> None:
        """Register ``state`` and spawn the poll loop task for ``key``.

        Idempotent: if ``key`` is already being polled, the existing task is
        left alone and the new ``state`` is discarded.
        """
        async with self._lock:
            if key in self._tasks:
                return
            self._sessions[key] = state
            self._tasks[key] = asyncio.create_task(
                self._poll_loop(key),
                name=task_name or f"{type(self).__name__}_poll_{key}",
            )

    async def is_polling(self, key: str) -> bool:
        """Return True if polling is currently active for ``key``."""
        async with self._lock:
            return key in self._sessions

    async def stop_polling(self, key: str) -> None:
        """Stop polling for ``key`` and await the cancelled task.

        Releases the lock before awaiting so a poll loop contending for it
        is not deadlocked (PR#44 regression). Safe to call when no task
        exists for ``key`` (no-op).
        """
        async with self._lock:
            task = self._tasks.pop(key, None)
            self._sessions.pop(key, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def stop_all(self) -> None:
        """Stop every active poll task and await each cancellation.

        Awaits each cancelled task so callers can be sure no poll loop is
        still running when this returns — the bug ``stop_all`` had in every
        concrete poller before ARC-013 (none of them awaited, leaving tasks
        to be GC'd mid-cancellation).
        """
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
            self._sessions.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _poll_loop(self, key: str) -> None:
        """Default loop: fetch state, call ``_check``, sleep, repeat.

        Exits cleanly when ``key`` is no longer in ``_sessions`` (e.g. after
        ``stop_polling``, ``stop_all``, or a subclass removing the entry from
        ``_check`` to signal a terminal condition such as a zombie agent).
        """
        try:
            while True:
                async with self._lock:
                    state = self._sessions.get(key)
                if state is None:
                    return
                try:
                    await self._check(key, state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Poll error (%s) for key %s", type(self).__name__, key)
                await asyncio.sleep(self._get_poll_interval())
        except asyncio.CancelledError:
            logger.debug("Poll loop for %s cancelled", key)
            raise

    @abstractmethod
    async def _check(self, key: str, state: TState) -> None:
        """Poll once for ``key``. Subclasses implement domain-specific logic.

        To terminate the loop (e.g. inactivity timeout, zombie agent), remove
        the entry from ``self._sessions`` under ``self._lock`` and return.
        """
        ...
