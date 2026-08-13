"""Hybrid coordinator for Codex hooks and native rollout JSONL events.

The coordinator intentionally keeps only allowlisted event identity metadata.
It is the single gate before a Codex event reaches ``EventProcessor`` so a
hook and the JSONL tail cannot create two office actions for one operation.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.events import AnyEvent

_SAFE_RE = re.compile(r"[A-Za-z0-9._:/+\-]{1,128}\Z")
_DEDUP_TTL_SECONDS = 30.0
_HOOK_HEALTH_SECONDS = 45.0
_TAIL_HEALTH_SECONDS = 45.0
_MAX_SESSION_TELEMETRY = 1024


def _safe(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_RE.fullmatch(value) else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class NormalizedCodexEvent:
    """Content-free identity used for deduplication and diagnostics."""

    source: str
    session_id: str
    event_kind: str
    timestamp: datetime
    agent_id: str | None = None
    agent_type: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    model: str | None = None
    project_name: str | None = None
    source_event_id: str | None = None
    raw_sequence: str | None = None


@dataclass(slots=True)
class CodexSessionTelemetry:
    session_id: str
    last_activity_at: datetime | None = None
    last_hook_event_at: datetime | None = None
    last_tail_event_at: datetime | None = None
    hook_event_count: int = 0
    tail_event_count: int = 0
    deduplicated_events: int = 0


def normalize_codex_event(event: AnyEvent, *, source: str) -> NormalizedCodexEvent | None:
    """Extract only safe metadata from a typed event envelope."""
    if event.data.source != "codex":
        return None
    data: Any = event.data
    kind = event.event_type.value
    return NormalizedCodexEvent(
        source=source,
        session_id=event.session_id,
        event_kind=kind,
        timestamp=_utc(event.timestamp),
        agent_id=_safe(getattr(data, "agent_id", None)),
        agent_type=_safe(getattr(data, "agent_type", None)),
        tool_name=_safe(getattr(data, "tool_name", None)),
        tool_use_id=_safe(getattr(data, "tool_use_id", None)),
        model=_safe(getattr(data, "model", None)),
        project_name=_safe(getattr(data, "project_name", None)),
        source_event_id=_safe(getattr(data, "turn_id", None)),
    )


def _dedupe_key(item: NormalizedCodexEvent) -> tuple[str, ...]:
    """Build a stable key, using a short time bucket only as a last resort."""
    identity = item.tool_use_id or item.source_event_id
    if identity:
        return (
            item.session_id,
            item.event_kind,
            item.agent_id or "main",
            identity,
        )
    # Native Codex records and hook deliveries normally carry the same event
    # timestamp. A two-second bucket handles delivery jitter without merging
    # repeated id-less tools in different time buckets.
    bucket = int(item.timestamp.timestamp() / 2)
    return (
        item.session_id,
        item.event_kind,
        item.agent_id or "main",
        item.tool_name or "",
        str(bucket),
    )


class HybridCoordinator:
    """Accept hook/tail events, deduplicate them, and forward accepted events."""

    def __init__(self) -> None:
        self._processor: Any | None = None
        self._sessions: dict[str, CodexSessionTelemetry] = {}
        self._recent: OrderedDict[tuple[str, ...], tuple[datetime, str]] = OrderedDict()
        self._tail_status: dict[str, object] = {}
        self._accepted_hook_events = 0
        self._accepted_tail_events = 0
        self._deduplicated_events = 0

    def bind(self, processor: Any) -> None:
        self._processor = processor

    def is_bound_to(self, processor: Any) -> bool:
        return self._processor is processor

    def update_tail_status(self, status: dict[str, object]) -> None:
        self._tail_status = dict(status)

    def _prune(self, now: datetime) -> None:
        cutoff = now.timestamp() - _DEDUP_TTL_SECONDS
        while self._recent:
            key, (seen, _source) = next(iter(self._recent.items()))
            if seen.timestamp() >= cutoff:
                break
            self._recent.pop(key, None)

    def _session(self, session_id: str) -> CodexSessionTelemetry:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        if len(self._sessions) >= _MAX_SESSION_TELEMETRY:
            oldest = min(
                self._sessions.values(),
                key=lambda item: item.last_activity_at or datetime.min.replace(tzinfo=UTC),
            )
            self._sessions.pop(oldest.session_id, None)
        session = CodexSessionTelemetry(session_id)
        self._sessions[session_id] = session
        return session

    async def process(self, event: AnyEvent, *, source: str) -> bool:
        """Forward one accepted event. Return false for non-Codex/duplicates."""
        normalized = normalize_codex_event(event, source=source)
        if normalized is None or self._processor is None:
            return False
        now = datetime.now(UTC)
        self._prune(now)
        key = _dedupe_key(normalized)
        previous = self._recent.get(key)
        if previous is not None and previous[1] != source:
            self._deduplicated_events += 1
            session = self._session(normalized.session_id)
            session.deduplicated_events += 1
            self._recent.move_to_end(key)
            return False
        self._recent[key] = (now, source)
        self._recent.move_to_end(key)

        session = self._session(normalized.session_id)
        session.last_activity_at = now
        if source == "hook":
            session.last_hook_event_at = now
            session.hook_event_count += 1
            self._accepted_hook_events += 1
        else:
            session.last_tail_event_at = now
            session.tail_event_count += 1
            self._accepted_tail_events += 1
        await self._processor.process_event(event)
        return True

    @staticmethod
    def _age(value: datetime | None, now: datetime) -> float | None:
        if value is None:
            return None
        return max(0.0, (now - value).total_seconds())

    def status(self, *, restored_sessions: int = 0) -> dict[str, object]:
        now = datetime.now(UTC)
        modes: dict[str, str] = {}
        for session_id, session in self._sessions.items():
            hook_live = (
                self._age(session.last_hook_event_at, now) or 10**9
            ) <= _HOOK_HEALTH_SECONDS
            tail_live = (
                self._age(session.last_tail_event_at, now) or 10**9
            ) <= _TAIL_HEALTH_SECONDS
            if hook_live and tail_live:
                modes[session_id] = "HYBRID"
            elif hook_live:
                modes[session_id] = "HOOK_ACTIVE"
            elif tail_live:
                modes[session_id] = "TAIL_FALLBACK"
            elif session.last_activity_at is not None:
                modes[session_id] = "IDLE"
        if any(value == "HYBRID" for value in modes.values()):
            current_mode = "HYBRID"
        elif any(value == "HOOK_ACTIVE" for value in modes.values()):
            current_mode = "HOOK_ACTIVE"
        elif any(value == "TAIL_FALLBACK" for value in modes.values()):
            current_mode = "TAIL_FALLBACK"
        elif int(self._tail_status.get("monitored_sessions", 0) or 0) > 0:
            # A registered tail target is already a usable fallback even
            # before its next append. Do not expose the misleading
            # "restore-only" state while the monitor is alive.
            current_mode = "TAIL_FALLBACK"
        elif restored_sessions:
            current_mode = "RESTORED_ONLY"
        else:
            current_mode = "IDLE"
        tail = self._tail_status
        return {
            "current_input_mode": current_mode,
            "session_modes": modes,
            "active_codex_sessions": len(modes),
            "hook_event_count": self._accepted_hook_events,
            "tail_event_count": self._accepted_tail_events,
            "deduplicated_events": self._deduplicated_events,
            "last_hook_event_at": max(
                (s.last_hook_event_at for s in self._sessions.values() if s.last_hook_event_at),
                default=None,
            ),
            "last_jsonl_event_at": max(
                (s.last_tail_event_at for s in self._sessions.values() if s.last_tail_event_at),
                default=None,
            ),
            "monitored_sessions": int(tail.get("monitored_sessions", 0) or 0),
            "jsonl_monitor": tail.get("state", "disabled"),
            "jsonl_monitor_health": tail.get("health", "idle"),
            "jsonl_parse_errors": int(tail.get("parse_errors", 0) or 0),
            "jsonl_file_access_failures": int(tail.get("file_access_failures", 0) or 0),
            "jsonl_last_scan_at": tail.get("last_scan_at"),
            "restored_sessions": restored_sessions,
        }


_coordinator = HybridCoordinator()


def get_codex_hybrid_coordinator() -> HybridCoordinator:
    return _coordinator
