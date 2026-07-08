"""Bounds and growth-control tests for ARC-015.

Covers:
- ``EventProcessor.evict_idle_sessions`` drops idle in-memory StateMachines
  but never ones that still have a WebSocket viewer.
- ``_reap_stale_sessions`` deletes EventRecord rows for completed sessions
  ONLY when ``EVENT_RETENTION_DAYS > 0``; the default (0) preserves every
  row so replay keeps working.
- Settings defaults are the opt-in values documented in AUDIT_REMEDIATION.md.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core.event_processor import EventProcessor
from app.core.state_machine import StateMachine
from app.db.database import AsyncSessionLocal
from app.db.models import EventRecord, SessionRecord

# ---------------------------------------------------------------------------
# Idle eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_idle_sessions_drops_stale_without_viewers() -> None:
    """Idle StateMachines with no WebSocket viewers are dropped; fresh ones kept."""
    ep = EventProcessor()
    stale_sm = StateMachine()
    stale_sm.last_event_at = datetime.now(UTC) - timedelta(hours=12)
    ep.sessions["stale-sid"] = stale_sm

    fresh_sm = StateMachine()  # last_event_at defaults to now
    ep.sessions["fresh-sid"] = fresh_sm

    count = await ep.evict_idle_sessions(max_idle_seconds=21600)  # 6h

    assert count == 1
    assert "stale-sid" not in ep.sessions
    assert "fresh-sid" in ep.sessions


@pytest.mark.asyncio
async def test_evict_idle_sessions_keeps_session_with_active_viewer() -> None:
    """A session with a live WebSocket viewer is never evicted (ARC-015 Do-NOT).

    Even when the session is well past the idle cutoff, an active viewer means
    eviction would force a DB-replay churn on reconnect — keep it warm.
    """
    from app.core.connection_manager import get_manager

    manager = get_manager()
    ws = AsyncMock()
    await manager.connect(ws, "viewed-sid")
    try:
        ep = EventProcessor()
        viewed_sm = StateMachine()
        viewed_sm.last_event_at = datetime.now(UTC) - timedelta(hours=12)
        ep.sessions["viewed-sid"] = viewed_sm

        count = await ep.evict_idle_sessions(max_idle_seconds=60)

        assert count == 0
        assert "viewed-sid" in ep.sessions
    finally:
        await manager.disconnect(ws, "viewed-sid")


@pytest.mark.asyncio
async def test_evict_idle_sessions_zero_count_when_nothing_stale() -> None:
    """An empty/fresh registry reports zero evictions."""
    ep = EventProcessor()
    assert await ep.evict_idle_sessions(max_idle_seconds=60) == 0


# ---------------------------------------------------------------------------
# Event retention
# ---------------------------------------------------------------------------


async def _seed_completed_session(sid: str, age: timedelta) -> None:
    """Insert a completed SessionRecord + one EventRecord, both ``age`` old."""
    old = datetime.now(UTC) - age
    async with AsyncSessionLocal() as db:
        db.add(
            SessionRecord(
                id=sid,
                status="completed",
                created_at=old,
                updated_at=old,
            )
        )
        db.add(
            EventRecord(
                session_id=sid,
                timestamp=old,
                event_type="session_start",
                data={"project_name": "old"},
            )
        )
        await db.commit()


async def _count_events(sid: str) -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.scalar(
                select(func.count()).select_from(EventRecord).where(EventRecord.session_id == sid)
            )
            or 0
        )


@pytest.mark.asyncio
async def test_reap_preserves_events_when_retention_zero() -> None:
    """Default EVENT_RETENTION_DAYS=0 deletes nothing (ARC-015 Do-NOT)."""
    from app.main import _reap_stale_sessions, settings

    assert settings.EVENT_RETENTION_DAYS == 0  # precondition

    sid = "retention-default-keep"
    await _seed_completed_session(sid, timedelta(days=30))

    await _reap_stale_sessions()

    assert await _count_events(sid) == 1  # event row preserved


@pytest.mark.asyncio
async def test_reap_deletes_events_when_retention_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With EVENT_RETENTION_DAYS>0, old completed sessions' events are deleted."""
    import app.main as main_mod
    from app.main import _reap_stale_sessions

    sid = "retention-optin-delete"
    await _seed_completed_session(sid, timedelta(days=30))

    # Opt in to a 7-day retention window by swapping the module-level settings
    # for a copy with the override. monkeypatch restores the original on
    # teardown. model_copy avoids any pydantic setattr/validation concerns.
    monkeypatch.setattr(
        main_mod,
        "settings",
        main_mod.settings.model_copy(update={"EVENT_RETENTION_DAYS": 7}),
    )
    await _reap_stale_sessions()

    assert await _count_events(sid) == 0  # event row deleted


@pytest.mark.asyncio
async def test_reap_keeps_recent_events_when_retention_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events newer than the retention window are NOT deleted even when opted in."""
    import app.main as main_mod
    from app.main import _reap_stale_sessions

    sid = "retention-recent-keep"
    # Only 1 day old — inside a 7-day retention window.
    await _seed_completed_session(sid, timedelta(days=1))

    monkeypatch.setattr(
        main_mod,
        "settings",
        main_mod.settings.model_copy(update={"EVENT_RETENTION_DAYS": 7}),
    )
    await _reap_stale_sessions()

    assert await _count_events(sid) == 1  # still present


# ---------------------------------------------------------------------------
# Settings defaults (declared, not env-overridden)
# ---------------------------------------------------------------------------


def test_settings_defaults_are_opt_in() -> None:
    """ARC-015 knobs default to opt-in values (no deletion, 6h idle eviction)."""
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["EVENT_RETENTION_DAYS"].default == 0
    assert fields["SESSION_IDLE_EVICT_SECONDS"].default == 21600
