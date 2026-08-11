"""Reconstruct current Codex session state from metadata-only local records.

The restorer deliberately does not replay conversation history. It reads a
small head for ``session_meta`` and a bounded tail for lifecycle/tool metadata,
then produces one current-state snapshot per active root session.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import json
import logging
import ntpath
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from app.services.app_settings import load_settings

if TYPE_CHECKING:
    from app.core.event_processor import EventProcessor

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_METADATA_RE = re.compile(r"[A-Za-z0-9._:/+\-]{1,128}\Z")
_ROLLOUT_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})\Z")
_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 2 * 1024 * 1024
_JOURNAL_TAIL_BYTES = 4 * 1024 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_MAX_ACTIVE_SESSIONS = 10
_MAX_CANDIDATE_ROLLOUTS = 60
_MAX_EXPLORED_ROLLOUTS = 5000
_MAX_INDEX_THREADS = 20
_MAX_PARENT_THREADS = 20
_INDEX_TAIL_BYTES = 4 * 1024 * 1024
_PROBE_TAIL_BYTES = 64 * 1024

_TOOL_NAMES = {
    "collaborationspawn_agent": "Agent",
    "collaborationwait_agent": "AgentWait",
    "spawn_agent": "Agent",
    "wait_agent": "AgentWait",
}
_JOURNAL_EVENT_TYPES = {
    "session_start",
    "session_end",
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "subagent_start",
    "subagent_stop",
    "stop",
}


def _empty_str_set() -> set[str]:
    return set()


def _empty_pending() -> dict[str, tuple[str, datetime]]:
    return {}


def _empty_agents() -> dict[str, RestoredAgent]:
    return {}


def _empty_agent_pending() -> dict[str, dict[str, tuple[str, datetime]]]:
    return {}


def _empty_completed() -> dict[str, datetime]:
    return {}


def _empty_agent_completed() -> dict[str, dict[str, datetime]]:
    return {}


def _empty_stopped_agents() -> dict[str, datetime]:
    return {}


@dataclass(slots=True)
class RestoredAgent:
    agent_id: str
    state: str = "working"
    agent_type: str | None = None
    model: str | None = None
    last_tool_name: str | None = None
    started_at: datetime | None = None


@dataclass(slots=True)
class RestoredSession:
    session_id: str
    project_name: str | None
    model: str | None
    boss_state: str
    last_tool_name: str | None
    agents: list[RestoredAgent]
    last_activity: datetime
    captured_at: datetime
    event_fingerprints: set[str] = field(default_factory=_empty_str_set)


@dataclass(slots=True)
class RestoreResult:
    status: str = "idle"
    restored_sessions: int = 0
    restored_agents: int = 0
    scanned_files: int = 0
    last_run: str | None = None
    message: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "state": self.status,
            "status": self.status,
            "session_count": self.restored_sessions,
            "agent_count": self.restored_agents,
            "restored_sessions": self.restored_sessions,
            "restored_agents": self.restored_agents,
            "scanned_files": self.scanned_files,
            "last_run": self.last_run,
            "message": self.message,
            "error": self.error,
        }


@dataclass(slots=True)
class _NativeThread:
    thread_id: str
    session_id: str
    parent_thread_id: str | None
    agent_path: str | None
    created_at: datetime | None
    project_name: str | None
    model: str | None
    agent_type: str | None
    last_activity: datetime
    active_turn: bool
    turn_state_at: datetime | None
    pending_tools: dict[str, tuple[str, datetime]]


@dataclass(slots=True)
class _SessionAccumulator:
    session_id: str
    project_name: str | None = None
    model: str | None = None
    last_activity: datetime | None = None
    ended: bool = False
    last_session_start: datetime | None = None
    main_turn_active: bool = False
    main_turn_state_at: datetime | None = None
    main_stop_at: datetime | None = None
    main_pending: dict[str, tuple[str, datetime]] = field(default_factory=_empty_pending)
    main_completed: dict[str, datetime] = field(default_factory=_empty_completed)
    agents: dict[str, RestoredAgent] = field(default_factory=_empty_agents)
    agent_pending: dict[str, dict[str, tuple[str, datetime]]] = field(
        default_factory=_empty_agent_pending
    )
    agent_completed: dict[str, dict[str, datetime]] = field(
        default_factory=_empty_agent_completed
    )
    stopped_agents: dict[str, datetime] = field(default_factory=_empty_stopped_agents)
    fingerprints: set[str] = field(default_factory=_empty_str_set)


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _safe_metadata(value: object) -> str | None:
    return value if isinstance(value, str) and _METADATA_RE.fullmatch(value) else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _project_name(cwd: object) -> str | None:
    if not isinstance(cwd, str) or not cwd:
        return None
    trimmed = cwd.rstrip("/\\")
    basename = ntpath.basename(trimmed) or Path(trimmed).name
    if not basename or len(basename) > 128 or any(ord(char) < 32 for char in basename):
        return None
    return basename


def event_fingerprint(
    event_type: object,
    session_id: object,
    timestamp: object,
    data: object,
) -> str:
    """Build a content-free identity for deduplicating journal/live events."""
    payload: dict[str, Any] = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    values = (
        event_type,
        session_id,
        timestamp,
        payload.get("agent_id"),
        payload.get("tool_use_id"),
        payload.get("tool_name"),
    )
    return "\x1f".join(str(value or "") for value in values)


def _bounded_segments(path: Path, *, head_bytes: int, tail_bytes: int) -> list[list[bytes]]:
    """Read bounded ordered segments, preserving whether the middle was omitted."""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            head = stream.read(min(size, head_bytes))
            tail_start = max(0, size - tail_bytes)
            stream.seek(tail_start)
            tail = stream.read(tail_bytes)
    except OSError:
        return []

    if tail_start <= len(head):
        overlap = max(0, len(head) - tail_start)
        chunks = [head + tail[overlap:]]
    else:
        chunks = [head, tail]
    segments: list[list[bytes]] = []
    for index, chunk in enumerate(chunks):
        lines = chunk.splitlines()
        if len(chunks) > 1 and index == 1 and lines:
            lines = lines[1:]
        clean: list[bytes] = []
        for line in lines:
            if line and len(line) <= _MAX_LINE_BYTES:
                clean.append(line)
        if clean:
            segments.append(clean)
    return segments


def _bounded_lines(path: Path, *, head_bytes: int, tail_bytes: int) -> list[bytes]:
    """Flatten bounded segments for journal inputs that need only a tail."""
    segments = _bounded_segments(path, head_bytes=head_bytes, tail_bytes=tail_bytes)
    result: list[bytes] = []
    for segment in segments:
        result.extend(segment)
    return result


def _json_object(line: bytes) -> dict[str, Any] | None:
    try:
        value: Any = json.loads(line)
        return cast(dict[str, Any], value) if isinstance(value, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _normalize_tool(value: object) -> str | None:
    tool = _safe_metadata(value)
    return _TOOL_NAMES.get(tool, tool) if tool else None


def _parse_native_rollout(path: Path, boundary: datetime) -> _NativeThread | None:
    segments = _bounded_segments(path, head_bytes=_HEAD_BYTES, tail_bytes=_TAIL_BYTES)
    if not segments:
        return None
    match = _ROLLOUT_RE.search(path.stem)
    filename_id = match.group(1) if match else None
    meta: dict[str, Any] | None = None
    meta_timestamp: datetime | None = None
    model: str | None = None
    last_started: datetime | None = None
    last_complete: datetime | None = None
    last_record_timestamp: datetime | None = None
    pending: dict[str, tuple[str, datetime]] = {}

    def track_timestamp(timestamp: datetime | None) -> None:
        nonlocal last_record_timestamp
        if timestamp is not None and timestamp <= boundary:
            last_record_timestamp = max(last_record_timestamp or timestamp, timestamp)

    for segment_index, lines in enumerate(segments):
        if segment_index > 0:
            # A completion may exist in the omitted middle. Never carry a
            # head call/task forward into the tail as falsely active work.
            pending.clear()
            last_started = None
            last_complete = None
        for line in lines:
            record = _json_object(line)
            if record is None:
                continue
            top_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            payload = cast(dict[str, Any], payload)
            timestamp = _parse_timestamp(record.get("timestamp"))

            if top_type == "session_meta" and meta is None:
                track_timestamp(timestamp)
                candidate_id = _safe_id(payload.get("id"))
                if filename_id is None or candidate_id == filename_id:
                    # Retain only lifecycle/identity metadata. In particular,
                    # reduce cwd to a basename immediately and never keep the
                    # full path in the reconstructed thread object.
                    meta = {
                        "id": candidate_id,
                        "session_id": _safe_id(payload.get("session_id")),
                        "parent_thread_id": _safe_id(payload.get("parent_thread_id")),
                        "agent_path": _safe_metadata(payload.get("agent_path")),
                        "project_name": _project_name(payload.get("cwd")),
                        "agent_role": _safe_metadata(payload.get("agent_role")),
                    }
                    if timestamp is not None and timestamp <= boundary:
                        meta_timestamp = timestamp
                continue
            if top_type == "turn_context":
                track_timestamp(timestamp)
                candidate_model = _safe_metadata(payload.get("model"))
                if candidate_model and (timestamp is None or timestamp <= boundary):
                    model = candidate_model
                continue
            if top_type == "event_msg":
                payload_type = payload.get("type")
                if payload_type == "task_started" and timestamp and timestamp <= boundary:
                    track_timestamp(timestamp)
                    last_started = timestamp
                elif payload_type == "task_complete" and timestamp and timestamp <= boundary:
                    track_timestamp(timestamp)
                    last_complete = timestamp
                elif payload_type == "sub_agent_activity":
                    track_timestamp(timestamp)
                    _safe_id(payload.get("agent_thread_id"))
                continue
            if top_type != "response_item":
                continue
            payload_type = payload.get("type")
            if payload_type in {"custom_tool_call", "function_call"}:
                track_timestamp(timestamp)
                call_id = _safe_id(payload.get("call_id")) or _safe_id(payload.get("id"))
                tool_name = _normalize_tool(payload.get("name"))
                if call_id and tool_name and timestamp and timestamp <= boundary:
                    pending[call_id] = (tool_name, timestamp)
            elif payload_type in {"custom_tool_call_output", "function_call_output"}:
                track_timestamp(timestamp)
                call_id = _safe_id(payload.get("call_id"))
                if call_id and timestamp and timestamp <= boundary:
                    pending.pop(call_id, None)

    if meta is None:
        return None
    thread_id = _safe_id(meta.get("id"))
    session_id = _safe_id(meta.get("session_id")) or thread_id
    if thread_id is None or session_id is None:
        return None
    parent_id = _safe_id(meta.get("parent_thread_id"))
    active_turn = last_started is not None and (
        last_complete is None or last_started > last_complete
    )
    turn_state_at = max(
        (value for value in (last_started, last_complete) if value is not None),
        default=None,
    )
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    activity = max(modified, last_record_timestamp or modified)
    return _NativeThread(
        thread_id=thread_id,
        session_id=session_id,
        parent_thread_id=parent_id,
        agent_path=_safe_metadata(meta.get("agent_path")),
        created_at=meta_timestamp,
        project_name=(
            meta.get("project_name")
            if isinstance(meta.get("project_name"), str)
            else None
        ),
        model=model,
        agent_type=_safe_metadata(meta.get("agent_role")),
        last_activity=activity,
        active_turn=active_turn,
        turn_state_at=turn_state_at,
        pending_tools=dict(pending),
    )


def _recent_index_threads(
    codex_home: Path, cutoff: datetime, boundary: datetime
) -> list[tuple[str, datetime]]:
    """Read only the bounded index tail and retain recent thread identities."""
    latest: dict[str, datetime] = {}
    index_path = codex_home / "session_index.jsonl"
    for line in _bounded_lines(index_path, head_bytes=0, tail_bytes=_INDEX_TAIL_BYTES):
        value = _json_object(line)
        if value is None:
            continue
        thread_id = _safe_id(value.get("id"))
        updated_at = _parse_timestamp(value.get("updated_at"))
        if (
            thread_id is None
            or updated_at is None
            or updated_at < cutoff
            or updated_at > boundary
        ):
            continue
        latest[thread_id] = max(latest.get(thread_id, updated_at), updated_at)
    return heapq.nlargest(
        _MAX_INDEX_THREADS, latest.items(), key=lambda item: item[1]
    )


def _recent_session_days(
    session_root: Path, cutoff: datetime, boundary: datetime
) -> list[Path]:
    """Return a small day window tolerant of Codex's local-date directories."""
    start = (cutoff - timedelta(days=1)).date()
    # Rollout directories use local calendar dates while persisted timestamps
    # are UTC-aware. The symmetric one-day cushion covers every timezone date
    # boundary without retaining an unbounded directory walk.
    end = (boundary + timedelta(days=1)).date()
    days: list[Path] = []
    current = start
    while current <= end:
        days.append(
            session_root
            / f"{current.year:04d}"
            / f"{current.month:02d}"
            / f"{current.day:02d}"
        )
        current += timedelta(days=1)
    return days


