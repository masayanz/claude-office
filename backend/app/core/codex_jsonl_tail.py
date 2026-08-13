"""Bounded polling tail monitor for native Codex rollout JSONL files.

The monitor watches only today's and adjacent session directories plus the
bounded session index tail. It never forwards native transcript bodies; only
allowlisted lifecycle/tool identity fields are converted into Viewer events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ntpath
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.codex_hybrid import HybridCoordinator
from app.models.events import (
    AgentEvent,
    AgentEventData,
    AnyEvent,
    EventType,
    LifecycleEvent,
    LifecycleEventData,
    PromptEvent,
    PromptEventData,
    SessionEvent,
    SessionEventData,
    ToolEvent,
    ToolEventData,
)

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_META_RE = re.compile(r"[A-Za-z0-9._:/+\-]{1,128}\Z")
_MAX_LINE_BYTES = 1024 * 1024
_READ_BYTES = 256 * 1024
_INDEX_BYTES = 256 * 1024
_MAX_DISCOVERY_FILES = 200
_TOOL_NAMES = {
    "collaborationspawn_agent": "Agent",
    "collaborationwait_agent": "AgentWait",
    "spawn_agent": "Agent",
    "wait_agent": "AgentWait",
}


def _safe(value: object) -> str | None:
    return value if isinstance(value, str) and _META_RE.fullmatch(value) else None


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (result if result.tzinfo else result.replace(tzinfo=UTC)).astimezone(UTC)


def _project_name(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = ntpath.basename(value.rstrip("/\\")) or Path(value).name
    return _safe(name)


def _json_object(line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(slots=True)
class _RolloutMeta:
    session_id: str
    thread_id: str
    project_name: str | None = None
    model: str | None = None
    agent_type: str | None = None
    agent_name: str | None = None
    agent_path: str | None = None
    parent_thread_id: str | None = None
    agent_id: str | None = None
    started_emitted: bool = False
    stopped_emitted: bool = False


@dataclass(slots=True)
class _Cursor:
    path: Path
    offset: int
    partial: bytes = b""
    signature: tuple[int, int, int] | None = None
    meta: _RolloutMeta | None = None


class JsonlTailReader:
    """Incremental UTF-8/JSONL reader, useful independently in unit tests."""

    def __init__(self, *, max_line_bytes: int = _MAX_LINE_BYTES) -> None:
        self.offset = 0
        self.partial = b""
        self.parse_errors = 0
        self.max_line_bytes = max_line_bytes

    def feed(self, data: bytes, *, eof: bool = False) -> list[dict[str, Any]]:
        combined = self.partial + data
        lines = combined.split(b"\n")
        self.partial = lines.pop() if lines else b""
        if len(self.partial) > self.max_line_bytes:
            self.partial = b""
            self.parse_errors += 1
        result: list[dict[str, Any]] = []
        for line in lines:
            line = line.rstrip(b"\r")
            if not line:
                continue
            if len(line) > self.max_line_bytes:
                self.parse_errors += 1
                continue
            parsed = _json_object(line)
            if parsed is None:
                self.parse_errors += 1
                continue
            result.append(parsed)
        if eof and self.partial:
            # A final unterminated line is retained for the next append. It is
            # deliberately not treated as a malformed JSON record.
            pass
        return result


class CodexJsonlTailMonitor:
    """Poll active rollout files without recursively scanning the filesystem."""

    def __init__(self, *, poll_interval: float = 1.0, max_sessions: int = 10) -> None:
        self.poll_interval = max(0.2, poll_interval)
        self.max_sessions = max(1, max_sessions)
        self._coordinator: HybridCoordinator | None = None
        self._processor_task: asyncio.Task[None] | None = None
        self._cursors: dict[Path, _Cursor] = {}
        self._started_at = datetime.now(UTC)
        self._initial_discovery_done = False
        self._parse_errors = 0
        self._file_access_failures = 0
        self._event_count = 0
        self._last_event_at: datetime | None = None
        self._last_scan_at: datetime | None = None
        self._last_error_at: datetime | None = None

    @staticmethod
    def _codex_home() -> Path:
        configured = os.environ.get("CODEX_HOME")
        return Path(configured) if configured else Path.home() / ".codex"

    def start(self, coordinator: HybridCoordinator) -> None:
        self._coordinator = coordinator
        if self._processor_task is None or self._processor_task.done():
            self._started_at = datetime.now(UTC)
            self._processor_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._processor_task
        self._processor_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def status(self) -> dict[str, object]:
        now = datetime.now(UTC)
        age = (
            (now - self._last_event_at).total_seconds()
            if self._last_event_at is not None
            else None
        )
        return {
            "state": "monitoring" if self._processor_task is not None else "stopped",
            "health": (
                "healthy"
                if self._processor_task is not None and (age is None or age <= 120)
                else "idle"
            ),
            "monitored_sessions": len(
                {c.meta.session_id for c in self._cursors.values() if c.meta}
            ),
            "monitored_files": len(self._cursors),
            "event_count": self._event_count,
            "last_jsonl_event_at": self._last_event_at.isoformat() if self._last_event_at else None,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "parse_errors": self._parse_errors,
            "file_access_failures": self._file_access_failures,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at else None,
            "max_sessions": self.max_sessions,
        }

    async def _run(self) -> None:
        while True:
            started = datetime.now(UTC)
            self._last_scan_at = started
            try:
                await self._discover()
                await self._read_all()
                self._prune()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._file_access_failures += 1
                self._last_error_at = datetime.now(UTC)
                logger.warning("Codex JSONL monitor cycle failed: %s", type(exc).__name__)
            if self._coordinator is not None:
                self._coordinator.update_tail_status(self.status())
            await asyncio.sleep(self.poll_interval)

    async def _discover(self) -> None:
        home = self._codex_home()
        session_root = home / "sessions"
        now = datetime.now(UTC)
        directories = [
            session_root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            for day in (now - timedelta(days=1), now, now + timedelta(days=1))
        ]
        paths: list[Path] = []
        indexed_paths: set[Path] = set()
        for directory in directories:
            try:
                paths.extend(directory.glob("rollout-*.jsonl"))
            except OSError:
                self._file_access_failures += 1
        # The index is read only as a bounded hint. It prevents missing a
        # UUIDv7 rollout whose local date is on the other side of midnight.
        index = home / "session_index.jsonl"
        try:
            with index.open("rb") as stream:
                stream.seek(max(0, index.stat().st_size - _INDEX_BYTES))
                for raw in stream.read().splitlines():
                    record = _json_object(raw)
                    thread_id = _safe_id(record.get("id")) if record else None
                    if not thread_id:
                        continue
                    for directory in directories:
                        matches = list(directory.glob(f"rollout-*{thread_id}.jsonl"))
                        paths.extend(matches[:1])
                        indexed_paths.update(matches[:1])
        except (OSError, ValueError):
            pass
        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path in seen or len(unique) >= _MAX_DISCOVERY_FILES:
                continue
            seen.add(path)
            try:
                if path.is_file():
                    unique.append(path)
            except OSError:
                self._file_access_failures += 1
        # Prefer recently modified candidates, but retain index/day discovery
        # bounds even on platforms with immutable rollout mtimes.
        unique.sort(key=lambda p: (p in indexed_paths, self._mtime(p)), reverse=True)
        metas: dict[Path, _RolloutMeta] = {}
        selected_sessions: list[str] = []
        selected_set: set[str] = set()
        for path in unique:
            try:
                meta = self._read_meta(path)
            except OSError:
                self._file_access_failures += 1
                continue
            if meta is None:
                continue
            metas[path] = meta
            if meta.session_id not in selected_set:
                if len(selected_sessions) >= self.max_sessions:
                    continue
                selected_sessions.append(meta.session_id)
                selected_set.add(meta.session_id)
        for path in unique:
            meta = metas.get(path)
            if meta is None or meta.session_id not in selected_set:
                continue
            if path not in self._cursors:
                cursor = self._new_cursor(
                    path,
                    initial=not self._initial_discovery_done,
                    meta=meta,
                )
                if cursor is not None:
                    self._cursors[path] = cursor
        self._initial_discovery_done = True

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _new_cursor(
        self,
        path: Path,
        *,
        initial: bool,
        meta: _RolloutMeta | None = None,
    ) -> _Cursor | None:
        try:
            stat = path.stat()
            meta = meta or self._read_meta(path)
        except OSError:
            self._file_access_failures += 1
            return None
        if meta is None:
            return None
        offset = stat.st_size if initial else 0
        return _Cursor(
            path=path,
            offset=offset,
            signature=(stat.st_dev, stat.st_ino, stat.st_ctime_ns),
            meta=meta,
        )

    def _read_meta(self, path: Path) -> _RolloutMeta | None:
        try:
            with path.open("rb") as stream:
                raw = stream.read(128 * 1024)
        except OSError:
            raise
        for line in raw.splitlines():
            record = _json_object(line)
            if not record or record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            thread_id = _safe_id(payload.get("id"))
            session_id = _safe_id(payload.get("session_id")) or thread_id
            if not thread_id or not session_id:
                return None
            child = thread_id != session_id or bool(_safe_id(payload.get("parent_thread_id")))
            return _RolloutMeta(
                session_id=session_id,
                thread_id=thread_id,
                project_name=_project_name(payload.get("cwd")),
                parent_thread_id=_safe_id(payload.get("parent_thread_id")),
                agent_type=_safe(payload.get("agent_role")),
                agent_name=_safe(payload.get("agent_nickname")),
                agent_path=_safe(payload.get("agent_path")),
                agent_id=(
                    _safe(payload.get("agent_path")) or thread_id
                    if child
                    else None
                ),
            )
        return None

    async def _read_all(self) -> None:
        for cursor in list(self._cursors.values()):
            await self._read_cursor(cursor)

    async def _read_cursor(self, cursor: _Cursor) -> None:
        path = cursor.path
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_ctime_ns)
            if stat.st_size < cursor.offset or signature != cursor.signature:
                cursor.offset = 0
                cursor.partial = b""
                cursor.signature = signature
                with suppress(OSError):
                    cursor.meta = self._read_meta(path) or cursor.meta
            if stat.st_size <= cursor.offset:
                return
            with path.open("rb") as stream:
                stream.seek(cursor.offset)
                data = stream.read(_READ_BYTES)
            cursor.offset += len(data)
        except OSError:
            self._file_access_failures += 1
            if not path.exists():
                self._cursors.pop(path, None)
            return
        reader = JsonlTailReader()
        reader.partial = cursor.partial
        records = reader.feed(data)
        cursor.partial = reader.partial
        self._parse_errors += reader.parse_errors
        for record in records:
            await self._handle_record(cursor, record)

    async def _handle_record(self, cursor: _Cursor, record: dict[str, Any]) -> None:
        meta = cursor.meta
        if meta is None:
            return
        timestamp = _timestamp(record.get("timestamp")) or datetime.now(UTC)
        top_type = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        if top_type == "session_meta":
            # The first line is allowed to refresh identity metadata after a
            # rotation, but raw cwd/record data never leaves this method.
            meta.model = _safe(payload.get("model")) or meta.model
            await self._emit_session_start(meta, timestamp)
            return
        if top_type == "turn_context":
            meta.model = _safe(payload.get("model")) or meta.model
            return
        if top_type == "event_msg":
            event_type = payload.get("type")
            if event_type == "task_started":
                await self._emit(
                    self._prompt_or_agent_info(meta, timestamp, _safe_id(payload.get("turn_id")))
                )
            elif event_type == "task_complete":
                if meta.agent_id:
                    await self._emit(self._agent_stop(meta, timestamp))
                else:
                    await self._emit(self._stop(meta, timestamp, _safe_id(payload.get("turn_id"))))
            elif event_type == "sub_agent_activity":
                agent_id = _safe_id(payload.get("agent_thread_id"))
                if agent_id:
                    activity_kind = _safe(payload.get("kind"))
                    activity_event = (
                        EventType.SUBAGENT_START
                        if activity_kind in {"start", "started", "spawned"}
                        else EventType.SUBAGENT_STOP
                        if activity_kind in {"stop", "stopped", "completed", "finished"}
                        else EventType.SUBAGENT_INFO
                    )
                    await self._emit(
                        AgentEvent(
                            event_type=activity_event,
                            session_id=meta.session_id,
                            timestamp=timestamp,
                            data=AgentEventData(
                                source="codex",
                                project_name=meta.project_name,
                                model=meta.model,
                                agent_id=agent_id,
                                agent_type=meta.agent_type,
                                agent_name=meta.agent_name,
                            ),
                        )
                    )
            elif event_type == "session_end":
                await self._emit(
                    SessionEvent(
                        event_type=EventType.SESSION_END,
                        session_id=meta.session_id,
                        timestamp=timestamp,
                        data=SessionEventData(
                            source="codex", project_name=meta.project_name, model=meta.model
                        ),
                    )
                )
            elif event_type == "patch_apply_end":
                call_id = _safe_id(payload.get("call_id"))
                await self._emit(
                    self._tool(
                        meta,
                        timestamp,
                        "apply_patch",
                        call_id,
                        _safe_id(payload.get("turn_id")),
                        start=False,
                    )
                )
            return
        if top_type != "response_item":
            return
        response_type = payload.get("type")
        if response_type in {"function_call", "custom_tool_call"}:
            tool_name = _safe(payload.get("name"))
            call_id = _safe_id(payload.get("call_id")) or _safe_id(payload.get("id"))
            if tool_name:
                await self._emit(
                    self._tool(
                        meta,
                        timestamp,
                        tool_name,
                        call_id,
                        _safe_id(payload.get("turn_id")),
                        start=True,
                    )
                )
        elif response_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = _safe_id(payload.get("call_id"))
            await self._emit(
                self._tool(
                    meta,
                    timestamp,
                    None,
                    call_id,
                    _safe_id(payload.get("turn_id")),
                    start=False,
                )
            )

    async def _emit_session_start(self, meta: _RolloutMeta, timestamp: datetime) -> None:
        if meta.started_emitted:
            return
        meta.started_emitted = True
        if meta.agent_id:
            await self._emit(
                AgentEvent(
                    event_type=EventType.SUBAGENT_START,
                    session_id=meta.session_id,
                    timestamp=timestamp,
                    data=AgentEventData(
                        source="codex",
                        project_name=meta.project_name,
                        model=meta.model,
                        agent_id=meta.agent_id,
                        agent_type=meta.agent_type,
                        agent_name=meta.agent_name,
                    ),
                )
            )
        else:
            await self._emit(
                SessionEvent(
                    event_type=EventType.SESSION_START,
                    session_id=meta.session_id,
                    timestamp=timestamp,
                    data=SessionEventData(
                        source="codex", project_name=meta.project_name, model=meta.model
                    ),
                )
            )

    def _prompt_or_agent_info(
        self, meta: _RolloutMeta, timestamp: datetime, turn_id: str | None
    ) -> AnyEvent:
        if meta.agent_id:
            return AgentEvent(
                event_type=EventType.SUBAGENT_INFO,
                session_id=meta.session_id,
                timestamp=timestamp,
                data=AgentEventData(
                    source="codex",
                    project_name=meta.project_name,
                    model=meta.model,
                    agent_id=meta.agent_id,
                ),
            )
        return PromptEvent(
            event_type=EventType.USER_PROMPT_SUBMIT,
            session_id=meta.session_id,
            timestamp=timestamp,
            data=PromptEventData(
                source="codex",
                project_name=meta.project_name,
                model=meta.model,
                prompt="Codex user activity",
                turn_id=turn_id,
            ),
        )

    def _tool(
        self,
        meta: _RolloutMeta,
        timestamp: datetime,
        tool_name: str | None,
        call_id: str | None,
        turn_id: str | None,
        *,
        start: bool,
    ) -> ToolEvent:
        normalized_tool = _TOOL_NAMES.get(tool_name or "", tool_name) if tool_name else None
        return ToolEvent(
            event_type=EventType.PRE_TOOL_USE if start else EventType.POST_TOOL_USE,
            session_id=meta.session_id,
            timestamp=timestamp,
            data=ToolEventData(
                source="codex",
                project_name=meta.project_name,
                model=meta.model,
                agent_id=meta.agent_id,
                agent_type=meta.agent_type,
                tool_name=normalized_tool,
                tool_use_id=call_id,
                turn_id=turn_id,
            ),
        )

    def _agent_stop(self, meta: _RolloutMeta, timestamp: datetime) -> AgentEvent:
        meta.stopped_emitted = True
        return AgentEvent(
            event_type=EventType.SUBAGENT_STOP,
            session_id=meta.session_id,
            timestamp=timestamp,
            data=AgentEventData(
                source="codex",
                project_name=meta.project_name,
                model=meta.model,
                agent_id=meta.agent_id,
                agent_type=meta.agent_type,
            ),
        )

    def _stop(
        self, meta: _RolloutMeta, timestamp: datetime, turn_id: str | None
    ) -> LifecycleEvent:
        return LifecycleEvent(
            event_type=EventType.STOP,
            session_id=meta.session_id,
            timestamp=timestamp,
            data=LifecycleEventData(
                source="codex",
                project_name=meta.project_name,
                model=meta.model,
                turn_id=turn_id,
            ),
        )

    async def _emit(self, event: AnyEvent) -> None:
        if self._coordinator is None:
            return
        if await self._coordinator.process(event, source="jsonl"):
            self._event_count += 1
            self._last_event_at = datetime.now(UTC)

    def _prune(self) -> None:
        if len(self._cursors) <= self.max_sessions * 3:
            return
        ranked = sorted(self._cursors.values(), key=lambda c: self._mtime(c.path), reverse=True)
        keep = {cursor.path for cursor in ranked[: self.max_sessions * 3]}
        self._cursors = {path: cursor for path, cursor in self._cursors.items() if path in keep}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_monitor = CodexJsonlTailMonitor(
    poll_interval=_env_float("CODEX_TAIL_POLL_INTERVAL", 1.0),
    max_sessions=_env_int("CODEX_TAIL_MAX_SESSIONS", 10),
)


def get_codex_jsonl_tail_monitor() -> CodexJsonlTailMonitor:
    return _monitor
