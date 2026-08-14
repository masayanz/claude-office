"""Privacy-safe Replay metadata and state projection helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from app.db.models import EventRecord, ReplayEventRecord
from app.models.events import AnyEvent, EventAdapter, EventType
from app.models.sessions import GameState

_SAFE_TEXT_RE = re.compile(r"[^\w .:/@+\-#()\[\]]", re.UNICODE)


def _safe_text(value: object | None, maximum: int = 120) -> str | None:
    """Keep display metadata bounded and free from control/free-form payloads."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = _SAFE_TEXT_RE.sub("", value.replace("\r", " ").replace("\n", " "))
    value = " ".join(value.split())[:maximum].strip()
    return value or None


def safe_display_text(value: object | None, maximum: int = 120) -> str | None:
    """Expose the bounded allow-list sanitizer to Replay response builders."""
    return _safe_text(value, maximum)


def safe_state_for_event(event_type: str | EventType) -> str:
    """Map a hook event to a small visual state label."""
    value = str(event_type)
    return {
        EventType.SESSION_START.value: "starting",
        EventType.SESSION_END.value: "ended",
        EventType.USER_PROMPT_SUBMIT.value: "thinking",
        EventType.PRE_TOOL_USE.value: "working",
        EventType.POST_TOOL_USE.value: "reviewing",
        EventType.SUBAGENT_START.value: "arriving",
        EventType.SUBAGENT_STOP.value: "leaving",
        EventType.CLEANUP.value: "departed",
        EventType.WAITING.value: "waiting",
        EventType.TEAMMATE_IDLE.value: "waiting",
        EventType.STOP.value: "completed",
        EventType.ERROR.value: "error",
        EventType.LEAVING.value: "leaving",
        EventType.WALKING_TO_DESK.value: "working",
    }.get(value, "active")