def _rollout_activity_probe(path: Path, boundary: datetime) -> datetime:
    """Estimate activity from mtime and metadata timestamps in a tiny tail."""
    try:
        activity = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return datetime.min.replace(tzinfo=UTC)
    for line in _bounded_lines(path, head_bytes=0, tail_bytes=_PROBE_TAIL_BYTES):
        value = _json_object(line)
        if value is None:
            continue
        timestamp = _parse_timestamp(value.get("timestamp"))
        if timestamp is not None and timestamp <= boundary:
            activity = max(activity, timestamp)
    return activity


def _find_rollout_by_id(day_dirs: list[Path], thread_id: str) -> Path | None:
    """Locate an indexed thread without parsing unrelated rollout payloads."""
    for directory in reversed(day_dirs):
        try:
            match = next(directory.glob(f"rollout-*{thread_id}.jsonl"), None)
        except OSError:
            continue
        if match is not None:
            return match
    return None


def _thread_day_dirs(
    session_root: Path, thread_id: str, fallback: list[Path]
) -> list[Path]:
    """Resolve a UUIDv7 thread's creation day without scanning the tree."""
    try:
        parsed = uuid.UUID(thread_id)
    except (ValueError, AttributeError):
        return fallback
    if parsed.version != 7:
        return fallback
    milliseconds = parsed.int >> 80
    try:
        created = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return fallback
    return [
        session_root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        for day in (created - timedelta(days=1), created, created + timedelta(days=1))
    ]


