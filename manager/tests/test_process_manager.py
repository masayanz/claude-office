"""Tests for the Manager's short-lived Codex restore API client."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from manager.process_manager import CodexRestoreStatus, ServiceManager


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _manager(monkeypatch: pytest.MonkeyPatch) -> ServiceManager:
    manager = object.__new__(ServiceManager)
    monkeypatch.setattr(
        manager,
        "_settings",
        lambda: {"backend_host": "127.0.0.1", "backend_port": 8123},
    )
    return manager


def test_manual_restore_posts_with_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    request_seen: list[tuple[urllib.request.Request, float]] = []

    def open_url(request: urllib.request.Request, timeout: float) -> _Response:
        request_seen.append((request, timeout))
        return _Response({"state": "checking"})

    monkeypatch.setattr(urllib.request, "urlopen", open_url)

    result = manager.restore_codex_sessions()

    request, timeout = request_seen[0]
    assert request.full_url == "http://127.0.0.1:8123/api/v1/codex/restore"
    assert request.method == "POST"
    assert timeout < 1
    assert result == CodexRestoreStatus(state="checking")


def test_restore_status_normalizes_backend_result(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(
            {"status": "completed", "restored_sessions": 2, "message": "完了"}
        ),
    )

    result = manager.codex_restore_status()

    assert result == CodexRestoreStatus(state="succeeded", session_count=2, detail="完了")


def test_explicit_api_key_is_forwarded_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    seen_key: list[str | None] = []
    monkeypatch.setenv("CLAUDE_OFFICE_API_KEY", "manager-test-key")

    def open_url(request: urllib.request.Request, timeout: float) -> _Response:
        seen_key.append(request.get_header("X-api-key"))
        return _Response({"state": "idle"})

    monkeypatch.setattr(urllib.request, "urlopen", open_url)

    manager.codex_restore_status()

    assert seen_key == ["manager-test-key"]


def test_invalid_backend_response_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)

    class InvalidResponse(_Response):
        def read(self) -> bytes:
            return b"[]"

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _request, timeout: InvalidResponse({}),
    )

    with pytest.raises(RuntimeError, match="不正な応答"):
        manager.codex_restore_status()