def _event_key(event: AnyEvent) -> str:
    timestamp = (
        event.timestamp.replace(tzinfo=UTC)
        if event.timestamp.tzinfo is None
        else event.timestamp.astimezone(UTC)
    )
    raw = "|".join(
        (
            event.session_id,
            timestamp.isoformat(),
            str(event.event_type),
            str(getattr(event.data, "agent_id", None) or "main"),
            str(getattr(event.data, "tool_use_id", None) or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_event_summary(
    event_type: str,
    tool_name: str | None = None,
    agent_id: str | None = None,
    error_type: str | None = None,
) -> str:
    """Create a Replay label without copying user/assistant/tool content."""
    tool = _safe_text(tool_name, 48)
    agent = _safe_text(agent_id, 48)
    error = _safe_text(error_type, 48)
    if event_type == EventType.PRE_TOOL_USE.value:
        return f"Tool started: {tool or 'tool'}"
    if event_type == EventType.POST_TOOL_USE.value:
        return f"Tool completed: {tool or 'tool'}"
    if event_type == EventType.SUBAGENT_START.value:
        return f"Subagent started: {agent or 'agent'}"
    if event_type in {EventType.SUBAGENT_STOP.value, EventType.CLEANUP.value}:
        return f"Subagent departed: {agent or 'agent'}"
    if event_type == EventType.ERROR.value:
        return f"Error: {error or 'unknown'}"
    return {
        EventType.SESSION_START.value: "Session started",
        EventType.SESSION_END.value: "Session ended",
        EventType.USER_PROMPT_SUBMIT.value: "Main thinking",
        EventType.WAITING.value: "Waiting",
        EventType.STOP.value: "Turn completed",
    }.get(event_type, event_type.replace("_", " ").title())


def safe_event_payload(event: AnyEvent, source_event_id: int | None = None) -> dict[str, Any]:
    """Build an allow-listed Replay record from a normalized event.

    Do not add fields here without a privacy review. In particular, no
    ``prompt``, ``message``, ``thinking``, ``tool_input`` or transcript fields
    are copied, even when they are present on the incoming event.
    """
    data = event.data
    event_type = str(event.event_type)
    agent_id = _safe_text(getattr(data, "agent_id", None) or "main", 128)
    agent_name = _safe_text(getattr(data, "agent_name", None), 80)
    agent_type = _safe_text(getattr(data, "agent_type", None), 48)
    source = _safe_text(getattr(data, "source", None), 32)
    project_name = _safe_text(getattr(data, "project_name", None), 120)
    model = _safe_text(getattr(data, "model", None), 120)
    tool_name = _safe_text(getattr(data, "tool_name", None), 80)
    tool_use_id = _safe_text(getattr(data, "tool_use_id", None), 160)
    turn_id = _safe_text(getattr(data, "turn_id", None), 160)
    error_type = _safe_text(getattr(data, "error_type", None), 80)
    safe_data: dict[str, Any] = {
        "source": source,
        "model": model,
        "agentType": agent_type,
        "safeState": safe_state_for_event(event_type),
    }
    if project_name:
        safe_data["projectName"] = project_name
    if agent_name:
        safe_data["agentName"] = agent_name
    if error_type:
        safe_data["errorType"] = error_type
    if tool_name:
        safe_data["toolName"] = tool_name
    if tool_use_id:
        safe_data["toolUseId"] = tool_use_id
    if turn_id:
        safe_data["turnId"] = turn_id
    if getattr(data, "restored", False) is True:
        safe_data["restored"] = True
    return {
        "event_key": _event_key(event),
        "source_event_id": source_event_id,
        "session_id": event.session_id,
        "timestamp": event.timestamp,
        "event_type": event_type,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_type": agent_type,
        "source": source,
        "project_name": project_name,
        "model": model,
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "error_type": error_type,
        "safe_state": safe_state_for_event(event_type),
        "safe_data": safe_data,
    }


def safe_event_payload_from_legacy(
    *,
    session_id: str,
    timestamp: datetime,
    event_type: str,
    source_event_id: int,
    agent_id: object | None = None,
    agent_name: object | None = None,
    agent_type: object | None = None,
    source: object | None = None,
    project_name: object | None = None,
    model: object | None = None,
    tool_name: object | None = None,
    tool_use_id: object | None = None,
    error_type: object | None = None,
    restored: object | None = None,
) -> dict[str, Any] | None:
    """Build the same safe projection without validating a full legacy body.

    Startup backfill can encounter hundreds of thousands of historical rows.
    Only the explicitly selected JSON columns reach this helper, which avoids
    parsing prompt/tool bodies while preserving the same privacy allow-list as
    live Replay persistence.
    """
    try:
        safe_type = EventType(event_type).value
    except ValueError:
        return None
    safe_agent_id = _safe_text(agent_id or "main", 128) or "main"
    safe_agent_name = _safe_text(agent_name, 80)
    safe_agent_type = _safe_text(agent_type, 48)
    safe_source = _safe_text(source, 32)
    safe_project_name = _safe_text(project_name, 120)
    safe_model = _safe_text(model, 120)
    safe_tool_name = _safe_text(tool_name, 80)
    safe_tool_use_id = _safe_text(tool_use_id, 160)
    safe_error_type = _safe_text(error_type, 80)
    safe_data: dict[str, Any] = {
        "source": safe_source,
        "model": safe_model,
        "agentType": safe_agent_type,
        "safeState": safe_state_for_event(safe_type),
    }
    if safe_project_name:
        safe_data["projectName"] = safe_project_name
    if safe_agent_name:
        safe_data["agentName"] = safe_agent_name
    if safe_error_type:
        safe_data["errorType"] = safe_error_type
    if safe_tool_name:
        safe_data["toolName"] = safe_tool_name
    if safe_tool_use_id:
        safe_data["toolUseId"] = safe_tool_use_id
    if restored is True:
        safe_data["restored"] = True
    safe_timestamp = (
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
    )
    raw_key = "|".join(
        (
            session_id,
            safe_timestamp.astimezone(UTC).isoformat(),
            safe_type,
            safe_agent_id,
            safe_tool_use_id or "",
        )
    )
    return {
        "event_key": hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        "source_event_id": source_event_id,
        "session_id": session_id,
        "timestamp": timestamp,
        "event_type": safe_type,
        "agent_id": safe_agent_id,
        "agent_name": safe_agent_name,
        "agent_type": safe_agent_type,
        "source": safe_source,
        "project_name": safe_project_name,
        "model": safe_model,
        "tool_name": safe_tool_name,
        "tool_use_id": safe_tool_use_id,
        "error_type": safe_error_type,
        "safe_state": safe_state_for_event(safe_type),
        "safe_data": safe_data,
    }


def safe_event_from_record(record: ReplayEventRecord) -> dict[str, Any]:
    """Serialize a persisted safe event for the frontend contract."""
    timestamp = (
        record.timestamp.astimezone(UTC)
        if record.timestamp.tzinfo
        else record.timestamp.replace(tzinfo=UTC)
    )
    detail = dict(record.safe_data or {})
    detail["safeState"] = record.safe_state
    return {
        # The API state reconstruction is keyed by the source LIVE event id.
        # Keep synthetic events on their own persisted id, but expose the
        # source id whenever this safe row originated from EventRecord.
        "id": str(record.source_event_id or record.id),
        "type": record.event_type,
        "agentId": record.agent_id or "main",
        "summary": safe_event_summary(
            record.event_type, record.tool_name, record.agent_id, record.error_type
        ),
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "detail": detail,
    }


def safe_event_from_normalized(
    event: AnyEvent, source_event_id: int | None = None
) -> dict[str, Any]:
    payload = safe_event_payload(event, source_event_id)
    timestamp = (
        event.timestamp.astimezone(UTC)
        if event.timestamp.tzinfo
        else event.timestamp.replace(tzinfo=UTC)
    )
    return {
        "id": str(source_event_id or payload["event_key"]),
        "type": payload["event_type"],
        "agentId": payload["agent_id"] or "main",
        "summary": safe_event_summary(
            payload["event_type"], payload["tool_name"], payload["agent_id"], payload["error_type"]
        ),
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "detail": payload["safe_data"],
    }


def redacted_game_state(state: GameState) -> dict[str, Any]:
    """Project a GameState to the character/office subset needed by Replay."""
    data = state.model_dump(mode="json", by_alias=True)
    boss = data.get("boss") or {}
    boss["currentTask"] = None
    boss["bubble"] = None
    for agent in data.get("agents", []):
        agent["currentTask"] = None
        agent["bubble"] = None
    data["history"] = []
    data["conversation"] = []
    data["todos"] = []
    whiteboard = data.get("whiteboardData") or {}
    whiteboard["kanbanTasks"] = []
    whiteboard["newsItems"] = []
    whiteboard["fileEdits"] = {}
    whiteboard["backgroundTasks"] = []
    data["whiteboardData"] = whiteboard
    return data


def normalized_event_from_record(record: EventRecord) -> AnyEvent | None:
    """Parse a legacy/raw event internally without returning its payload."""
    try:
        return EventAdapter.validate_python(
            {
                "event_type": EventType(record.event_type),
                "session_id": record.session_id,
                "timestamp": record.timestamp,
                "data": record.data or {},
            }
        )
    except Exception:
        return None


def normalized_event_from_replay_record(record: ReplayEventRecord) -> AnyEvent | None:
    """Rebuild the common event contract from a privacy-safe Replay row.

    Replay rows intentionally do not contain prompt, command, or output
    bodies.  State reconstruction only needs the allow-listed lifecycle
    metadata, so this fallback keeps chunked Replay independent from the
    legacy LIVE payload table (and also handles synthetic Replay rows).
    """
    detail = dict(record.safe_data or {})
    data: dict[str, Any] = {
        "agent_id": record.agent_id,
        "agent_name": record.agent_name,
        "agent_type": record.agent_type,
        "source": record.source or detail.get("source"),
        "model": record.model or detail.get("model"),
        "tool_name": record.tool_name,
        "tool_use_id": record.tool_use_id,
        "error_type": record.error_type,
        "project_name": record.project_name or detail.get("projectName"),
        "restored": detail.get("restored", False),
    }
    try:
        return EventAdapter.validate_python(
            {
                "event_type": EventType(record.event_type),
                "session_id": record.session_id,
                "timestamp": record.timestamp,
                "data": data,
            }
        )
    except Exception:
        return None