def _native_candidates(
    codex_home: Path, cutoff: datetime, boundary: datetime
) -> list[Path]:
    """Select bounded candidates while preserving Windows open-file activity.

    Codex rollout mtimes may remain at creation time on Windows. Recent index
    identities are therefore admitted first, then a capped number of files in
    the relevant UTC day directories are ranked by a 64 KiB tail timestamp
    probe. Both retained candidates and explored filenames have hard limits.
    """
    session_root = codex_home / "sessions"
    day_dirs = _recent_session_days(session_root, cutoff, boundary)
    selected: list[Path] = []
    selected_set: set[Path] = set()
    for thread_id, _updated_at in _recent_index_threads(codex_home, cutoff, boundary):
        lookup_dirs = _thread_day_dirs(session_root, thread_id, day_dirs)
        path = _find_rollout_by_id(lookup_dirs, thread_id)
        if path is not None and path not in selected_set:
            selected.append(path)
            selected_set.add(path)

    iterators = (directory.glob("rollout-*.jsonl") for directory in reversed(day_dirs))
    explored = itertools.islice(
        itertools.chain.from_iterable(iterators), _MAX_EXPLORED_ROLLOUTS
    )
    ranked = heapq.nlargest(
        max(0, _MAX_CANDIDATE_ROLLOUTS - len(selected)),
        (
            (path, _rollout_activity_probe(path, boundary))
            for path in explored
            if path not in selected_set
        ),
        key=lambda item: item[1],
    )
    selected.extend(path for path, _activity in ranked)
    return selected


