from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.codex_integration import (
    CodexLiveTelemetry,
    get_codex_live_telemetry,
    reset_codex_live_telemetry,
)
from app.main import app
from app.models.events import EventAdapter

client = TestClient(app)


def _event(*, restored: bool = False, source: str = "codex", event_type: str = "pre_tool_use"):
    return EventAdapter.validate_python(
        {
            "event_type": event_type,
            "session_id": "codex-test",
            "timestamp": "2026-08-12T00:00:00Z",
            "data": {"source": source, "restored": restored, "tool_name": "Read"},
        }
    )


def test_live_codex_event_is_counted() -> None:
    telemetry = CodexLiveTelemetry(started_at=datetime(2026, 8, 12, tzinfo=UTC))
    received_at = datetime(2026, 8, 12, 0, 0, 5, tzinfo=UTC)

    assert telemetry.record(_event(), received_at=received_at) is True
    assert telemetry.live_event_count == 1
    assert telemetry.last_live_event_at == received_at


def test_restored_event_is_not_counted_as_live() -> None:
    telemetry = CodexLiveTelemetry(started_at=datetime(2026, 8, 12, tzinfo=UTC))

    assert telemetry.record(_event(restored=True)) is False
    assert telemetry.live_event_count == 0
    assert telemetry.last_live_event_at is None


def test_non_codex_or_non_lifecycle_event_is_not_counted() -> None:
    telemetry = CodexLiveTelemetry(started_at=datetime(2026, 8, 12, tzinfo=UTC))

    assert telemetry.record(_event(source="claude")) is False
    assert telemetry.record(_event(event_type="notification")) is False
    assert telemetry.live_event_count == 0


def test_integration_status_reports_live_receipt_separately_from_restore() -> None:
    reset_codex_live_telemetry(started_at=datetime(2026, 8, 12, tzinfo=UTC))
    response = client.post(
        "/api/v1/events",
        json={
            "event_type": "session_start",
            "session_id": "codex-live-api-test",
            "timestamp": "2026-08-12T00:00:00Z",
            "data": {"source": "codex"},
        },
    )
    assert response.status_code == 200

    status_response = client.get("/api/v1/system/integration-status")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["backend"] == "ok"
    assert payload["codex"]["live_event_count"] == 1
    assert payload["codex"]["last_live_event_at"] is not None
    assert "restored_sessions" in payload["codex"]
    assert get_codex_live_telemetry().live_event_count == 1
