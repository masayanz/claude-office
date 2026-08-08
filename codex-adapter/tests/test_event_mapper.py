from datetime import UTC, datetime

import pytest

from claude_office_codex_adapter.event_mapper import map_event

NOW = datetime(2026, 8, 8, 3, 4, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hook_name", "event_type"),
    [
        ("SessionStart", "session_start"),
        ("SessionEnd", "session_end"),
        ("UserPromptSubmit", "user_prompt_submit"),
        ("PreToolUse", "pre_tool_use"),
        ("PostToolUse", "post_tool_use"),
        ("SubagentStart", "subagent_start"),
        ("SubagentStop", "subagent_stop"),
        ("Stop", "stop"),
    ],
)
def test_maps_all_supported_hooks(hook_name: str, event_type: str) -> None:
    event = map_event(
        {"hook_event_name": hook_name, "session_id": "session-1"}, received_at=NOW
    )

    assert event is not None
    assert event["event_type"] == event_type
    assert event["session_id"] == "session-1"
    assert event["timestamp"] == "2026-08-08T03:04:05+00:00"


def test_session_start_only_maps_cwd() -> None:
    event = map_event(
        {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
            "cwd": "D:/safe/project",
            "model": "not-forwarded",
            "prompt": "secret",
        },
        received_at=NOW,
    )

    assert event is not None
    assert event["data"] == {"working_dir": "D:/safe/project"}


def test_user_prompt_uses_fixed_message_and_drops_content() -> None:
    event = map_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "prompt": "secret prompt",
            "input_messages": ["secret"],
        },
        received_at=NOW,
    )

    assert event is not None
    assert event["data"] == {"message": "Codex user prompt"}


@pytest.mark.parametrize(
    ("source_name", "mapped_name"),
    [
        ("collaborationspawn_agent", "Agent"),
        ("collaborationwait_agent", "AgentWait"),
        ("Bash", "Bash"),
    ],
)
def test_tool_metadata_and_name_normalization(source_name: str, mapped_name: str) -> None:
    event = map_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_name": source_name,
            "tool_use_id": "tool-1",
            "agent_id": "agent-1",
            "tool_input": {"command": "secret"},
            "tool_response": "secret",
        },
        received_at=NOW,
    )

    assert event is not None
    assert event["data"] == {
        "tool_name": mapped_name,
        "tool_use_id": "tool-1",
        "agent_id": "agent-1",
    }


def test_subagent_start_builds_safe_stable_name() -> None:
    event = map_event(
        {
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "agent_id": "abc-123456789",
            "agent_type": "default",
            "task": "secret task",
        },
        received_at=NOW,
    )

    assert event is not None
    assert event["data"] == {
        "agent_id": "abc-123456789",
        "agent_name": "Codex Agent abc12345",
        "agent_type": "default",
    }


def test_subagent_stop_maps_only_identity() -> None:
    event = map_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": "session-1",
            "agent_id": "agent-1",
            "agent_type": "default",
            "last_assistant_message": "secret",
        },
        received_at=NOW,
    )

    assert event is not None
    assert event["data"] == {"agent_id": "agent-1", "agent_type": "default"}


def test_unknown_hook_is_ignored() -> None:
    assert map_event({"hook_event_name": "Unknown", "session_id": "session-1"}) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hook_event_name": "Stop"},
        {"hook_event_name": "Stop", "session_id": ""},
        {"hook_event_name": "Stop", "session_id": "invalid session"},
    ],
)
def test_missing_or_invalid_required_fields_are_ignored(payload: object) -> None:
    assert map_event(payload) is None


@pytest.mark.parametrize("payload", [None, "bad", [], 1])
def test_malformed_payload_is_ignored(payload: object) -> None:
    assert map_event(payload) is None


def test_missing_optional_fields_are_allowed() -> None:
    event = map_event(
        {"hook_event_name": "PreToolUse", "session_id": "session-1"}, received_at=NOW
    )

    assert event is not None
    assert event["data"] == {}