def _apply_journal_event(acc: _SessionAccumulator, event: dict[str, Any]) -> None:
    event_type = event.get("event_type")
    timestamp = _parse_timestamp(event.get("timestamp"))
    data = event.get("data")
    if not isinstance(event_type, str) or timestamp is None or not isinstance(data, dict):
        return
    data = cast(dict[str, Any], data)
    acc.last_activity = max(acc.last_activity or timestamp, timestamp)
    acc.fingerprints.add(
        event_fingerprint(event_type, acc.session_id, event.get("timestamp"), data)
    )
    project_name = data.get("project_name")
    if isinstance(project_name, str) and project_name and len(project_name) <= 128:
        acc.project_name = project_name
    agent_id = _safe_id(data.get("agent_id"))
    model = _safe_metadata(data.get("model"))
    if model and not agent_id:
        acc.model = model

    if event_type == "session_start":
        acc.ended = False
        acc.last_session_start = timestamp
        acc.main_turn_active = False
        acc.main_turn_state_at = timestamp
    elif event_type == "session_end":
        acc.ended = True
        acc.main_turn_active = False
        acc.main_turn_state_at = timestamp
        acc.main_stop_at = timestamp
        acc.main_pending.clear()
        acc.agents.clear()
        acc.agent_pending.clear()
    elif event_type == "subagent_start" and agent_id:
        acc.stopped_agents.pop(agent_id, None)
        acc.agents[agent_id] = RestoredAgent(
            agent_id=agent_id,
            agent_type=_safe_metadata(data.get("agent_type")),
            model=model,
            started_at=timestamp,
        )
    elif event_type == "subagent_stop" and agent_id:
        acc.agents.pop(agent_id, None)
        acc.agent_pending.pop(agent_id, None)
        acc.stopped_agents[agent_id] = timestamp
    elif event_type == "pre_tool_use":
        tool = _normalize_tool(data.get("tool_name"))
        if not tool:
            return
        tool_id = _safe_id(data.get("tool_use_id")) or f"fallback:{agent_id or 'main'}:{tool}"
        target = acc.main_pending if not agent_id else acc.agent_pending.setdefault(agent_id, {})
        completed = (
            acc.main_completed
            if not agent_id
            else acc.agent_completed.setdefault(agent_id, {})
        )
        completed.pop(tool_id, None)
        target[tool_id] = (tool, timestamp)
        if not agent_id:
            acc.main_turn_active = True
            acc.main_turn_state_at = timestamp
        if agent_id and agent_id in acc.agents and model:
            acc.agents[agent_id].model = model
    elif event_type == "post_tool_use":
        tool = _normalize_tool(data.get("tool_name"))
        tool_id = _safe_id(data.get("tool_use_id"))
        target = acc.main_pending if not agent_id else acc.agent_pending.setdefault(agent_id, {})
        completed = (
            acc.main_completed
            if not agent_id
            else acc.agent_completed.setdefault(agent_id, {})
        )
        if tool_id:
            target.pop(tool_id, None)
            completed[tool_id] = timestamp
        elif tool:
            matching = [key for key, value in target.items() if value[0] == tool]
            if matching:
                matched = matching[-1]
                target.pop(matched, None)
                completed[matched] = timestamp
    elif event_type == "stop":
        acc.main_pending.clear()
        acc.main_turn_active = False
        acc.main_turn_state_at = timestamp
        acc.main_stop_at = timestamp
    elif event_type == "user_prompt_submit":
        acc.main_turn_active = True
        acc.main_turn_state_at = timestamp


