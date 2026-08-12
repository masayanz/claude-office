"""Unit tests for the Codex hook/rollout hybrid boundary."""

import asyncio
import json
from datetime import UTC, datetime

from app.core.codex_hybrid import HybridCoordinator
from app.core.codex_jsonl_tail import (
    CodexJsonlTailMonitor,
    JsonlTailReader,
    _RolloutMeta,
)
from app.models.events import EventAdapter, EventType


def test_jsonl_reader_keeps_unterminated_utf8_line_until_next_append() -> None:
    reader = JsonlTailReader()
    assert reader.feed('{"type":"event_msg","payload":"'.encode("utf-8")) == []
    records = reader.feed('秘密"}\n'.encode("utf-8"))
    assert records == [{"type": "event_msg", "payload": "秘密"}]
    assert reader.parse_errors == 0


def test_jsonl_reader_rejects_only_complete_malformed_lines() -> None:
    reader = JsonlTailReader()
    assert reader.feed(b"{broken\n") == []
    assert reader.parse_errors == 1
    assert reader.feed(b'{"ok":true}\n') == [{"ok": True}]


def test_hybrid_deduplicates_hook_and_jsonl_without_retaining_body() -> None:
    received = []

    class Processor:
        async def process_event(self, event):
            received.append(event)

    async def run() -> None:
        coordinator = HybridCoordinator()
        coordinator.bind(Processor())
        hook = EventAdapter.validate_python(
            {
                "event_type": "pre_tool_use",
                "session_id": "session-1",
                "timestamp": "2026-08-12T00:00:00Z",
                "data": {
                    "source": "codex",
                    "tool_name": "Bash",
                    "tool_use_id": "call-1",
                    "prompt": "SECRET_DO_NOT_FORWARD_123",
                },
            }
        )
        tail = EventAdapter.validate_python(
            {
                "event_type": "pre_tool_use",
                "session_id": "session-1",
                "timestamp": "2026-08-12T00:00:01Z",
                "data": {
                    "source": "codex",
                    "tool_name": "Bash",
                    "tool_use_id": "call-1",
                },
            }
        )
        assert await coordinator.process(hook, source="hook") is True
        assert await coordinator.process(tail, source="jsonl") is False

    asyncio.run(run())
    assert len(received) == 1
    assert "SECRET_DO_NOT_FORWARD_123" not in json.dumps(received[0].data.model_dump())
    assert received[0].event_type == EventType.PRE_TOOL_USE


def test_native_rollout_mapping_is_allowlisted() -> None:
    received = []

    class Processor:
        async def process_event(self, event):
            received.append(event)

    async def run() -> None:
        coordinator = HybridCoordinator()
        coordinator.bind(Processor())
        monitor = CodexJsonlTailMonitor()
        monitor._coordinator = coordinator
        meta = _RolloutMeta(
            session_id="session-2",
            thread_id="session-2",
            project_name="project",
        )
        await monitor._handle_record(
            type("Cursor", (), {"meta": meta})(),
            {
                "type": "response_item",
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": {
                    "type": "function_call",
                    "name": "Bash",
                    "call_id": "call-2",
                    "command": "SECRET_DO_NOT_FORWARD_123",
                },
            },
        )

    asyncio.run(run())
    assert len(received) == 1
    assert received[0].data.tool_name == "Bash"
    assert received[0].data.tool_input is None
    assert received[0].data.result_summary is None
