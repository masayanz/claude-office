"""Map allowlisted Codex hook metadata to Claude Office events."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

_EVENT_TYPES = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
}

_TOOL_NAMES = {
    "collaborationspawn_agent": "Agent",
    "collaborationwait_agent": "AgentWait",
}

_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _nonempty_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _agent_name(agent_id: str) -> str:
    """Create a stable display name without using prompt or task content."""
    compact_id = re.sub(r"[^A-Za-z0-9]", "", agent_id)
    suffix = compact_id[:8] or "unknown"
    return f"Codex Agent {suffix}"


def _tool_data(payload: Mapping[str, object]) -> dict[str, str]:
    data: dict[str, str] = {}
    tool_name = _nonempty_string(payload.get("tool_name"))
    if tool_name is not None:
        data["tool_name"] = _TOOL_NAMES.get(tool_name, tool_name)
    tool_use_id = _nonempty_string(payload.get("tool_use_id"))
    if tool_use_id is not None:
        data["tool_use_id"] = tool_use_id
    agent_id = _nonempty_string(payload.get("agent_id"))
    if agent_id is not None:
        data["agent_id"] = agent_id
    return data


def _event_data(hook_name: str, payload: Mapping[str, object]) -> dict[str, str]:
    if hook_name == "SessionStart":
        cwd = _nonempty_string(payload.get("cwd"))
        return {"working_dir": cwd} if cwd is not None else {}
    if hook_name == "UserPromptSubmit":
        return {"message": "Codex user prompt"}
    if hook_name in {"PreToolUse", "PostToolUse"}:
        return _tool_data(payload)
    if hook_name == "SubagentStart":
        data: dict[str, str] = {}
        agent_id = _nonempty_string(payload.get("agent_id"))
        if agent_id is not None:
            data["agent_id"] = agent_id
            data["agent_name"] = _agent_name(agent_id)
        agent_type = _nonempty_string(payload.get("agent_type"))
        if agent_type is not None:
            data["agent_type"] = agent_type
        return data
    if hook_name == "SubagentStop":
        data = {}
        agent_id = _nonempty_string(payload.get("agent_id"))
        if agent_id is not None:
            data["agent_id"] = agent_id
        agent_type = _nonempty_string(payload.get("agent_type"))
        if agent_type is not None:
            data["agent_type"] = agent_type
        return data
    return {}


def map_event(
    payload: object,
    *,
    received_at: datetime | None = None,
) -> dict[str, object] | None:
    """Return a sanitized Claude Office event, or ``None`` if it cannot be sent."""
    if not isinstance(payload, Mapping):
        return None
    typed_payload = cast("Mapping[str, object]", payload)

    hook_name = _nonempty_string(typed_payload.get("hook_event_name"))
    session_id = _nonempty_string(typed_payload.get("session_id"))
    if hook_name not in _EVENT_TYPES or session_id is None:
        return None
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return None

    timestamp = received_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    return {
        "event_type": _EVENT_TYPES[hook_name],
        "session_id": session_id,
        "timestamp": timestamp.astimezone(UTC).isoformat(),
        "data": _event_data(hook_name, typed_payload),
    }