def _read_journal(codex_home: Path, cutoff: datetime, boundary: datetime) -> tuple[
    dict[str, _SessionAccumulator], int
]:
    accumulators: dict[str, _SessionAccumulator] = {}
    scanned = 0
    journal_dir = codex_home / "claude-office-events"
    # Adapter journal names are UTC dates. Construct exactly three known paths
    # rather than materializing an unbounded directory glob.
    files = [
        journal_dir / f"{boundary - timedelta(days=offset):%Y-%m-%d}.jsonl"
        for offset in (2, 1, 0)
    ]
    ordered_events: list[tuple[datetime, int, dict[str, Any]]] = []
    sequence = 0
    for path in files:
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < cutoff - timedelta(days=1):
                continue
        except OSError:
            continue
        scanned += 1
        for line in _bounded_lines(path, head_bytes=0, tail_bytes=_JOURNAL_TAIL_BYTES):
            event = _json_object(line)
            if event is None:
                continue
            session_id = _safe_id(event.get("session_id"))
            timestamp = _parse_timestamp(event.get("timestamp"))
            event_type = event.get("event_type")
            raw_data = event.get("data")
            if (
                session_id is None
                or timestamp is None
                or timestamp > boundary
                or event_type not in _JOURNAL_EVENT_TYPES
                or not isinstance(raw_data, dict)
            ):
                continue
            typed_data = cast(dict[str, Any], raw_data)
            if typed_data.get("source") != "codex":
                continue
            safe_data: dict[str, str] = {"source": "codex"}
            project_name = _project_name(typed_data.get("project_name"))
            if project_name:
                safe_data["project_name"] = project_name
            for key in ("model", "tool_name", "agent_type"):
                value = _safe_metadata(typed_data.get(key))
                if value:
                    safe_data[key] = value
            for key in ("agent_id", "tool_use_id"):
                value = _safe_id(typed_data.get(key))
                if value:
                    safe_data[key] = value
            ordered_events.append(
                (
                    timestamp,
                    sequence,
                    {
                        "event_type": event_type,
                        "session_id": session_id,
                        "timestamp": event.get("timestamp"),
                        "data": safe_data,
                    },
                )
            )
            sequence += 1
    for _timestamp, _sequence, event in sorted(
        ordered_events, key=lambda item: (item[0], item[1])
    ):
        session_id = cast(str, event["session_id"])
        acc = accumulators.setdefault(session_id, _SessionAccumulator(session_id))
        _apply_journal_event(acc, event)
    return accumulators, scanned


