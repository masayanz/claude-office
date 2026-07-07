"""ARC-014: EventData discriminated-union contract tests.

These tests pin the wire-format equivalence between the legacy god-model
`Event`/`EventData` and the new `AnyEvent` discriminated union. They are the
load-bearing safety net for Batches 2 and 3 of the refactor: if the union
diverges from the legacy model on any field, these tests fail before the
legacy types are retired.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.events import (
    AgentEvent,
    AgentEventData,
    BackgroundTaskEvent,
    BackgroundTaskEventData,
    Event,
    EventAdapter,
    EventType,
    LifecycleEvent,
    PromptEvent,
    SessionEvent,
    TaskEvent,
    ToolEvent,
)

# Every EventType value must map to exactly one variant of the AnyEvent union.
# If a new EventType is added without extending the union, this dict is the
# place that needs updating.
_EVENT_TYPE_TO_VARIANT = {
    EventType.SESSION_START: SessionEvent,
    EventType.SESSION_END: SessionEvent,
    EventType.PRE_TOOL_USE: ToolEvent,
    EventType.POST_TOOL_USE: ToolEvent,
    EventType.USER_PROMPT_SUBMIT: PromptEvent,
    EventType.PERMISSION_REQUEST: ToolEvent,
    EventType.NOTIFICATION: LifecycleEvent,
    EventType.SUBAGENT_START: AgentEvent,
    EventType.SUBAGENT_INFO: AgentEvent,
    EventType.SUBAGENT_STOP: AgentEvent,
    EventType.AGENT_UPDATE: AgentEvent,
    EventType.STOP: LifecycleEvent,
    EventType.CLEANUP: AgentEvent,
    EventType.CONTEXT_COMPACTION: LifecycleEvent,
    EventType.REPORTING: LifecycleEvent,
    EventType.WALKING_TO_DESK: LifecycleEvent,
    EventType.WAITING: LifecycleEvent,
    EventType.LEAVING: LifecycleEvent,
    EventType.ERROR: LifecycleEvent,
    EventType.BACKGROUND_TASK_NOTIFICATION: BackgroundTaskEvent,
    EventType.TASK_CREATED: TaskEvent,
    EventType.TASK_COMPLETED: TaskEvent,
    EventType.TEAMMATE_IDLE: LifecycleEvent,
}


def test_every_event_type_is_covered_by_a_variant() -> None:
    """Each of the 23 EventType values must route to a known variant."""
    assert set(_EVENT_TYPE_TO_VARIANT) == set(EventType)


@pytest.mark.parametrize("et", list(EventType))
def test_event_adapter_accepts_every_event_type(et: EventType) -> None:
    """The discriminator must route every EventType value to a variant.

    Producers (hooks, plugin, scenarios) emit `{"event_type": <str>, ...}`;
    the union must accept every member of the enum with a minimal payload.
    """
    payload: dict[str, Any] = {"event_type": et, "session_id": "s1", "data": {}}
    event = EventAdapter.validate_python(payload)
    assert event.event_type == et
    assert isinstance(event, _EVENT_TYPE_TO_VARIANT[et])


def test_event_adapter_rejects_unknown_event_type() -> None:
    """Unknown event_type values must be rejected (not silently dropped)."""
    with pytest.raises(ValidationError):
        EventAdapter.validate_python(
            {"event_type": "not_a_real_event_type", "session_id": "s1", "data": {}}
        )


def test_event_adapter_rejects_bad_session_id() -> None:
    """session_id validator on _EventBase mirrors the legacy Event validator."""
    with pytest.raises(ValidationError):
        EventAdapter.validate_python(
            {"event_type": EventType.SESSION_START, "session_id": "has spaces!", "data": {}}
        )


# A representative payload per family. These exercise every field placed on a
# family payload class (and thereby every field not on EventDataBase). The
# round-trip test below uses these to prove wire-format parity with Event.
_FAMILY_SAMPLES: dict[str, dict[str, Any]] = {
    "session": {
        "event_type": EventType.SESSION_START,
        "data": {"reason": "fresh session", "task_list_id": "abc", "project_name": "p"},
    },
    "tool": {
        "event_type": EventType.POST_TOOL_USE,
        "data": {
            "tool_name": "Edit",
            "tool_use_id": "toolu_01",
            "tool_input": {"file_path": "/tmp/x.py"},
            "success": True,
            "result_summary": "edited",
            "error_type": None,
            "thinking": "planning the edit",
        },
    },
    "prompt": {
        "event_type": EventType.USER_PROMPT_SUBMIT,
        "data": {"prompt": "hello world"},
    },
    "agent": {
        "event_type": EventType.SUBAGENT_INFO,
        "data": {
            "agent_name": "researcher",
            "agent_type": "general-purpose",
            "task_description": "find prior art",
            "result_summary": "done",
            "tool_use_id": "toolu_02",
            "thinking": "thinking",
            "bubble_content": {
                "type": "speech",
                "text": "hi",
                "icon": None,
                "persistent": False,
            },
            "speech_content": {"boss": None, "agent": "hi", "boss_phone": None},
            # agent_transcript_path lives on EventDataBase (token_tracker).
            "agent_transcript_path": "/tmp/agent.jsonl",
        },
    },
    "lifecycle": {
        "event_type": EventType.NOTIFICATION,
        "data": {
            "notification_type": "info",
            "error_type": None,
            "reason": "idle",
            "bubble_content": None,
            "speech_content": None,
        },
    },
    "task": {
        "event_type": EventType.TASK_CREATED,
        "data": {"task_id": "tas_123", "task_subject": "ship it"},
    },
    "background_task": {
        "event_type": EventType.BACKGROUND_TASK_NOTIFICATION,
        "data": {
            "background_task_id": "bg_1",
            "background_task_output_file": "/tmp/out.log",
            "background_task_status": "completed",
            "background_task_summary": "ok",
        },
    },
}


@pytest.mark.parametrize("family", list(_FAMILY_SAMPLES), ids=list(_FAMILY_SAMPLES))
def test_union_round_trips_identical_to_legacy_event(family: str) -> None:
    """Wire-format parity between AnyEvent and the legacy god-model Event.

    The union's payload carries only its family's fields (per ARC-014), so its
    `model_dump` is a *subset* of the legacy 40-field dump. What must not
    change is the value of any field that appears in both, and no new field
    may appear. Concretely:

      - Envelope (event_type, session_id, timestamp) is identical.
      - Every key in the union's `data` exists in legacy `data` with the same
        value (no renames, no value edits, no new fields).
      - Input parity: the same JSON document validates against both models
        (producers' payloads keep working unmodified).
    """
    data_payload = _FAMILY_SAMPLES[family]["data"]
    envelope: dict[str, Any] = {
        "event_type": _FAMILY_SAMPLES[family]["event_type"],
        "session_id": "s1",
        "timestamp": "2025-01-01T00:00:00Z",
        "data": data_payload,
    }

    legacy = Event.model_validate(envelope).model_dump(mode="json")
    unionized = EventAdapter.validate_python(envelope).model_dump(mode="json")

    # Envelope identical (everything except `data`).
    legacy_envelope = {k: v for k, v in legacy.items() if k != "data"}
    union_envelope = {k: v for k, v in unionized.items() if k != "data"}
    assert union_envelope == legacy_envelope

    # Union data is a subset of legacy data with matching values for the
    # overlap: every union key must exist in legacy with the same value.
    union_data = unionized["data"]
    legacy_data = legacy["data"]
    for key, value in union_data.items():
        assert key in legacy_data, f"union introduced unknown field {key!r}"
        assert legacy_data[key] == value, f"value mismatch for field {key!r}"

    # Inverse direction for fields the producer actually set: every key in the
    # input payload must be present (with the same value) in BOTH dumps.
    for key, value in data_payload.items():
        assert union_data.get(key) == value, f"union dropped input field {key!r}"
        assert legacy_data.get(key) == value, f"legacy dropped input field {key!r}"


def test_extra_payload_fields_are_ignored_not_rejected() -> None:
    """Hooks send fields opportunistically; payloads must use extra="ignore"."""
    payload = {
        "event_type": EventType.PRE_TOOL_USE,
        "session_id": "s1",
        "data": {"tool_name": "Read", "future_field": "no problem"},
    }
    # Must not raise.
    event = EventAdapter.validate_python(payload)
    assert isinstance(event, ToolEvent)
    # And the unknown field must not be retained on the model.
    assert "future_field" not in event.data.model_dump()


def test_agent_transcript_path_is_on_base_payload() -> None:
    """Regression guard: token_tracker.update_from_event reads
    agent_transcript_path on the slow path for ANY event type (not just
    AgentEvent family events). If a non-agent event's payload omits this
    attribute, the fast/slow token-read path raises AttributeError.

    The field therefore lives on EventDataBase, inherited by every variant.
    """
    # A PromptEvent (not an AgentEvent) carrying agent_transcript_path.
    event = EventAdapter.validate_python(
        {
            "event_type": EventType.USER_PROMPT_SUBMIT,
            "session_id": "s1",
            "data": {"agent_transcript_path": "/tmp/agent.jsonl"},
        }
    )
    assert isinstance(event, PromptEvent)
    # Access must not raise (this is the line that breaks if the field is
    # mistakenly scoped to AgentEventData only).
    assert event.data.agent_transcript_path == "/tmp/agent.jsonl"


def test_payload_classes_inherit_extra_ignore() -> None:
    """Every payload class must keep extra="ignore" so producers keep working."""
    for payload_cls in (
        AgentEventData,
        BackgroundTaskEventData,
    ):
        assert payload_cls.model_config.get("extra") == "ignore", payload_cls


def test_timestamp_defaults_to_utc_now() -> None:
    """Family event models inherit the timestamp default from _EventBase."""
    before = datetime.now(UTC)
    event = EventAdapter.validate_python(
        {"event_type": EventType.STOP, "session_id": "s1", "data": {}}
    )
    after = datetime.now(UTC)
    assert before <= event.timestamp <= after
