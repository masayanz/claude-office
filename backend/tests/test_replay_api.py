"""Privacy and contract tests for the Viewer Replay API."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import ReplayEventRecord
from app.main import app

client = TestClient(app)


def _post_event(session_id: str, event_type: str, data: dict | None = None) -> None:
    response = client.post(
        "/api/v1/events",
        json={
            "event_type": event_type,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data or {},
        },
    )
    assert response.status_code == 200, response.text


def test_replay_api_only_returns_safe_metadata() -> None:
    session_id = f"replay-security-{uuid4()}"
    prompt_secret = "PROMPT_SECRET_SHOULD_NOT_APPEAR"
    input_secret = "TOOL_INPUT_SECRET_SHOULD_NOT_APPEAR"
    _post_event(
        session_id,
        "session_start",
        {"project_name": "Replay Demo", "source": "codex", "model": "gpt-test"},
    )
    _post_event(session_id, "user_prompt_submit", {"prompt": prompt_secret})
    _post_event(
        session_id,
        "pre_tool_use",
        {
            "tool_name": "Read",
            "tool_input": {"file_path": input_secret, "command": input_secret},
            "agent_id": "main",
        },
    )
    _post_event(session_id, "post_tool_use", {"tool_name": "Read", "agent_id": "main"})
    _post_event(session_id, "session_end")

    response = client.get(f"/api/v1/replay/sessions/{session_id}/events")
    assert response.status_code == 200, response.text
    payload = response.json()
    serialized = response.text
    assert prompt_secret not in serialized
    assert input_secret not in serialized
    assert payload
    assert all(
        set(item["event"]) == {"id", "type", "agentId", "summary", "timestamp", "detail"}
        for item in payload
    )
    assert all(item["state"]["history"] == [] for item in payload)
    assert all(item["state"]["conversation"] == [] for item in payload)
    assert all(item["state"]["todos"] == [] for item in payload)

    async def read_safe_rows() -> list[dict]:
        async for db in get_db():
            result = await db.execute(
                select(ReplayEventRecord).where(
                    ReplayEventRecord.session_id == session_id
                )
            )
            return [record.safe_data for record in result.scalars().all()]
        return []

    safe_rows = asyncio.run(read_safe_rows())
    assert safe_rows
    assert prompt_secret not in repr(safe_rows)
    assert input_secret not in repr(safe_rows)


def test_replay_session_list_and_delete_keep_live_session() -> None:
    session_id = f"replay-delete-{uuid4()}"
    _post_event(session_id, "session_start", {"project_name": "Delete Demo"})
    _post_event(session_id, "session_end")

    listing = client.get("/api/v1/replay/sessions", params={"project": "Delete Demo"})
    assert listing.status_code == 200, listing.text
    assert any(item["id"] == session_id for item in listing.json())
    storage = client.get("/api/v1/replay/storage")
    assert storage.status_code == 200, storage.text
    before_delete_count = storage.json()["eventCount"]
    assert before_delete_count >= 2

    deleted = client.delete(f"/api/v1/replay/sessions/{session_id}")
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/v1/replay/sessions/{session_id}").status_code == 404
    assert client.get("/api/v1/replay/storage").json()["eventCount"] == before_delete_count - 2
    # Deleting Replay metadata must not delete the LIVE restoration source.
    assert client.get(f"/api/v1/sessions/{session_id}/replay").status_code == 200


def test_replay_chunk_response_is_bounded_and_restores_main_and_agents() -> None:
    session_id = f"replay-chunk-{uuid4()}"
    _post_event(session_id, "session_start", {"source": "codex", "model": "gpt-test"})
    _post_event(session_id, "user_prompt_submit", {"source": "codex"})
    _post_event(
        session_id,
        "subagent_start",
        {"source": "codex", "agent_id": "agent-a", "agent_type": "subagent"},
    )
    _post_event(
        session_id,
        "subagent_start",
        {"source": "codex", "agent_id": "agent-b", "agent_type": "subagent"},
    )
    _post_event(
        session_id,
        "subagent_stop",
        {"source": "codex", "agent_id": "agent-a"},
    )
    _post_event(session_id, "stop", {"source": "codex"})

    first = client.get(
        f"/api/v1/replay/sessions/{session_id}/events",
        params={"offset": 0, "limit": 2},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert set(first_payload) >= {"items", "total", "nextOffset", "hasMore"}
    assert len(first_payload["items"]) == 2
    assert first_payload["total"] >= 6
    assert first_payload["nextOffset"] == 2
    assert first_payload["items"][0]["state"]["boss"]["name"] == "Codex Main"

    pages = list(first_payload["items"])
    offset = first_payload["nextOffset"]
    while first_payload["hasMore"]:
        response = client.get(
            f"/api/v1/replay/sessions/{session_id}/events",
            params={"offset": offset, "limit": 2},
        )
        assert response.status_code == 200, response.text
        first_payload = response.json()
        pages.extend(first_payload["items"])
        offset = first_payload["nextOffset"]

    agent_a_frame = next(
        item
        for item in pages
        if item["event"]["type"] == "subagent_start"
        and item["event"]["agentId"] == "agent-a"
    )
    agent_b_frame = next(
        item
        for item in pages
        if item["event"]["type"] == "subagent_start"
        and item["event"]["agentId"] == "agent-b"
    )
    assert {agent["id"] for agent in agent_a_frame["state"]["agents"]} == {"agent-a"}
    assert {agent["id"] for agent in agent_b_frame["state"]["agents"]} == {"agent-a", "agent-b"}