def _apply_terminal_markers(
    codex_home: Path,
    accumulators: dict[str, _SessionAccumulator],
    boundary: datetime,
) -> int:
    scanned = 0
    terminal_dir = codex_home / "claude-office-events" / "terminal"
    for session_id, acc in accumulators.items():
        marker = terminal_dir / f"{session_id}.json"
        try:
            if marker.stat().st_size > 4096:
                continue
            value: Any = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        scanned += 1
        if not isinstance(value, dict):
            continue
        payload = cast(dict[str, Any], value)
        ended_at = _parse_timestamp(payload.get("timestamp"))
        if (
            payload.get("event_type") == "session_end"
            and payload.get("session_id") == session_id
            and ended_at is not None
            and ended_at <= boundary
            and (acc.last_session_start is None or ended_at >= acc.last_session_start)
        ):
            acc.ended = True
            acc.last_activity = max(acc.last_activity or ended_at, ended_at)
    return scanned


def _scan_snapshots(
    codex_home: Path,
    *,
    cutoff: datetime,
    boundary: datetime,
) -> tuple[list[RestoredSession], int]:
    accumulators, scanned = _read_journal(codex_home, cutoff, boundary)
    candidates = _native_candidates(codex_home, cutoff, boundary)

    threads: list[_NativeThread] = []
    parsed_paths = set(candidates)
    for path in candidates:
        scanned += 1
        parsed = _parse_native_rollout(path, boundary)
        if parsed is not None:
            threads.append(parsed)

    # A recent follow-up may be selected while its initial child/root rollout
    # has an old mtime. Resolve the allowlisted parent chain by ID; UUIDv7
    # embeds its creation date, so no recursive payload scan is needed.
    session_root = codex_home / "sessions"
    fallback_days = _recent_session_days(session_root, cutoff, boundary)
    present_ids = {thread.thread_id for thread in threads}
    resolved_count = 0
    while resolved_count < _MAX_PARENT_THREADS:
        missing_ids = {
            candidate
            for thread in threads
            for candidate in (thread.session_id, thread.parent_thread_id)
            if candidate is not None and candidate not in present_ids
        }
        if not missing_ids:
            break
        progress = False
        remaining = _MAX_PARENT_THREADS - resolved_count
        for missing_id in sorted(missing_ids)[:remaining]:
            present_ids.add(missing_id)
            lookup_dirs = _thread_day_dirs(session_root, missing_id, fallback_days)
            path = _find_rollout_by_id(lookup_dirs, missing_id)
            if path is None or path in parsed_paths:
                continue
            parsed_paths.add(path)
            scanned += 1
            resolved_count += 1
            parsed = _parse_native_rollout(path, boundary)
            if parsed is not None:
                threads.append(parsed)
                progress = True
        if not progress:
            break

    thread_by_id = {thread.thread_id: thread for thread in threads}

    def inherited_agent_path(thread: _NativeThread) -> str | None:
        current = thread
        visited: set[str] = set()
        while current.thread_id not in visited:
            visited.add(current.thread_id)
            if current.agent_path:
                return current.agent_path
            if not current.parent_thread_id:
                break
            parent = thread_by_id.get(current.parent_thread_id)
            if parent is None or parent.session_id != thread.session_id:
                break
            current = parent
        return None

    # A follow-up turn creates a new native thread with the same agent_path,
    # but current Codex versions may omit agent_path on the follow-up meta.
    # Keep only the newest turn's activity, but retain the first/root child ID
    # so later hook SubagentStop events address the restored character.
    child_groups: dict[tuple[str, str], list[_NativeThread]] = {}
    root_threads: list[_NativeThread] = []
    for thread in threads:
        is_child = thread.parent_thread_id is not None or thread.thread_id != thread.session_id
        if not is_child:
            root_threads.append(thread)
            continue
        group_key = inherited_agent_path(thread) or thread.thread_id
        child_groups.setdefault((thread.session_id, group_key), []).append(thread)

    for thread in root_threads:
        acc = accumulators.setdefault(thread.session_id, _SessionAccumulator(thread.session_id))
        acc.last_activity = max(acc.last_activity or thread.last_activity, thread.last_activity)
        acc.project_name = acc.project_name or thread.project_name
        acc.model = acc.model or thread.model
        if thread.turn_state_at is not None and (
            acc.main_turn_state_at is None
            or thread.turn_state_at > acc.main_turn_state_at
        ):
            acc.main_turn_active = thread.active_turn
            acc.main_turn_state_at = thread.turn_state_at
        for tool_id, pending in thread.pending_tools.items():
            tool_name, started_at = pending
            completed_at = acc.main_completed.get(tool_id)
            if (
                (acc.main_stop_at is not None and acc.main_stop_at >= started_at)
                or (completed_at is not None and completed_at >= started_at)
            ):
                continue
            existing_pending = acc.main_pending.get(tool_id)
            if existing_pending is None or started_at > existing_pending[1]:
                acc.main_pending[tool_id] = (tool_name, started_at)

    for (session_id, _group_key), grouped in child_groups.items():
        latest = max(grouped, key=lambda item: item.last_activity)
        group_ids = {item.thread_id for item in grouped}
        initial_turns = [
            item for item in grouped if item.parent_thread_id not in group_ids
        ]
        canonical_turn = min(
            initial_turns or grouped,
            key=lambda item: (
                item.created_at or item.last_activity,
                item.thread_id,
            ),
        )
        canonical_id = canonical_turn.thread_id
        acc = accumulators.setdefault(session_id, _SessionAccumulator(session_id))
        acc.last_activity = max(acc.last_activity or latest.last_activity, latest.last_activity)
        acc.project_name = acc.project_name or latest.project_name
        if latest.last_activity < cutoff:
            continue
        stopped_at = acc.stopped_agents.get(canonical_id)
        native_state_at = latest.turn_state_at or latest.last_activity
        if (
            not latest.active_turn
            or (stopped_at is not None and stopped_at >= native_state_at)
        ):
            continue
        existing = acc.agents.get(canonical_id)
        if existing is None:
            existing = RestoredAgent(
                agent_id=canonical_id,
                agent_type=latest.agent_type,
                model=latest.model,
                # Agent numbering must remain stable across Viewer restarts.
                # Rollout activity advances while a child works, whereas its
                # session_meta timestamp is immutable for the conceptual
                # agent's initial turn.
                started_at=canonical_turn.created_at or canonical_turn.last_activity,
            )
            acc.agents[canonical_id] = existing
        valid_native_pending = [
            pending
            for tool_id, pending in latest.pending_tools.items()
            if not (
                stopped_at is not None and stopped_at >= pending[1]
            )
            and not (
                (completed_at := acc.agent_completed.get(canonical_id, {}).get(tool_id))
                is not None
                and completed_at >= pending[1]
            )
        ]
        latest_pending = (
            max(valid_native_pending, key=lambda item: item[1])
            if valid_native_pending
            else None
        )
        existing.last_tool_name = (
            latest_pending[0] if latest_pending else existing.last_tool_name
        )
        existing.model = latest.model or existing.model
        existing.state = (
            "waiting"
            if latest_pending and latest_pending[0] == "AgentWait"
            else "working"
        )

    scanned += _apply_terminal_markers(codex_home, accumulators, boundary)

    snapshots: list[RestoredSession] = []
    for acc in accumulators.values():
        if acc.ended or acc.last_activity is None or acc.last_activity < cutoff:
            continue
        for agent_id, pending in acc.agent_pending.items():
            if agent_id not in acc.agents or not pending:
                continue
            tool, _ = max(pending.values(), key=lambda item: item[1])
            acc.agents[agent_id].last_tool_name = tool
            acc.agents[agent_id].state = "waiting" if tool == "AgentWait" else "working"
        last_main = (
            max(acc.main_pending.values(), key=lambda item: item[1])
            if acc.main_pending
            else None
        )
        snapshots.append(
            RestoredSession(
                session_id=acc.session_id,
                project_name=acc.project_name,
                model=acc.model,
                boss_state=(
                    "reviewing" if last_main and last_main[0] == "AgentWait" else
                    "working" if last_main or acc.main_turn_active else "idle"
                ),
                last_tool_name=last_main[0] if last_main else None,
                agents=sorted(
                    acc.agents.values(),
                    key=lambda agent: (agent.started_at or acc.last_activity, agent.agent_id),
                ),
                last_activity=acc.last_activity,
                captured_at=boundary,
                event_fingerprints=acc.fingerprints,
            )
        )
    snapshots.sort(key=lambda snapshot: snapshot.last_activity, reverse=True)
    return snapshots[:_MAX_ACTIVE_SESSIONS], scanned


