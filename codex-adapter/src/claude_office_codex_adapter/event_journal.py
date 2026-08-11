"""Append sanitized Codex hook events to a small local recovery journal."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

_JOURNAL_DIRNAME = "claude-office-events"
_EVENT_TYPES = frozenset(
    {
        "session_start",
        "session_end",
        "user_prompt_submit",
        "pre_tool_use",
        "post_tool_use",
        "subagent_start",
        "subagent_stop",
        "stop",
    }
)
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_METADATA_RE = re.compile(r"[A-Za-z0-9._:/+\-]{1,128}\Z")
_PROJECT_RE = re.compile(r"[A-Za-z0-9._\-]{1,128}\Z")
_RETENTION_DAYS = 3
_MAX_DAILY_BYTES = 16 * 1024 * 1024


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured) if configured else Path.home() / ".codex"


def _journal_event(event: dict[str, object]) -> dict[str, object] | None:
    """Return a metadata-only copy suitable for persistence."""
    event_type = event.get("event_type")
    session_id = event.get("session_id")
    timestamp = event.get("timestamp")
    raw_data = event.get("data")
    if event_type not in _EVENT_TYPES:
        return None
    if not isinstance(session_id, str) or _ID_RE.fullmatch(session_id) is None:
        return None
    if not isinstance(timestamp, str) or not 1 <= len(timestamp) <= 64:
        return None
    if not isinstance(raw_data, dict):
        return None
    typed_data = cast(dict[str, object], raw_data)
    if typed_data.get("source") != "codex":
        return None
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    data: dict[str, str] = {"source": "codex"}
    project_name = typed_data.get("project_name")
    if isinstance(project_name, str) and _PROJECT_RE.fullmatch(project_name):
        data["project_name"] = project_name
    for key in ("model", "tool_name", "tool_use_id", "agent_id", "agent_type"):
        value = typed_data.get(key)
        if isinstance(value, str) and _METADATA_RE.fullmatch(value):
            data[key] = value
    return {
        "event_type": event_type,
        "session_id": session_id,
        "timestamp": timestamp,
        "data": data,
    }


def _update_terminal_marker(directory: Path, event: dict[str, object]) -> None:
    """Keep SessionEnd durable even when it ages out of the journal tail."""
    event_type = str(event["event_type"])
    if event_type != "session_end":
        return
    session_id = str(event["session_id"])
    terminal_dir = directory / "terminal"
    marker = terminal_dir / f"{session_id}.json"
    terminal_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="terminal-", suffix=".json", dir=terminal_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "session_id": session_id,
                    "event_type": "session_end",
                    "timestamp": event["timestamp"],
                },
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        os.replace(temporary, marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _prune_old_journals(directory: Path, current: datetime) -> None:
    cutoff = current.date().toordinal() - _RETENTION_DAYS
    for path in directory.glob("????-??-??.jsonl"):
        try:
            journal_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            if journal_date.toordinal() < cutoff:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
    terminal_dir = directory / "terminal"
    for marker in terminal_dir.glob("*.json"):
        try:
            modified = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC).date()
            if modified.toordinal() < cutoff:
                marker.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue


def append_event(event: dict[str, object], *, now: datetime | None = None) -> bool:
    """Append one sanitized event, suppressing all filesystem failures.

    A single ``os.write`` against an ``O_APPEND`` descriptor keeps each small
    record contiguous when several hook processes finish at nearly the same
    time. The hook remains fail-open if the directory is unavailable.
    """
    safe_event = _journal_event(event)
    if safe_event is None:
        return False
    try:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        directory = _codex_home() / _JOURNAL_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        _update_terminal_marker(directory, safe_event)
        _prune_old_journals(directory, current)
        path = directory / f"{current:%Y-%m-%d}.jsonl"
        payload = (
            json.dumps(safe_event, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.fstat(descriptor).st_size + len(payload) > _MAX_DAILY_BYTES:
                return safe_event["event_type"] == "session_end"
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        return True
    except (OSError, TypeError, ValueError):
        return False
