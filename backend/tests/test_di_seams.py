"""Regression tests for the dependency-injection seams (ARC-012).

These tests exist to lock in the fix for the bug where ``override_manager`` /
``override_event_processor`` rebound the module-level singleton but never
reached consumers that had captured the old reference at import time. The
production code now resolves the singleton at *use* time via
``get_manager()`` / ``get_event_processor()``, so overrides actually take
effect.

Each test overrides the singleton with a stub, invokes a code path that
consumes it, and asserts the stub received the call. On the pre-ARC-012 code
these tests would fail because the consumers held a captured reference to the
real singleton.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.broadcast_service import broadcast_state
from app.core.connection_manager import (
    ConnectionManager,
    get_manager,
    override_manager,
)
from app.core.event_processor import (
    EventProcessor,
    get_event_processor,
    override_event_processor,
)
from app.core.state_machine import StateMachine


class _RecordingManager(ConnectionManager):
    """``ConnectionManager`` subclass that records every broadcast call."""

    def __init__(self) -> None:
        super().__init__()
        self.broadcast_calls: list[tuple[dict[str, Any], str]] = []

    async def broadcast(self, message: dict[str, Any], session_id: str) -> None:
        self.broadcast_calls.append((message, session_id))


@pytest.fixture(autouse=True)
def _restore_singletons() -> Any:  # pyright: ignore[reportUnusedFunction]
    """Restore the real singletons after each test (overrides are global)."""
    real_manager = get_manager()
    real_processor = get_event_processor()
    yield
    override_manager(real_manager)
    override_event_processor(real_processor)


def test_override_manager_takes_effect_through_get_manager() -> None:
    """``get_manager()`` must return the overridden instance, not the captured one."""
    stub = _RecordingManager()
    override_manager(stub)
    assert get_manager() is stub


def test_override_event_processor_takes_effect_through_get_event_processor() -> None:
    """``get_event_processor()`` must return the overridden instance."""
    real = get_event_processor()
    # ``EventProcessor`` requires no external resources to construct.
    stub = EventProcessor()
    override_event_processor(stub)
    assert get_event_processor() is stub
    assert get_event_processor() is not real


@pytest.mark.asyncio
async def test_broadcast_service_uses_overridden_manager() -> None:
    """``broadcast_state`` must route through the overridden manager.

    This is the core regression: before ARC-012, ``broadcast_service`` captured
    ``manager`` at import time, so overriding the singleton had no effect on
    broadcasts. With the accessor-based lookup, the stub receives the message.
    """
    stub = _RecordingManager()
    override_manager(stub)

    sm = StateMachine()
    await broadcast_state("session-abc", sm)

    assert len(stub.broadcast_calls) == 1
    message, sid = stub.broadcast_calls[0]
    assert sid == "session-abc"
    assert message["type"] == "state_update"