class CodexSessionRestorer:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._result = RestoreResult()
        self._task: asyncio.Task[dict[str, str | int | None]] | None = None
        self._has_run = False

    def status(self) -> dict[str, str | int | None]:
        settings, _ = load_settings()
        if (
            not self._has_run
            and self._task is None
            and not bool(settings.get("restore_codex_sessions", True))
        ):
            return RestoreResult(
                status="disabled", message="Codexセッションの自動復元は無効です"
            ).as_dict()
        return self._result.as_dict()

    def start(self, event_processor: EventProcessor) -> dict[str, str | int | None]:
        """Start one background scan, coalescing concurrent manual requests."""
        if self._task is not None and not self._task.done():
            return self.status()
        self._has_run = True
        self._result = RestoreResult(status="checking", message="Codexセッションを確認中…")
        task = asyncio.create_task(self.restore(event_processor))
        self._task = task

        def clear_task(completed: asyncio.Task[dict[str, str | int | None]]) -> None:
            if self._task is completed:
                self._task = None

        task.add_done_callback(clear_task)
        return self.status()

    async def cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def restore(self, event_processor: EventProcessor) -> dict[str, str | int | None]:
        async with self._lock:
            self._has_run = True
            self._result = RestoreResult(status="checking", message="Codexセッションを確認中…")
            boundary = datetime.now(UTC)
            start_sequence = event_processor.begin_codex_restore()
            settings, _ = load_settings()
            window = int(settings.get("restore_window_minutes", 30))
            cutoff = boundary - timedelta(minutes=window)
            configured = os.environ.get("CODEX_HOME")
            codex_home = Path(configured) if configured else Path.home() / ".codex"
            try:
                snapshots, scanned = await asyncio.to_thread(
                    _scan_snapshots,
                    codex_home,
                    cutoff=cutoff,
                    boundary=boundary,
                )
                restored_agents = 0
                restored_sessions = 0
                for snapshot in snapshots:
                    merged = await event_processor.merge_codex_restored_session(
                        snapshot, start_sequence=start_sequence
                    )
                    if merged:
                        restored_sessions += 1
                        restored_agents += len(snapshot.agents)
                self._result = RestoreResult(
                    status="completed",
                    restored_sessions=restored_sessions,
                    restored_agents=restored_agents,
                    scanned_files=scanned,
                    last_run=datetime.now(UTC).isoformat(),
                    message=f"Codexセッション: {restored_sessions}件復元",
                )
            except Exception as exc:
                # Never include source JSON, paths, or exception locals.
                logger.warning("Codex session restoration failed: %s", type(exc).__name__)
                self._result = RestoreResult(
                    status="failed",
                    last_run=datetime.now(UTC).isoformat(),
                    message=(
                        "Codexセッションの復元に失敗しました。"
                        "Codex自体の動作には影響ありません。"
                    ),
                    error=type(exc).__name__,
                )
            return self._result.as_dict()


_restorer = CodexSessionRestorer()


def get_codex_session_restorer() -> CodexSessionRestorer:
    return _restorer
