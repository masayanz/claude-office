import json
from datetime import UTC, datetime

from claude_office_codex_adapter import event_journal


def test_append_event_persists_metadata_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    event = {
        "event_type": "pre_tool_use",
        "session_id": "session-1",
        "timestamp": "2026-08-09T01:02:03+00:00",
        "data": {
            "source": "codex",
            "project_name": "safe-project",
            "model": "gpt-5.6-sol",
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "agent_id": "agent-1",
            "agent_type": "default",
            "working_dir": "C:/Users/private/secret",
            "tool_input": {"command": "secret command"},
            "message": "secret response",
        },
    }

    assert event_journal.append_event(
        event, now=datetime(2026, 8, 9, tzinfo=UTC)
    )

    path = tmp_path / "claude-office-events" / "2026-08-09.jsonl"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["data"] == {
        "source": "codex",
        "project_name": "safe-project",
        "model": "gpt-5.6-sol",
        "tool_name": "Bash",
        "tool_use_id": "tool-1",
        "agent_id": "agent-1",
        "agent_type": "default",
    }
    serialized = path.read_text(encoding="utf-8")
    assert "secret" not in serialized
    assert "working_dir" not in serialized


def test_append_event_is_fail_open(monkeypatch) -> None:
    monkeypatch.setattr(event_journal, "_codex_home", lambda: None)
    assert not event_journal.append_event(
        {
            "event_type": "stop",
            "session_id": "session-1",
            "timestamp": "2026-08-09T01:02:03+00:00",
            "data": {"source": "codex"},
        }
    )


def test_append_event_rejects_unsanitized_envelope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert not event_journal.append_event(
        {
            "event_type": "pre_tool_use",
            "session_id": "bad session",
            "timestamp": "not-a-time",
            "data": {"source": "other", "tool_name": "secret tool"},
        }
    )
    assert not (tmp_path / "claude-office-events").exists()


def test_session_end_marker_is_atomic_metadata_and_late_start_does_not_erase_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    ended = {
        "event_type": "session_end",
        "session_id": "session-1",
        "timestamp": "2026-08-09T01:02:03+00:00",
        "data": {"source": "codex", "message": "secret response"},
    }
    assert event_journal.append_event(
        ended, now=datetime(2026, 8, 9, tzinfo=UTC)
    )
    # Hook processes may finish out of order. A delayed SessionStart must not
    # delete a terminal marker already written by SessionEnd.
    assert event_journal.append_event(
        {
            "event_type": "session_start",
            "session_id": "session-1",
            "timestamp": "2026-08-09T01:01:00+00:00",
            "data": {"source": "codex"},
        },
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    marker = tmp_path / "claude-office-events" / "terminal" / "session-1.json"
    saved = json.loads(marker.read_text(encoding="utf-8"))
    assert saved == {
        "session_id": "session-1",
        "event_type": "session_end",
        "timestamp": "2026-08-09T01:02:03+00:00",
    }
    assert "secret" not in marker.read_text(encoding="utf-8")


def test_append_event_prunes_old_daily_journals(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    directory = tmp_path / "claude-office-events"
    directory.mkdir()
    old = directory / "2026-08-05.jsonl"
    retained = directory / "2026-08-06.jsonl"
    old.write_text("old", encoding="utf-8")
    retained.write_text("retained", encoding="utf-8")

    assert event_journal.append_event(
        {
            "event_type": "stop",
            "session_id": "session-1",
            "timestamp": "2026-08-09T01:02:03+00:00",
            "data": {"source": "codex"},
        },
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert not old.exists()
    assert retained.exists()


def test_daily_journal_has_a_hard_size_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(event_journal, "_MAX_DAILY_BYTES", 1)

    assert not event_journal.append_event(
        {
            "event_type": "stop",
            "session_id": "session-1",
            "timestamp": "2026-08-09T01:02:03+00:00",
            "data": {"source": "codex"},
        },
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    path = tmp_path / "claude-office-events" / "2026-08-09.jsonl"
    assert path.stat().st_size == 0
