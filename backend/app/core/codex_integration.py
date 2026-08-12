"""Process-local telemetry for the Codex live hook integration.

Only sanitized counters and timestamps are retained.  Event payloads, prompts,
session identifiers, and credentials are deliberately never stored here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.events import AnyEvent, EventType

CODEX_LIVE_EVENT_TYPES = frozenset(
    {
        EventType.SESSION_START,
        EventType.SESSION_END,
        EventType.USER_PROMPT_SUBMIT,
        EventType.PRE_TOOL_USE,
        EventType.POST_TOOL_USE,
        EventType.SUBAGENT_START,
        EventType.SUBAGENT_STOP,
        EventType.STOP,
    }
)


@dataclass(slots=True)
class CodexLiveTelemetry:
    """Counters for live Codex events received since this backend started."""

    started_at: datetime
    last_live_event_at: datetime | None = None
    live_event_count: int = 0

    def record(self, event: AnyEvent, *, received_at: datetime | None = None) -> bool:
        """Record a genuine Codex lifecycle event and return whether it counted."""
        if (
            event.event_type not in CODEX_LIVE_EVENT_TYPES
            or event.data.source != "codex"
            or bool(event.data.restored)
        ):
            return False
        now = received_at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        self.last_live_event_at = now.astimezone(UTC)
        self.live_event_count += 1
        return True

    def snapshot(self) -> dict[str, str | int | None]:
        return {
            "backend_started_at": self.started_at.isoformat(),
            "last_live_event_at": (
                self.last_live_event_at.isoformat() if self.last_live_event_at else None
            ),
            "live_event_count": self.live_event_count,
        }


_telemetry = CodexLiveTelemetry(started_at=datetime.now(UTC))


def get_codex_live_telemetry() -> CodexLiveTelemetry:
    return _telemetry


def reset_codex_live_telemetry(*, started_at: datetime | None = None) -> None:
    """Reset process telemetry. Intended for isolated tests."""
    global _telemetry
    _telemetry = CodexLiveTelemetry(started_at=started_at or datetime.now(UTC))
