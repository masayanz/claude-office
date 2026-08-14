"""Tests for content-free Live session checkpoints."""

from datetime import UTC, datetime
from time import perf_counter

import pytest
from sqlalchemy import insert

from app.core.event_processor import EventProcessor
from app.core.state_machine import StateMachine
from app.db.database import AsyncSessionLocal
from app.db.models import EventRecord, SessionCheckpoint, SessionRecord


def test_checkpoint_excludes_conversation_content_and_hydrates_state() -> None:
    machine = StateMachine()
    machine.initialize_main("codex", "gpt-test")
    machine.turn_active = True
    machine.active_turn_id = "turn-1"
    machine.boss_current_task = "SECRET PROMPT"
    machine.last_user_prompt = "SECRET PROMPT"

    checkpoint = machine.to_checkpoint("checkpoint-session")

    assert "SECRET PROMPT" not in repr(checkpoint)
    restored = StateMachine.from_checkpoint(checkpoint)
    assert restored is not None
    assert restored.boss_source == "codex"
    assert restored.turn_active is True
    assert restored.active_turn_id == "turn-1"
    assert restored.boss_current_task is None


def test_corrupted_checkpoint_falls_back() -> None:
    assert StateMachine.from_checkpoint({"version": 1, "state": {}}) is None
    assert StateMachine.from_checkpoint({"version": 99, "state": {}}) is None


@pytest.mark.asyncio
async def test_checkpoint_written_at_bounded_frequency() -> None:
    session_id = "checkpoint-frequency"
    machine = StateMachine()
    machine.initialize_main("codex", "gpt-test")
    processor = EventProcessor()
    processor._events_since_checkpoint[session_id] = 999

    async with AsyncSessionLocal() as db:
        db.add(
            SessionRecord(
                id=session_id,
                status="active",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

    await processor._maybe_persist_checkpoint(session_id, 123, machine)

    async with AsyncSessionLocal() as db:
        checkpoint = await db.get(SessionCheckpoint, session_id)
    assert checkpoint is not None
    assert checkpoint.last_event_id == 123
    assert "prompt" not in repr(checkpoint.state).lower()


@pytest.mark.asyncio
async def test_restore_applies_only_events_after_checkpoint() -> None:
    session_id = "checkpoint-tail"
    timestamp = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    machine = StateMachine()
    machine.initialize_main("codex", "gpt-test")
    checkpoint_state = machine.to_checkpoint(session_id)

    async with AsyncSessionLocal() as db:
        db.add(
            SessionRecord(
                id=session_id,
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        first = EventRecord(
            session_id=session_id,
            timestamp=timestamp,
            event_type="session_start",
            data={"source": "codex", "model": "gpt-test"},
        )
        db.add(first)
        await db.flush()
        db.add(
            SessionCheckpoint(
                session_id=session_id,
                last_event_id=first.id,
                created_at=timestamp,
                state=checkpoint_state,
            )
        )
        db.add(
            EventRecord(
                session_id=session_id,
                timestamp=timestamp,
                event_type="user_prompt_submit",
                data={"source": "codex", "prompt": "not stored in checkpoint"},
            )
        )
        await db.commit()

    restored = await EventProcessor()._build_restored_state_machine(session_id)

    assert restored is not None
    assert restored.boss_state.value == "thinking"
    assert restored.turn_active is True


@pytest.mark.asyncio
@pytest.mark.performance
@pytest.mark.timeout(120)
async def test_restore_200k_events_uses_checkpoint_tail() -> None:
    """A large legacy restore should create a fast tail-only second restore."""
    session_id = "checkpoint-perf-200k"
    timestamp = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)

    async with AsyncSessionLocal() as db:
        await db.execute(
            insert(SessionRecord),
            {
                "id": session_id,
                "status": "active",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        rows = [
            {
                "session_id": session_id,
                "timestamp": timestamp,
                "event_type": "session_start",
                "data": {"source": "codex", "model": "gpt-test"},
            }
        ]
        rows.extend(
            {
                "session_id": session_id,
                "timestamp": timestamp,
                "event_type": "post_tool_use",
                "data": {"tool_name": "Read", "agent_id": "main"},
            }
            for _ in range(199_999)
        )
        await db.execute(insert(EventRecord), rows)
        await db.commit()

    full_start = perf_counter()
    first_restore = await EventProcessor()._build_restored_state_machine(session_id)
    full_elapsed = perf_counter() - full_start

    checkpoint_start = perf_counter()
    second_restore = await EventProcessor()._build_restored_state_machine(session_id)
    checkpoint_elapsed = perf_counter() - checkpoint_start

    assert first_restore is not None
    assert second_restore is not None
    assert checkpoint_elapsed < full_elapsed
