"""Codex-specific state metadata without changing Claude/OpenCode behavior."""

from unittest.mock import AsyncMock

import pytest

from app.core import state_machine
from app.core.event_processor import _build_history_detail  # pyright: ignore[reportPrivateUsage]
from app.core.handlers import agent_handler
from app.core.state_machine import StateMachine
from app.models.agents import AgentState, BossState
from app.models.events import (
    AgentEvent,
    AgentEventData,
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


def _prompt() -> PromptEvent:
    return PromptEvent(
        event_type=EventType.USER_PROMPT_SUBMIT,
        session_id="codex-session",
        data=PromptEventData(source="codex", model="gpt-5.6-sol"),
    )


def _lifecycle(event_type: EventType) -> LifecycleEvent:
    return LifecycleEvent(
        event_type=event_type,  # type: ignore[arg-type]
        session_id="codex-session",
        data=LifecycleEventData(source="codex"),
    )


def _session_end() -> SessionEvent:
    return SessionEvent(
        event_type=EventType.SESSION_END,
        session_id="codex-session",
        data=SessionEventData(source="codex"),
    )


def test_codex_session_sets_main_identity_and_model() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex", model="gpt-5.6-sol"))

    boss = sm.to_game_state("codex-session").boss
    assert boss.name == "Codex Main"
    assert boss.source == "codex"
    assert boss.model == "gpt-5.6-sol"
    assert boss.agent_type == "main"
    assert boss.state == BossState.IDLE


def test_codex_main_is_visible_and_moves_without_subagents() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex", model="gpt-5.6-sol"))
    assert sm.to_game_state("codex-session").boss.name == "Codex Main"

    sm.transition(_prompt())
    assert sm.boss_state == BossState.THINKING
    assert sm.turn_active is True

    sm.transition(_tool_event(EventType.PRE_TOOL_USE, "Bash"))
    assert sm.boss_state == BossState.WORKING
    assert sm.boss_last_tool_name == "Bash"

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "Bash"))
    assert sm.boss_state == BossState.THINKING

    sm.transition(_lifecycle(EventType.STOP))
    assert sm.boss_state == BossState.COMPLETED
    assert sm.turn_active is False

    sm.transition(_session_end())
    assert sm.boss_state == BossState.IDLE
    assert sm.boss_name == "Codex Main"


def test_codex_main_reviewing_during_agent_wait_and_subagent_activity() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex"))
    sm.transition(_prompt())
    sm.transition(_tool_event(EventType.PRE_TOOL_USE, "AgentWait"))
    assert sm.boss_state == BossState.REVIEWING

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "AgentWait"))
    assert sm.boss_state == BossState.REVIEWING

    sm.transition(_subagent_start("agent-a"))
    assert sm.boss_state == BossState.REVIEWING
    assert sm.agents["agent-a"].state == AgentState.ARRIVING

    sm.transition(
        AgentEvent(
            event_type=EventType.SUBAGENT_STOP,
            session_id="codex-session",
            data=AgentEventData(agent_id="agent-a", source="codex"),
        )
    )
    assert sm.boss_state == BossState.THINKING


def test_codex_main_error_is_explicit() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex"))
    sm.transition(_prompt())
    sm.transition(_lifecycle(EventType.ERROR))
    assert sm.boss_state == BossState.ERROR
    assert sm.turn_active is False


def test_non_codex_session_keeps_legacy_empty_identity() -> None:
    sm = StateMachine()
    sm.transition(_session_start(source="codex", model="gpt-5.6-sol"))
    sm.transition(_session_start())

    boss = sm.to_game_state("claude-session").boss
    assert boss.name == "AI Main"
    assert boss.source is None
    assert boss.model is None
    assert boss.agent_type is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("codex", "Codex Main"),
        ("claude", "Claude Main"),
        ("claude-code", "Claude Main"),
        ("claude_code", "Claude Main"),
        ("opencode", "OpenCode Main"),
        ("other", "AI Main"),
        (None, "AI Main"),
    ],
)
def test_main_name_comes_only_from_session_source_metadata(
    source: str | None, expected: str
) -> None:
    sm = StateMachine()
    sm.transition(_session_start(source=source))
    assert sm.boss_source == source
    assert sm.boss_name == expected


def test_custom_main_name_applies_to_each_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        state_machine,
        "load_settings",
        lambda: (
            {
                "main_agent_name_mode": "custom",
                "main_agent_custom_name": "Office AI",
            },
            None,
        ),
    )
    sm = StateMachine()
    sm.transition(_session_start(source="claude"))
    assert sm.boss_name == "Office AI"
    assert sm.to_game_state("claude-session").boss.name == "Office AI"


def test_subagent_stop_enters_leaving_before_cleanup() -> None:
    sm = StateMachine()
    sm.transition(_subagent_start("agent-a"))
    sm.transition(
        AgentEvent(
            event_type=EventType.SUBAGENT_STOP,
            session_id="codex-session",
            data=AgentEventData(agent_id="agent-a", source="codex"),
        )
    )
    assert sm.agents["agent-a"].state == AgentState.LEAVING
    assert sm.handin_queue == ["agent-a"]


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
    assert sm.boss_bubble is None
    assert sm.boss_last_tool_name == "AgentWait"

    sm.transition(_tool_event(EventType.POST_TOOL_USE, "AgentWait"))
    assert sm.boss_state == BossState.REVIEWING


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
