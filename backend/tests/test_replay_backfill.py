"""Regression tests for legacy-event Replay compatibility."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.event_processor import EventProcessor
from app.db.database import AsyncSessionLocal
from app.db.models import (
    EventRecord,
    ReplayEventRecord,
    ReplaySessionTombstone,
    SessionRecord,
)
from app.db.replay_backfill import backfill_replay_history
from app.models.events import EventAdapter


@pytest.mark.asyncio
async def test_backfill_is_safe_and_idempotent() -> None:
    session_id = f"legacy-replay-{uuid4()}"
    timestamp = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    async with AsyncSessionLocal() as db:
        db.add(
            SessionRecord(
                id=session_id,
                project_name="legacy-project",
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        db.add(
            EventRecord(
                session_id=session_id,
                timestamp=timestamp,
                event_type="user_prompt_submit",
                data={"source": "codex", "project_name": "legacy-project", "prompt": "SECRET"},
            )
        )
        await db.commit()

    first = await backfill_replay_history(batch_size=1)
    second = await backfill_replay_history(batch_size=1)

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(ReplayEventRecord).where(
                        ReplayEventRecord.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert len(rows) == 1
    assert "SECRET" not in repr(rows[0].safe_data)
    assert rows[0].source_event_id is not None


@pytest.mark.asyncio
async def test_tombstoned_legacy_session_is_not_rebuilt() -> None:
    session_id = f"legacy-tombstone-{uuid4()}"
    timestamp = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    async with AsyncSessionLocal() as db:
        db.add(
            SessionRecord(
                id=session_id,
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        db.add(
            EventRecord(
                session_id=session_id,
                timestamp=timestamp,
                event_type="pre_tool_use",
                data={"source": "codex", "tool_name": "Read"},
            )
        )
        db.add(ReplaySessionTombstone(session_id=session_id, deleted_at=timestamp))
        await db.commit()

    result = await backfill_replay_history()

    async with AsyncSessionLocal() as db:
        count = await db.scalar(
            select(ReplayEventRecord.id).where(ReplayEventRecord.session_id == session_id)
        )
    assert result["inserted"] == 0
    assert count is None


@pytest.mark.asyncio
async def test_hybrid_processor_persists_one_replay_row_for_one_accepted_event() -> None:
    """The processor is the single Replay persistence boundary after dedupe."""
    session_id = f"replay-live-{uuid4()}"
    event = EventAdapter.validate_python(
        {
            "event_type": "pre_tool_use",
            "session_id": session_id,
            "timestamp": "2026-08-13T10:00:00Z",
            "data": {"source": "codex", "tool_name": "Read", "tool_use_id": "one"},
        }
    )

    await EventProcessor().process_event(event)

    async with AsyncSessionLocal() as db:
        live_count = await db.scalar(
            select(EventRecord.id).where(EventRecord.session_id == session_id)
        )
        replay_count = await db.scalar(
            select(ReplayEventRecord.id).where(ReplayEventRecord.session_id == session_id)
        )
    assert live_count is not None
    assert replay_count is not None
