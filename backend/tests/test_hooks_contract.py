"""ARC-010: Hooks -> backend event contract test.

Validates that ``hooks/src/claude_office_hooks/event_mapper.py::map_event()``
produces payloads the backend's ``AnyEvent`` discriminated union (ARC-014)
accepts. This is the contract-test half of ARC-010: the union (post-ARC-014)
is the canonical parser, so anything the hooks emit must round-trip through
``EventAdapter.validate_python`` without error.

Three classes of drift are caught:

1. Hooks emits an ``event_type`` the backend enum no longer carries (or that
   isn't routed to a family variant) -> ``EventAdapter.validate_python``
   raises ``ValidationError``.
2. A payload shape violates the family payload model (a renamed field, a
   wrong-typed value, a missing required field) -> raises.
3. ``map_event`` grows a new output ``event_type`` without a matching case
   here -> ``test_hooks_output_surface_is_covered`` fails because the
   observed set no longer equals the documented surface.

This is a contract test, not a behaviour test: it asserts only that whatever
``map_event`` produces is acceptable to the backend. Producer behaviour
(branch logic, field extraction) is exercised in ``hooks/tests/``.
"""
# Pyright can't resolve ``claude_office_hooks`` (loaded via runtime
# ``sys.path.insert`` below — the hooks package lives outside ``backend/``).
# The cast on ``map_event`` re-asserts its signature for the rest of the file.
# pyright: reportMissingImports=false, reportUnknownVariableType=false

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Make the hooks package importable from the backend venv.
#
# The hooks package is intentionally dependency-light (only ``defusedxml``,
# mirrored into this backend's dev deps for this test) and deliberately does
# NOT depend on the backend. Importing it as a peer via ``sys.path`` keeps
# that boundary intact: a backend install never needs the hooks package at
# runtime, only at test time. The insert runs at module load because pytest
# discovers test files by importing them.
# ---------------------------------------------------------------------------
_HOOKS_SRC = Path(__file__).resolve().parents[2] / "hooks" / "src"
if str(_HOOKS_SRC) not in sys.path:
    sys.path.insert(0, str(_HOOKS_SRC))

from claude_office_hooks.event_mapper import (  # noqa: E402
    map_event as _untyped_map_event,
)
from pydantic import ValidationError  # noqa: E402

from app.models.events import EventAdapter  # noqa: E402

# Pyright can't follow the runtime ``sys.path.insert`` above, so without help
# it treats ``map_event`` as Unknown and the whole file goes red under strict
# mode. Re-assert its signature here as a typed alias. If the real mapper
# signature drifts, ``hooks/tests/test_event_mapper.py`` catches that; this
# contract test only cares that whatever it returns validates against AnyEvent.
map_event: Callable[..., dict[str, Any] | None] = cast(
    Callable[..., dict[str, Any] | None], _untyped_map_event
)

# ---------------------------------------------------------------------------
# Representative raw hook inputs.
#
# Each tuple is ``(hook_event_name, raw_stdin_dict, case_id)``. The set of
# OUTPUT event_types produced by these inputs MUST cover the full surface
# map_event() can emit (see ``_EXPECTED_HOOKS_OUTPUT_TYPES``). When you add
# a new output branch to ``map_event``, extend this list with a case AND, if
# the new type isn't already in ``_EXPECTED_HOOKS_OUTPUT_TYPES``, extend that
# set too. Otherwise ``test_hooks_output_surface_is_covered`` fails.
#
# Raw shapes mirror what Claude Code actually writes to a hook's stdin:
# ``session_id`` and ``cwd`` are present on most hooks; tool hooks add
# ``tool_name``/``tool_use_id``/``tool_input``/``tool_response``; the native
# subagent hooks carry ``agent_id``.
# ---------------------------------------------------------------------------
_RAW_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "session_start",
        {"session_id": "abc123", "cwd": "/tmp/proj", "source": "startup"},
        "session_start",
    ),
    ("session_end", {"session_id": "abc123", "reason": "exit"}, "session_end"),
    (
        "user_prompt_submit",
        {"session_id": "abc123", "prompt": "do the thing"},
        "user_prompt_submit",
    ),
    # <task-notification> XML in the prompt routes to background_task_notification.
    (
        "user_prompt_submit",
        {
            "session_id": "abc123",
            "prompt": "<task-notification><task-id>bg_1</task-id>"
            "<output-file>/tmp/out.log</output-file>"
            "<status>completed</status>"
            "<summary>done</summary></task-notification>",
        },
        "user_prompt_submit-to-background_task_notification",
    ),
    (
        "pre_tool_use",
        {
            "session_id": "abc123",
            "tool_name": "Read",
            "tool_use_id": "t_read",
            "tool_input": {"file_path": "/x"},
        },
        "pre_tool_use",
    ),
    # Task / Agent tool on pre_tool_use remaps to subagent_start.
    (
        "pre_tool_use",
        {
            "session_id": "abc123",
            "tool_name": "Task",
            "tool_use_id": "t_task",
            "tool_input": {
                "subagent_type": "general",
                "description": "research it",
                "prompt": "find prior art",
            },
        },
        "pre_tool_use-to-subagent_start",
    ),
    (
        "post_tool_use",
        {
            "session_id": "abc123",
            "tool_name": "Read",
            "tool_use_id": "t_read",
            "tool_response": "ok",
        },
        "post_tool_use",
    ),
    # Synchronous Task tool on post_tool_use remaps to subagent_stop. Response
    # must NOT carry ``agentId`` (that marks an async agent, which defers the
    # stop to the native SubagentStop hook).
    (
        "post_tool_use",
        {
            "session_id": "abc123",
            "tool_name": "Task",
            "tool_use_id": "t_task",
            "tool_response": {"content": [{"type": "text", "text": "done"}]},
        },
        "post_tool_use-to-subagent_stop",
    ),
    # Native SubagentStart hook -> subagent_info.
    (
        "subagent_start",
        {"session_id": "abc123", "agent_id": "sa_1", "agent_type": "general-purpose"},
        "subagent_start-to-subagent_info",
    ),
    # Native SubagentStop hook -> subagent_stop.
    ("subagent_stop", {"session_id": "abc123", "agent_id": "sa_1"}, "subagent_stop"),
    (
        "permission_request",
        {
            "session_id": "abc123",
            "tool_name": "Bash",
            "tool_use_id": "t_bash",
            "tool_input": {"command": "ls"},
        },
        "permission_request",
    ),
    (
        "notification",
        {"session_id": "abc123", "type": "permission", "message": "needs review"},
        "notification",
    ),
    ("stop", {"session_id": "abc123"}, "stop"),
    # pre_compact remaps to context_compaction.
    ("pre_compact", {"session_id": "abc123"}, "pre_compact-to-context_compaction"),
]


