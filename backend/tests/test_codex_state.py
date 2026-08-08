"""Codex-specific state metadata without changing Claude/OpenCode behavior."""

from unittest.mock import AsyncMock

import pytest

from app.core.event_processor import _build_history_detail  # pyright: ignore[reportPrivateUsage]
from app.core.handlers import agent_handler
from app.core.state_machine import StateMachine
from app.models.agents import AgentState, BossState
from app.models.events import (
    AgentEvent,
    AgentEventData,
    EventType,
    SessionEvent,
    SessionEventData,
    ToolEvent,
    ToolEventData,
)


def _session_start(*, source: str | None = None, model: str | None = None) -> SessionEvent:
    return SessionEvent(
        event_type=EventType.SESSION_START,
        session_id="codex-session",
        data=SessionEventData(source=source, model=model),
    )


def _subagent_start(agent_id: str, *, source: str = "codex") -> AgentEvent:
    return AgentEvent(
        event_type=EventType.SUBAGENT_START,
        session_id="codex-session",
        data=AgentEventData(
            source=source,
            model="gpt-5.6-sol",
            agent_id=agent_id,
            agent_type="default",
            agent_name="untrusted adapter display name",
        ),
    )


def _tool_event(event_type: EventType, tool_name: str, agent_id: str | None = None) -> ToolEvent:
    return ToolEvent(
        event_type=event_type,  # type: ignore[arg-type]
        session_id="codex-session",
        data=ToolEventData(source="codex", tool_name=tool_name, agent_id=agent_id),
    )


def test_codex_session_sets_main_identity_and_model() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex", model="gpt-5.6-sol"))

    boss = sm.to_game_state("codex-session").boss
    assert boss.name == "Codex Main"
    assert boss.source == "codex"
    assert boss.model == "gpt-5.6-sol"
    assert boss.agent_type == "main"


def test_non_codex_session_keeps_legacy_empty_identity() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex", model="gpt-5.6-sol"))
    sm.transition(_session_start())

    boss = sm.to_game_state("claude-session").boss
    assert boss.name is None
    assert boss.source is None
    assert boss.model is None
    assert boss.agent_type is None


def test_codex_subagent_names_are_stable_and_monotonic() -> None:
    sm = StateMachine()
    sm.transition(_subagent_start("agent-a"))
    sm.transition(_subagent_start("agent-b"))

    assert sm.agents["agent-a"].name == "Codex Agent 1"
    assert sm.agents["agent-b"].name == "Codex Agent 2"
    assert sm.agents["agent-a"].source == "codex"
    assert sm.agents["agent-a"].model == "gpt-5.6-sol"
    assert sm.agents["agent-a"].agent_type == "default"
    assert sm.agents["agent-a"].current_task is None

    sm.remove_agent("agent-a")
    sm.transition(_subagent_start("agent-c"))
    assert sm.agents["agent-c"].name == "Codex Agent 3"

    sm.transition(_subagent_start("agent-a"))
    assert sm.agents["agent-a"].name == "Codex Agent 1"


def test_non_codex_subagent_keeps_existing_naming_path() -> None:
    sm = StateMachine()
    sm.transition(_subagent_start("agent-a", source="claude"))

    assert sm.agents["agent-a"].name != "Codex Agent 1"
    assert sm.agents["agent-a"].source == "claude"


def test_main_agent_wait_uses_reviewing_state() -> None:
    sm = StateMachine()
    sm.transition(_tool_event(EventType.PRE_TOOL_USE, "AgentWait"))

    assert sm.boss_state == BossState.REVIEWING
    assert sm.boss_bubble is not None
    assert sm.boss_bubble.text == "Waiting for agents..."

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "AgentWait"))
    assert sm.boss_state == BossState.IDLE


def test_subagent_agent_wait_round_trips_to_working() -> None:
    sm = StateMachine()
    sm.transition(_subagent_start("agent-a"))
    sm.transition(_tool_event(EventType.PRE_TOOL_USE, "AgentWait", "agent-a"))
    assert sm.agents["agent-a"].state == AgentState.WAITING

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "AgentWait", "agent-a"))
    assert sm.agents["agent-a"].state == AgentState.WORKING


def test_unrelated_post_tool_does_not_revive_departing_agent() -> None:
    sm = StateMachine()
    sm.transition(_subagent_start("agent-a"))
    sm.agents["agent-a"].state = AgentState.WAITING

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "Bash", "agent-a"))

    assert sm.agents["agent-a"].state == AgentState.WAITING


def test_codex_metadata_is_available_in_frontend_history_detail() -> None:
    event = ToolEvent(
        event_type=EventType.PRE_TOOL_USE,
        session_id="codex-session",
        data=ToolEventData(
            source="codex",
            model="gpt-5.6-sol",
            agent_type="default",
            tool_name="AgentWait",
        ),
    )

    assert _build_history_detail(event) == {
        "toolName": "AgentWait",
        "agentType": "default",
        "source": "codex",
        "model": "gpt-5.6-sol",
    }


@pytest.mark.asyncio
async def test_codex_name_skips_summary_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = StateMachine()
    event = _subagent_start("agent-a")
    sm.transition(event)
    enrich = AsyncMock()
    broadcast = AsyncMock()
    update_state = AsyncMock()
    monkeypatch.setattr(agent_handler, "enrich_agent_with_summaries", enrich)
    monkeypatch.setattr(agent_handler, "broadcast_state", broadcast)

    await agent_handler.handle_subagent_start(sm, event, lambda: None, update_state)

    enrich.assert_not_awaited()
    assert sm.agents["agent-a"].name == "Codex Agent 1"
    update_state.assert_awaited_once_with("codex-session", "agent-a", AgentState.WALKING_TO_DESK)
