"""Tests for event_mapper subagent event handling."""

from claude_office_hooks.event_mapper import map_event


SESSION_ID = "test-session-001"
TRANSCRIPT = "/home/user/.claude/projects/myproject/session.jsonl"


def _pre_tool_use_agent(tool_use_id: str = "tu_123") -> dict:
    return {
        "tool_name": "Agent",
        "tool_use_id": tool_use_id,
        "tool_input": {"prompt": "do something", "description": "Test Agent"},
        "session_id": SESSION_ID,
        "transcript_path": TRANSCRIPT,
    }


def _pre_tool_use_task(tool_use_id: str = "tu_456") -> dict:
    return {
        "tool_name": "Task",
        "tool_use_id": tool_use_id,
        "tool_input": {"prompt": "do something", "description": "Test Task"},
        "session_id": SESSION_ID,
        "transcript_path": TRANSCRIPT,
    }


def _post_tool_use_agent(
    tool_use_id: str = "tu_123",
    agent_id: str | None = "a5a60c7",
    run_in_background: bool = False,
) -> dict:
    tool_input: dict = {"prompt": "do something"}
    if run_in_background:
        tool_input["run_in_background"] = True
    resp = {}
    if agent_id is not None:
        resp["agentId"] = agent_id
    return {
        "tool_name": "Agent",
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
        "tool_response": resp,
        "session_id": SESSION_ID,
        "transcript_path": TRANSCRIPT,
    }


def _post_tool_use_task(tool_use_id: str = "tu_456") -> dict:
    return {
        "tool_name": "Task",
        "tool_use_id": tool_use_id,
        "tool_input": {"prompt": "do something"},
        "tool_response": {"content": "done"},
        "session_id": SESSION_ID,
        "transcript_path": TRANSCRIPT,
    }


class TestPreToolUseSubagentStart:
    """PreToolUse for Agent/Task tools should remap to subagent_start."""

    def test_agent_tool_creates_subagent_start(self) -> None:
        result = map_event("pre_tool_use", _pre_tool_use_agent(), SESSION_ID)
        assert result is not None
        assert result["event_type"] == "subagent_start"
        assert result["data"]["agent_id"] == "subagent_tu_123"

    def test_task_tool_creates_subagent_start(self) -> None:
        result = map_event("pre_tool_use", _pre_tool_use_task(), SESSION_ID)
        assert result is not None
        assert result["event_type"] == "subagent_start"
        assert result["data"]["agent_id"] == "subagent_tu_456"

    def test_extracts_description_and_prompt(self) -> None:
        result = map_event("pre_tool_use", _pre_tool_use_agent(), SESSION_ID)
        assert result is not None
        assert result["data"]["agent_name"] == "Test Agent"
        assert result["data"]["task_description"] == "do something"


class TestPostToolUseAgentAsync:
    """PostToolUse for Agent tool with agentId in response (async, v2.1+)."""

    def test_agent_with_agentId_does_not_send_subagent_stop(self) -> None:
        """Agent tool with agentId in response means agent is still running async."""
        result = map_event("post_tool_use", _post_tool_use_agent(), SESSION_ID)
        assert result is not None
        assert result["event_type"] == "post_tool_use"
        assert result["data"]["agent_id"] == "main"

    def test_agent_with_agentId_preserves_native_id(self) -> None:
        result = map_event("post_tool_use", _post_tool_use_agent(), SESSION_ID)
        assert result is not None
        assert result["data"]["native_agent_id"] == "a5a60c7"

    def test_agent_with_agentId_preserves_transcript_path(self) -> None:
        result = map_event("post_tool_use", _post_tool_use_agent(), SESSION_ID)
        assert result is not None
        assert result["data"]["agent_transcript_path"] is not None
        assert "subagents/agent-a5a60c7" in result["data"]["agent_transcript_path"]

    def test_agent_without_agentId_sends_subagent_stop(self) -> None:
        """Agent tool without agentId in response means sync completion (legacy)."""
        result = map_event(
            "post_tool_use", _post_tool_use_agent(agent_id=None), SESSION_ID
        )
        assert result is not None
        assert result["event_type"] == "subagent_stop"
        assert result["data"]["agent_id"] == "subagent_tu_123"


class TestPostToolUseTaskSync:
    """PostToolUse for Task tool (always sync, no agentId)."""

    def test_task_sends_subagent_stop(self) -> None:
        result = map_event("post_tool_use", _post_tool_use_task(), SESSION_ID)
        assert result is not None
        assert result["event_type"] == "subagent_stop"
        assert result["data"]["agent_id"] == "subagent_tu_456"


class TestPostToolUseBackground:
    """PostToolUse with run_in_background should not send subagent_stop."""

    def test_background_agent_sends_main_id(self) -> None:
        result = map_event(
            "post_tool_use",
            _post_tool_use_agent(run_in_background=True),
            SESSION_ID,
        )
        assert result is not None
        assert result["event_type"] == "post_tool_use"
        assert result["data"]["agent_id"] == "main"


class TestNativeSubagentStart:
    """Native SubagentStart hook should remap to subagent_info."""

    def test_native_subagent_start_maps_to_info(self) -> None:
        raw = {
            "agent_id": "a5a60c7",
            "agent_type": "general-purpose",
            "session_id": SESSION_ID,
            "transcript_path": TRANSCRIPT,
        }
        result = map_event("subagent_start", raw, SESSION_ID)
        assert result is not None
        assert result["event_type"] == "subagent_info"
        assert result["data"]["native_agent_id"] == "a5a60c7"

    def test_native_subagent_start_without_agent_id_returns_none(self) -> None:
        raw = {
            "session_id": SESSION_ID,
            "transcript_path": TRANSCRIPT,
        }
        result = map_event("subagent_start", raw, SESSION_ID)
        assert result is None


class TestNativeSubagentStop:
    """Native SubagentStop hook should pass through with native_agent_id."""

    def test_native_subagent_stop_includes_native_id(self) -> None:
        raw = {
            "agent_id": "a5a60c7",
            "session_id": SESSION_ID,
            "transcript_path": TRANSCRIPT,
        }
        result = map_event("subagent_stop", raw, SESSION_ID)
        assert result is not None
        assert result["event_type"] == "subagent_stop"
        assert result["data"]["native_agent_id"] == "a5a60c7"

    def test_native_subagent_stop_without_agent_id_returns_none(self) -> None:
        raw = {
            "session_id": SESSION_ID,
            "transcript_path": TRANSCRIPT,
        }
        result = map_event("subagent_stop", raw, SESSION_ID)
        assert result is None
