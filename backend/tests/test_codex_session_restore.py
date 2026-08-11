"""Codex startup restoration: bounded parsing, state rebuild, and live merge."""

# pyright: reportPrivateUsage=false

import asyncio
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.routes import codex_restore as codex_restore_route
from app.core.codex_session_restorer import (
    CodexSessionRestorer,
    RestoredAgent,
    RestoredSession,
    _scan_snapshots,
)
from app.core.event_processor import EventProcessor
from app.db.database import AsyncSessionLocal
from app.db.models import SessionRecord
from app.main import app
from app.models.agents import AgentState, BossState
from app.models.common import TodoItem, TodoStatus
from app.models.events import (
    AgentEvent,
    AgentEventData,
    EventType,
    SessionEvent,
    SessionEventData,
    ToolEvent,
    ToolEventData,
)

ROOT = "11111111-1111-4111-8111-111111111111"
AGENT_A = "22222222-2222-4222-8222-222222222222"
FOLLOWUP_A = "33333333-3333-4333-8333-333333333333"
NESTED = "44444444-4444-4444-8444-444444444444"


def _write_lines(
    path: Path, rows: Sequence[dict[str, object] | str], mtime: float
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(row if isinstance(row, str) else json.dumps(row) for row in rows)
    path.write_text(text + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _journal_event(
    event_type: str,
    session_id: str,
    timestamp: datetime,
    **data: str,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "session_id": session_id,
        "timestamp": timestamp.isoformat(),
        "data": {"source": "codex", **data},
    }


def _native_row(top_type: str, timestamp: datetime, **payload: object) -> dict[str, object]:
    return {"timestamp": timestamp.isoformat(), "type": top_type, "payload": payload}


def test_journal_reconstructs_state_and_respects_terminal_events(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    active = ROOT
    ended = "55555555-5555-4555-8555-555555555555"
    rows = [
        _journal_event(
            "session_start", active, now - timedelta(minutes=5),
            project_name="project", model="gpt-main",
        ),
        _journal_event(
            "subagent_start", active, now - timedelta(minutes=4),
            agent_id=AGENT_A, agent_type="worker", model="gpt-child",
        ),
        _journal_event(
            "pre_tool_use", active, now - timedelta(minutes=3),
            agent_id=AGENT_A, tool_name="AgentWait", tool_use_id="wait-1",
            model="gpt-child",
        ),
        _journal_event(
            "pre_tool_use", active, now - timedelta(minutes=2),
            tool_name="Bash", tool_use_id="main-1", model="gpt-main",
        ),
        _journal_event("session_start", ended, now - timedelta(minutes=4)),
        _journal_event("session_end", ended, now - timedelta(minutes=1)),
        "{broken-final-line",
    ]
    path = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(path, rows, now.timestamp())

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert [snapshot.session_id for snapshot in snapshots] == [active]
    snapshot = snapshots[0]
    assert snapshot.project_name == "project"
    assert snapshot.model == "gpt-main"
    assert snapshot.boss_state == "working"
    assert snapshot.last_tool_name == "Bash"
    assert len(snapshot.agents) == 1
    assert snapshot.agents[0].agent_id == AGENT_A
    assert snapshot.agents[0].state == "waiting"
    assert snapshot.agents[0].model == "gpt-child"


def test_pre_post_and_subagent_stop_do_not_restore_work(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    rows = [
        _journal_event("session_start", ROOT, now - timedelta(minutes=5)),
        _journal_event("subagent_start", ROOT, now - timedelta(minutes=4), agent_id=AGENT_A),
        _journal_event(
            "pre_tool_use", ROOT, now - timedelta(minutes=3),
            agent_id=AGENT_A, tool_name="Read", tool_use_id="tool-1",
        ),
        _journal_event(
            "post_tool_use", ROOT, now - timedelta(minutes=2),
            agent_id=AGENT_A, tool_name="Read", tool_use_id="tool-1",
        ),
        _journal_event("subagent_stop", ROOT, now - timedelta(minutes=1), agent_id=AGENT_A),
    ]
    path = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(path, rows, now.timestamp())

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 1
    assert snapshots[0].boss_state == "idle"
    assert snapshots[0].agents == []


def test_multiple_model_less_sessions_are_distinct_and_capped_at_ten(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    rows: list[dict[str, object] | str] = []
    session_ids = [f"session-{index}" for index in range(12)]
    for index, session_id in enumerate(session_ids):
        rows.append(
            _journal_event(
                "session_start",
                session_id,
                now - timedelta(seconds=index),
                project_name=f"project-{index}",
            )
        )
    path = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(path, rows, now.timestamp())

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 10
    assert len({snapshot.session_id for snapshot in snapshots}) == 10
    assert all(snapshot.model is None for snapshot in snapshots)


def test_journal_files_are_applied_oldest_to_newest(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old = tmp_path / "claude-office-events" / f"{now - timedelta(days=1):%Y-%m-%d}.jsonl"
    new = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(
        old,
        [_journal_event("session_start", ROOT, now - timedelta(minutes=2))],
        now.timestamp(),
    )
    _write_lines(
        new,
        [_journal_event("session_end", ROOT, now - timedelta(minutes=1))],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert snapshots == []


def test_out_of_order_journal_events_are_applied_by_timestamp(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    rows = [
        _journal_event("session_start", ROOT, now - timedelta(minutes=5)),
        # New completions can be appended first when their hook process exits
        # before an older Start/Pre process.
        _journal_event("stop", ROOT, now - timedelta(minutes=1)),
        _journal_event(
            "post_tool_use",
            ROOT,
            now - timedelta(minutes=2),
            tool_name="Bash",
            tool_use_id="tool-1",
        ),
        _journal_event("user_prompt_submit", ROOT, now - timedelta(minutes=4)),
        _journal_event(
            "pre_tool_use",
            ROOT,
            now - timedelta(minutes=3),
            tool_name="Bash",
            tool_use_id="tool-1",
        ),
    ]
    path = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(path, rows, now.timestamp())

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 1
    assert snapshots[0].boss_state == "idle"
    assert snapshots[0].last_tool_name is None


def test_newer_journal_post_suppresses_older_native_pending_tool(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    _write_lines(
        session_dir / f"rollout-native-pending-{ROOT}.jsonl",
        [
            _native_row(
                "session_meta", now - timedelta(minutes=6), id=ROOT, session_id=ROOT
            ),
            _native_row(
                "event_msg", now - timedelta(minutes=5), type="task_started"
            ),
            _native_row(
                "response_item",
                now - timedelta(minutes=4),
                type="function_call",
                call_id="tool-1",
                name="wait_agent",
            ),
        ],
        now.timestamp(),
    )
    journal = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(
        journal,
        [
            _journal_event("session_start", ROOT, now - timedelta(minutes=7)),
            _journal_event(
                "post_tool_use",
                ROOT,
                now - timedelta(minutes=1),
                tool_name="AgentWait",
                tool_use_id="tool-1",
            ),
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 1
    assert snapshots[0].boss_state == "working"
    assert snapshots[0].last_tool_name is None


def test_newer_journal_stop_suppresses_older_native_active_and_pending(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    _write_lines(
        session_dir / f"rollout-native-active-{ROOT}.jsonl",
        [
            _native_row(
                "session_meta", now - timedelta(minutes=6), id=ROOT, session_id=ROOT
            ),
            _native_row(
                "event_msg", now - timedelta(minutes=5), type="task_started"
            ),
            _native_row(
                "response_item",
                now - timedelta(minutes=4),
                type="function_call",
                call_id="tool-1",
                name="wait_agent",
            ),
        ],
        now.timestamp(),
    )
    journal = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(
        journal,
        [
            _journal_event("session_start", ROOT, now - timedelta(minutes=7)),
            _journal_event("stop", ROOT, now - timedelta(minutes=1)),
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 1
    assert snapshots[0].boss_state == "idle"
    assert snapshots[0].last_tool_name is None


def test_terminal_marker_blocks_restore_when_session_end_left_journal_tail(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    journal = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(
        journal,
        [_journal_event("session_start", ROOT, now - timedelta(minutes=5))],
        now.timestamp(),
    )
    marker = tmp_path / "claude-office-events" / "terminal" / f"{ROOT}.json"
    _write_lines(
        marker,
        [
            {
                "event_type": "session_end",
                "session_id": ROOT,
                "timestamp": (now - timedelta(minutes=1)).isoformat(),
            }
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert snapshots == []


def test_newer_session_start_wins_over_an_older_terminal_marker(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    journal = tmp_path / "claude-office-events" / f"{now:%Y-%m-%d}.jsonl"
    _write_lines(
        journal,
        [_journal_event("session_start", ROOT, now - timedelta(minutes=1))],
        now.timestamp(),
    )
    marker = tmp_path / "claude-office-events" / "terminal" / f"{ROOT}.json"
    _write_lines(
        marker,
        [
            {
                "event_type": "session_end",
                "session_id": ROOT,
                "timestamp": (now - timedelta(minutes=2)).isoformat(),
            }
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert [snapshot.session_id for snapshot in snapshots] == [ROOT]


def test_native_followup_same_path_is_one_agent_but_nested_path_is_distinct(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    root_rows = [
        _native_row("session_meta", now - timedelta(minutes=5), id=ROOT, session_id=ROOT,
                    cwd=r"C:\Users\private\project"),
        _native_row("turn_context", now - timedelta(minutes=4), model="gpt-main"),
        _native_row("event_msg", now - timedelta(minutes=3), type="task_started"),
    ]
    initial_rows = [
        _native_row("session_meta", now - timedelta(minutes=4), id=AGENT_A,
                    session_id=ROOT, parent_thread_id=ROOT, agent_path="/root/worker",
                    cwd=r"C:\Users\private\project", agent_role="worker"),
        _native_row("event_msg", now - timedelta(minutes=3), type="task_started"),
        _native_row("event_msg", now - timedelta(minutes=2), type="task_complete"),
    ]
    followup_rows = [
        _native_row("session_meta", now - timedelta(minutes=2), id=FOLLOWUP_A,
                    session_id=ROOT, parent_thread_id=AGENT_A,
                    cwd=r"C:\Users\private\project", agent_role="worker"),
        _native_row("turn_context", now - timedelta(minutes=2), model="gpt-child"),
        _native_row("event_msg", now - timedelta(minutes=1), type="task_started"),
        _native_row("response_item", now - timedelta(seconds=30), type="function_call",
                    call_id="wait-1", name="wait_agent"),
    ]
    nested_rows = [
        _native_row("session_meta", now - timedelta(minutes=2), id=NESTED,
                    session_id=ROOT, parent_thread_id=AGENT_A,
                    agent_path="/root/worker/nested", cwd=r"C:\Users\private\project"),
        _native_row("event_msg", now - timedelta(minutes=1), type="task_started"),
    ]
    for index, (thread_id, rows) in enumerate(
        (
            (ROOT, root_rows),
            (AGENT_A, initial_rows),
            (FOLLOWUP_A, followup_rows),
            (NESTED, nested_rows),
        )
    ):
        mtime = (
            (now - timedelta(minutes=40)).timestamp()
            if thread_id == ROOT
            else now.timestamp() + index
        )
        _write_lines(
            session_dir / f"rollout-2026-08-09T00-00-0{index}-{thread_id}.jsonl",
            rows,
            mtime,
        )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now + timedelta(seconds=10)
    )

    snapshot = next(item for item in snapshots if item.session_id == ROOT)
    assert snapshot.project_name == "project"
    assert snapshot.model == "gpt-main"
    assert {agent.agent_id for agent in snapshot.agents} == {AGENT_A, NESTED}
    worker = next(agent for agent in snapshot.agents if agent.agent_id == AGENT_A)
    assert worker.state == "waiting"
    assert worker.model == "gpt-child"


def test_agent_order_uses_immutable_session_meta_time_across_rescans(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    boundary = now + timedelta(seconds=10)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    root_path = session_dir / f"rollout-root-{ROOT}.jsonl"
    first_path = session_dir / f"rollout-first-{AGENT_A}.jsonl"
    second_path = session_dir / f"rollout-second-{NESTED}.jsonl"
    first_started = now - timedelta(minutes=4)
    second_started = now - timedelta(minutes=3)
    _write_lines(
        root_path,
        [
            _native_row("session_meta", now - timedelta(minutes=5), id=ROOT,
                        session_id=ROOT),
            _native_row("event_msg", now - timedelta(minutes=4), type="task_started"),
        ],
        now.timestamp(),
    )
    _write_lines(
        first_path,
        [
            _native_row("session_meta", first_started, id=AGENT_A,
                        session_id=ROOT, parent_thread_id=ROOT,
                        agent_path="/root/first"),
            _native_row("event_msg", now - timedelta(minutes=2), type="task_started"),
        ],
        now.timestamp(),
    )
    _write_lines(
        second_path,
        [
            _native_row("session_meta", second_started, id=NESTED,
                        session_id=ROOT, parent_thread_id=ROOT,
                        agent_path="/root/second"),
            _native_row("event_msg", now - timedelta(minutes=1), type="task_started"),
        ],
        (now - timedelta(seconds=1)).timestamp(),
    )

    first_scan, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=boundary
    )
    first_agents = next(item for item in first_scan if item.session_id == ROOT).agents

    # Simulate activity advancing in the opposite order before a Viewer restart.
    first_mtime = (now - timedelta(seconds=1)).timestamp()
    second_mtime = now.timestamp()
    os.utime(first_path, (first_mtime, first_mtime))
    os.utime(second_path, (second_mtime, second_mtime))
    second_scan, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=boundary
    )
    second_agents = next(item for item in second_scan if item.session_id == ROOT).agents

    assert [agent.agent_id for agent in first_agents] == [AGENT_A, NESTED]
    assert [agent.agent_id for agent in second_agents] == [AGENT_A, NESTED]
    assert [agent.started_at for agent in second_agents] == [first_started, second_started]


def test_recent_session_index_keeps_old_root_metadata_beyond_candidate_cap(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    _write_lines(
        session_dir / f"rollout-old-{ROOT}.jsonl",
        [
            _native_row(
                "session_meta",
                now - timedelta(minutes=40),
                id=ROOT,
                session_id=ROOT,
                cwd=r"C:\Users\private\indexed-project",
            ),
            _native_row(
                "turn_context", now - timedelta(minutes=40), model="gpt-main"
            ),
        ],
        (now - timedelta(minutes=40)).timestamp(),
    )
    _write_lines(
        session_dir / f"rollout-initial-{AGENT_A}.jsonl",
        [
            _native_row(
                "session_meta",
                now - timedelta(minutes=40),
                id=AGENT_A,
                session_id=ROOT,
                parent_thread_id=ROOT,
                agent_path="/root/worker",
            ),
            _native_row(
                "event_msg", now - timedelta(minutes=39), type="task_complete"
            ),
        ],
        (now - timedelta(minutes=40)).timestamp(),
    )
    _write_lines(
        session_dir / f"rollout-followup-{FOLLOWUP_A}.jsonl",
        [
            _native_row(
                "session_meta",
                now - timedelta(minutes=2),
                id=FOLLOWUP_A,
                session_id=ROOT,
                parent_thread_id=AGENT_A,
            ),
            _native_row("event_msg", now - timedelta(minutes=1), type="task_started"),
        ],
        now.timestamp(),
    )
    for index in range(61):
        dummy = f"77777777-7777-4777-8777-{index:012d}"
        _write_lines(
            session_dir / f"rollout-dummy-{dummy}.jsonl",
            [
                _native_row(
                    "session_meta",
                    now - timedelta(seconds=30),
                    id=dummy,
                    session_id=dummy,
                ),
                _native_row(
                    "event_msg", now - timedelta(seconds=20), type="task_complete"
                ),
            ],
            (now - timedelta(seconds=20)).timestamp(),
        )
    _write_lines(
        tmp_path / "session_index.jsonl",
        [
            {
                "id": ROOT,
                "thread_name": "must-not-be-retained",
                "updated_at": now.isoformat(),
            },
            {
                "id": FOLLOWUP_A,
                "thread_name": "also-not-retained",
                "updated_at": now.isoformat(),
            },
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    snapshot = next(item for item in snapshots if item.session_id == ROOT)
    assert snapshot.project_name == "indexed-project"
    assert snapshot.model == "gpt-main"
    assert len(snapshot.agents) == 1
    assert snapshot.agents[0].agent_id == AGENT_A
    assert snapshot.agents[0].model is None
    assert "must-not-be-retained" not in repr(snapshot)
    assert "also-not-retained" not in repr(snapshot)


def test_rollout_local_date_ahead_of_utc_boundary_is_discovered(tmp_path: Path) -> None:
    boundary = datetime(2026, 8, 8, 16, 0, tzinfo=UTC)
    # In UTC+09 this record belongs to the 2026-08-09 Codex directory even
    # though its normalized event timestamp is still 2026-08-08 UTC.
    session_dir = tmp_path / "sessions" / "2026" / "08" / "09"
    _write_lines(
        session_dir / f"rollout-local-date-{ROOT}.jsonl",
        [
            _native_row(
                "session_meta",
                boundary - timedelta(minutes=2),
                id=ROOT,
                session_id=ROOT,
            ),
            _native_row(
                "event_msg", boundary - timedelta(minutes=1), type="task_started"
            ),
        ],
        boundary.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=boundary - timedelta(minutes=30), boundary=boundary
    )

    assert [snapshot.session_id for snapshot in snapshots] == [ROOT]


def test_large_rollout_is_tail_bounded_and_does_not_expose_body(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = tmp_path / "sessions" / f"{now:%Y/%m/%d}" / f"rollout-x-{ROOT}.jsonl"
    rows: list[dict[str, object] | str] = [
        _native_row("session_meta", now - timedelta(minutes=2), id=ROOT, session_id=ROOT,
                    cwd=r"C:\Users\private\safe-project"),
        json.dumps(
            {
                "type": "world_state",
                "payload": {"type": "full", "state": "x" * 100_000},
            }
        ),
        _native_row("turn_context", now - timedelta(minutes=2), model="gpt-main"),
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "message", "content": "secret" * 400_000},
            }
        ),
        _native_row("event_msg", now - timedelta(minutes=1), type="task_started"),
    ]
    _write_lines(path, rows, now.timestamp())

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    assert len(snapshots) == 1
    assert snapshots[0].project_name == "safe-project"
    assert snapshots[0].model == "gpt-main"
    assert "secret" not in repr(snapshots[0])
    assert not hasattr(snapshots[0], "cwd")


def test_omitted_middle_cannot_leave_false_pending_child_work(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    session_dir = tmp_path / "sessions" / f"{now:%Y/%m/%d}"
    _write_lines(
        session_dir / f"rollout-root-{ROOT}.jsonl",
        [
            _native_row("session_meta", now - timedelta(minutes=5), id=ROOT, session_id=ROOT),
            _native_row("event_msg", now - timedelta(minutes=4), type="task_started"),
        ],
        now.timestamp(),
    )
    child_path = session_dir / f"rollout-child-{AGENT_A}.jsonl"
    _write_lines(
        child_path,
        [
            _native_row(
                "session_meta",
                now - timedelta(minutes=4),
                id=AGENT_A,
                session_id=ROOT,
                parent_thread_id=ROOT,
                agent_path="/root/worker",
            ),
            _native_row("event_msg", now - timedelta(minutes=3), type="task_started"),
            _native_row(
                "response_item",
                now - timedelta(minutes=3),
                type="function_call",
                call_id="wait-1",
                name="wait_agent",
            ),
            _native_row(
                "response_item",
                now - timedelta(minutes=2),
                type="function_call_output",
                call_id="wait-1",
            ),
            _native_row("event_msg", now - timedelta(minutes=2), type="task_complete"),
            "x" * (2 * 1024 * 1024 + 100_000),
            _native_row("event_msg", now - timedelta(minutes=1), type="token_count"),
        ],
        now.timestamp(),
    )

    snapshots, _ = _scan_snapshots(
        tmp_path, cutoff=now - timedelta(minutes=30), boundary=now
    )

    snapshot = next(item for item in snapshots if item.session_id == ROOT)
    assert snapshot.agents == []


def _snapshot(*, boss_state: str = "idle", agent_tool: str | None = None) -> RestoredSession:
    now = datetime.now(UTC)
    return RestoredSession(
        session_id=ROOT,
        project_name="project",
        model="gpt-main",
        boss_state=boss_state,
        last_tool_name=None,
        agents=[
            RestoredAgent(
                AGENT_A,
                state="waiting" if agent_tool == "AgentWait" else "working",
                model="gpt-child",
                last_tool_name=agent_tool,
            )
        ],
        last_activity=now,
        captured_at=now,
    )


@pytest.mark.asyncio
async def test_live_stop_tombstone_prevents_agent_resurrection() -> None:
    ep = EventProcessor()
    boundary = ep.begin_codex_restore()
    stop = AgentEvent(
        event_type=EventType.SUBAGENT_STOP,
        session_id=ROOT,
        data=AgentEventData(source="codex", agent_id=AGENT_A),
    )
    with patch.object(ep, "_persist_event", new=AsyncMock()):
        await ep.process_event(stop)
    assert await ep.merge_codex_restored_session(_snapshot(), start_sequence=boundary)
    assert AGENT_A not in ep.sessions[ROOT].agents


@pytest.mark.asyncio
async def test_live_agentwait_post_wins_over_waiting_snapshot() -> None:
    ep = EventProcessor()
    boundary = ep.begin_codex_restore()
    post = ToolEvent(
        event_type=EventType.POST_TOOL_USE,
        session_id=ROOT,
        data=ToolEventData(
            source="codex", agent_id=AGENT_A, tool_name="AgentWait", tool_use_id="wait-1"
        ),
    )
    with patch.object(ep, "_persist_event", new=AsyncMock()):
        await ep.process_event(post)
    assert await ep.merge_codex_restored_session(
        _snapshot(agent_tool="AgentWait"), start_sequence=boundary
    )
    assert ep.sessions[ROOT].agents[AGENT_A].state == AgentState.WORKING


@pytest.mark.asyncio
async def test_manual_rescan_preserves_history_tasks_and_agent_identity() -> None:
    ep = EventProcessor()
    snapshot = _snapshot(boss_state="working", agent_tool="AgentWait")
    snapshot.last_tool_name = "Bash"
    assert await ep.merge_codex_restored_session(snapshot, start_sequence=0)
    sm = ep.sessions[ROOT]
    sm.todos = [TodoItem(content="keep", status=TodoStatus.PENDING)]
    first_agent = sm.agents[AGENT_A]
    history_count = len(sm.history)
    sm.boss_state = BossState.IDLE
    first_agent.state = AgentState.WORKING

    assert await ep.merge_codex_restored_session(snapshot, start_sequence=0)

    assert sm.todos[0].content == "keep"
    assert len(sm.history) == history_count
    assert sm.agents[AGENT_A] is first_agent
    assert sm.boss_state == BossState.IDLE
    assert first_agent.state == AgentState.WORKING


@pytest.mark.asyncio
async def test_delayed_live_session_start_and_subagent_start_are_idempotent() -> None:
    ep = EventProcessor()
    snapshot = _snapshot()
    assert await ep.merge_codex_restored_session(snapshot, start_sequence=0)
    sm = ep.sessions[ROOT]
    first_agent = sm.agents[AGENT_A]
    first_number = first_agent.number

    sm.transition(
        SessionEvent(
            event_type=EventType.SESSION_START,
            session_id=ROOT,
            data=SessionEventData(source="codex", model="gpt-main"),
        )
    )
    sm.transition(
        AgentEvent(
            event_type=EventType.SUBAGENT_START,
            session_id=ROOT,
            data=AgentEventData(source="codex", agent_id=AGENT_A, model="gpt-child"),
        )
    )

    assert sm.agents[AGENT_A] is first_agent
    assert sm.agents[AGENT_A].number == first_number
    assert sm.restored_session_start_pending is False


@pytest.mark.asyncio
async def test_live_session_end_and_completed_db_record_block_restore() -> None:
    ep = EventProcessor()
    end = SessionEvent(
        event_type=EventType.SESSION_END,
        session_id=ROOT,
        data=SessionEventData(source="codex"),
    )
    with patch.object(ep, "_persist_event", new=AsyncMock()):
        await ep.process_event(end)
    assert not await ep.merge_codex_restored_session(_snapshot(), start_sequence=0)

    other = "66666666-6666-4666-8666-666666666666"
    async with AsyncSessionLocal() as db:
        db.add(SessionRecord(id=other, status="completed"))
        await db.commit()
    completed = _snapshot()
    completed.session_id = other
    assert not await ep.merge_codex_restored_session(completed, start_sequence=0)


def test_disabled_status_and_manual_start_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restorer = CodexSessionRestorer()
    monkeypatch.setattr(
        "app.core.codex_session_restorer.load_settings",
        lambda: ({"restore_codex_sessions": False}, None),
    )
    status = restorer.status()
    assert status["state"] == "disabled"
    assert status["session_count"] == 0
    assert status["agent_count"] == 0


def test_restored_session_marker_has_an_explicit_event_log_summary() -> None:
    event = SessionEvent(
        event_type=EventType.SESSION_START,
        session_id=ROOT,
        data=SessionEventData(source="codex", restored=True),
    )

    assert EventProcessor().get_event_summary(event) == "Codexセッションを復元しました"


@pytest.mark.asyncio
async def test_background_start_coalesces_and_cancel_awaits_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restorer = CodexSessionRestorer()
    processor = EventProcessor()
    entered = asyncio.Event()

    async def wait_forever(
        _processor: EventProcessor,
    ) -> dict[str, str | int | None]:
        entered.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(restorer, "restore", wait_forever)
    first = restorer.start(processor)
    first_task = restorer._task
    second = restorer.start(processor)

    assert first["state"] == "checking"
    assert second["state"] == "checking"
    assert restorer._task is first_task
    await entered.wait()
    await restorer.cancel()
    assert first_task is not None
    assert first_task.cancelled()


def test_restore_http_status_and_nonblocking_post_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRestorer:
        def status(self) -> dict[str, str | int | None]:
            return {
                "state": "disabled",
                "status": "disabled",
                "session_count": 0,
                "agent_count": 0,
            }

        def start(self, _event_processor: EventProcessor) -> dict[str, str | int | None]:
            return {
                "state": "checking",
                "status": "checking",
                "session_count": 0,
                "agent_count": 0,
            }

    fake = FakeRestorer()
    monkeypatch.setattr(codex_restore_route, "get_codex_session_restorer", lambda: fake)
    client = TestClient(app)

    status = client.get("/api/v1/codex/restore/status")
    trigger = client.post("/api/v1/codex/restore")
    allowed = client.post(
        "/api/v1/codex/restore", headers={"Origin": "http://localhost:3000"}
    )
    rejected = client.post(
        "/api/v1/codex/restore", headers={"Origin": "https://attacker.example"}
    )

    assert status.status_code == 200
    assert status.json()["state"] == "disabled"
    assert trigger.status_code == 200
    assert trigger.json()["state"] == "checking"
    assert allowed.status_code == 200
    assert rejected.status_code == 403
