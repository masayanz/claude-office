"""Map allowlisted Codex hook metadata to AI Office Viewer events."""

import ntpath
import posixpath
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
_SAFE_METADATA_PATTERN = re.compile(r"[A-Za-z0-9._:/+\-]{1,128}\Z")
_SAFE_PROJECT_NAME_PATTERN = re.compile(r"[A-Za-z0-9._\-]{1,128}\Z")


def _nonempty_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _safe_metadata(value: object) -> str | None:
    """Accept short identifier-like metadata, never arbitrary text content."""
    text = _nonempty_string(value)
    if text is None or _SAFE_METADATA_PATTERN.fullmatch(text) is None:
        return None
    return text


def _project_name(payload: Mapping[str, object]) -> str | None:
    """Derive a safe, display-only project name from Codex's working directory."""
    cwd = _nonempty_string(payload.get("cwd"))
    if cwd is None:
        return None

    # Hooks run on Windows in the supported setup, but accepting both path
    # separators keeps mapper tests and copied configurations portable.
    trimmed = cwd.rstrip("/\\")
    basename = ntpath.basename(trimmed) or posixpath.basename(trimmed)
    if _SAFE_PROJECT_NAME_PATTERN.fullmatch(basename):
        return basename
    return None


def _base_data(payload: Mapping[str, object]) -> dict[str, str]:
    data = {"source": "codex"}
    project_name = _project_name(payload)
    if project_name is not None:
        data["project_name"] = project_name
    model = _safe_metadata(payload.get("model"))
    if model is not None:
        data["model"] = model
    return data


def _tool_data(payload: Mapping[str, object]) -> dict[str, str]:
    data = _base_data(payload)
    tool_name = _nonempty_string(payload.get("tool_name"))
    if tool_name is not None:
        data["tool_name"] = _TOOL_NAMES.get(tool_name, tool_name)
    tool_use_id = _nonempty_string(payload.get("tool_use_id"))
    if tool_use_id is not None:
        data["tool_use_id"] = tool_use_id
    agent_id = _nonempty_string(payload.get("agent_id"))
    if agent_id is not None:
        data["agent_id"] = agent_id
    agent_type = _safe_metadata(payload.get("agent_type"))
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def _event_data(hook_name: str, payload: Mapping[str, object]) -> dict[str, str]:
    data = _base_data(payload)
    if hook_name == "SessionStart":
        cwd = _nonempty_string(payload.get("cwd"))
        if cwd is not None:
            data["working_dir"] = cwd
        return data
    if hook_name == "UserPromptSubmit":
        data["message"] = "Codex user prompt"
        return data
    if hook_name in {"PreToolUse", "PostToolUse"}:
        return _tool_data(payload)
    if hook_name == "SubagentStart":
        agent_id = _nonempty_string(payload.get("agent_id"))
        if agent_id is not None:
            data["agent_id"] = agent_id
        agent_type = _safe_metadata(payload.get("agent_type"))
        if agent_type is not None:
            data["agent_type"] = agent_type
        return data
    if hook_name == "SubagentStop":
        agent_id = _nonempty_string(payload.get("agent_id"))
        if agent_id is not None:
            data["agent_id"] = agent_id
        agent_type = _safe_metadata(payload.get("agent_type"))
        if agent_type is not None:
            data["agent_type"] = agent_type
        return data
    return data


def map_event(
    payload: object,
    *,
    received_at: datetime | None = None,
) -> dict[str, object] | None:
    """Return a sanitized viewer event, or ``None`` if it cannot be sent."""
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