# The complete set of ``event_type`` strings ``map_event`` can emit. Every
# member must be a valid backend ``EventType`` value (the parametrized test
# enforces that). If the mapper grows a new output branch, add the type here
# AND add a case in ``_RAW_CASES`` exercising it.
_EXPECTED_HOOKS_OUTPUT_TYPES: frozenset[str] = frozenset(
    {
        "session_start",
        "session_end",
        "user_prompt_submit",
        "background_task_notification",
        "pre_tool_use",
        "subagent_start",
        "post_tool_use",
        "subagent_stop",
        "subagent_info",
        "permission_request",
        "notification",
        "stop",
        "context_compaction",
    }
)


@pytest.mark.parametrize(
    ("hook_name", "raw"),
    [(c[0], c[1]) for c in _RAW_CASES],
    ids=[c[2] for c in _RAW_CASES],
)
def test_map_event_output_validates_against_union(
    hook_name: str,
    raw: dict[str, Any],
) -> None:
    """Each ``map_event`` output must validate against the AnyEvent union."""
    payload = map_event(hook_name, raw, session_id="fallback_session")
    if payload is None:
        pytest.fail(f"map_event({hook_name!r}) returned None for a contract case")
    # Raises ValidationError on any contract violation: unknown event_type,
    # wrong-typed field, missing required field, session_id regex failure.
    EventAdapter.validate_python(payload)


def test_hooks_output_surface_is_covered() -> None:
    """Every OUTPUT event_type the mapper can emit is exercised by _RAW_CASES.

    Closes the "new output branch without a test case" gap. If ``map_event``
    gains a new output ``event_type``, the maintainer must add a case (and
    extend ``_EXPECTED_HOOKS_OUTPUT_TYPES`` only if the new type isn't already
    anticipated there); otherwise this assertion fails.
    """
    observed: set[str] = set()
    for hook_name, raw, _case_id in _RAW_CASES:
        payload = map_event(hook_name, raw, session_id="fallback_session")
        assert payload is not None, f"{hook_name} returned None unexpectedly"
        event_type = payload.get("event_type")
        assert isinstance(event_type, str), f"non-str event_type from {hook_name}"
        observed.add(event_type)

    expected = set(_EXPECTED_HOOKS_OUTPUT_TYPES)
    missing = expected - observed
    unexpected = observed - expected
    assert not missing and not unexpected, (
        "hooks output surface drift. "
        f"Missing from cases: {sorted(missing)}; "
        f"unexpected in cases: {sorted(unexpected)}"
    )


def test_unknown_hook_event_returns_none() -> None:
    """Unknown hook names are skipped (not emitted as an unknown event_type).

    Guards against a future change that emits an unmapped ``event_type``: the
    backend union would reject it, silently dropping every event from that
    hook. Better to fail at the producer.
    """
    payload = map_event(
        "totally_unknown_hook", {"session_id": "abc123"}, session_id="fallback_session"
    )
    assert payload is None


def test_fallback_session_id_validates() -> None:
    """The ``unknown_session`` fallback must satisfy the backend's session_id
    regex (``[a-zA-Z0-9_-]{1,128}``). If the fallback ever changes to
    something with a space or slash, every hook event from a missing-session
    context would 422 at the backend."""
    payload = map_event("stop", {}, session_id="")
    assert payload is not None
    EventAdapter.validate_python(payload)


def test_payload_rejected_by_union_fails() -> None:
    """Sanity check: a deliberately-broken payload must raise. If this test
    fails, ``EventAdapter.validate_python`` is no longer enforcing the union
    and the contract test above is meaningless."""
    bad_payload: dict[str, Any] = {
        "event_type": "not_a_real_event_type",
        "session_id": "abc123",
        "data": {},
    }
    with pytest.raises(ValidationError):
        EventAdapter.validate_python(bad_payload)
