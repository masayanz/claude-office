"""Tests for the Manager's short-lived Codex restore API client."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import pytest

from manager.process_manager import (
    CodexRestoreStatus,
    GlobalHooksRepairResult,
    ServiceManager,
    ServiceStatus,
)


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
        lambda: {
            "backend_host": "127.0.0.1",
            "backend_port": 8123,
            "frontend_host": "127.0.0.1",
            "frontend_port": 3123,
        },
    )
    manager.processes = {}
    manager._log_streams = {}
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


def test_integration_status_uses_shared_backend_port(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    seen: list[str] = []

    def open_url(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request.full_url)
        return _Response(
            {
                "backend": "ok",
                "codex": {
                    "live_event_count": 4,
                    "last_live_event_at": "2026-08-12T00:00:04Z",
                    "restored_sessions": 1,
                },
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_url)

    status = manager.codex_integration_status()

    assert seen == ["http://127.0.0.1:8123/api/v1/system/integration-status"]
    assert status.reachable is True
    assert status.live_event_count == 4
    assert status.restored_sessions == 1


def test_repair_hooks_uses_argument_list_and_never_controls_vscode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    installer = tmp_path / "codex-adapter" / "install-global-hooks.ps1"
    installer.parent.mkdir()
    installer.write_text("# test", encoding="utf-8")
    seen: list[object] = []

    monkeypatch.setattr("manager.process_manager.ROOT", tmp_path)
    monkeypatch.setattr("manager.process_manager.which", lambda name: "powershell.exe")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend([command, kwargs])
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    result = manager.repair_global_hooks()

    assert result == GlobalHooksRepairResult(True, "Codex global hooksを修復しました")
    command = seen[0]
    assert isinstance(command, list)
    assert command[:5] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert command[0].lower() == "powershell.exe"
    assert seen[1] | {
        "cwd": tmp_path,
        "capture_output": True,
        "check": False,
        "text": True,
        "timeout": 30,
    } == seen[1]
    if os.name == "nt":
        assert seen[1]["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(os.name != "nt", reason="Windows console startup flags")
def test_start_hides_console_and_keeps_service_log_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    captured: dict[str, Any] = {}

    class Process:
        pid = 4567

        def poll(self) -> None:
            return None

    def popen(command: list[str], **kwargs: Any) -> Process:
        captured["command"] = command
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr("manager.process_manager.LOG_DIR", tmp_path)
    monkeypatch.setattr(manager, "status", lambda service: ServiceStatus(service, False, False))
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", popen)

    result = manager.start("backend")

    assert result.pid == 4567
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.STDOUT
    assert captured["stdout"] is manager._log_streams["backend"]
    assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert captured["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert captured["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert (tmp_path / "backend.log").exists()


def test_adapter_self_check_uses_shared_backend_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    hook = tmp_path / "codex-adapter" / "hook.py"
    hook.parent.mkdir()
    hook.write_text("", encoding="utf-8")
    monkeypatch.setattr("manager.process_manager.ROOT", tmp_path)
    monkeypatch.setattr("manager.process_manager.os.name", "nt")
    monkeypatch.setattr("manager.process_manager.which", lambda _name: "py.exe")
    monkeypatch.setattr(
        manager,
        "_settings",
        lambda: {"backend_host": "127.0.0.1", "backend_port": 8123},
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["py.exe", "-3.13", str(hook), "--check"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "settings_loaded": True,
                    "endpoint": {
                        "host": "127.0.0.1",
                        "port": 8123,
                        "path": "/api/v1/events",
                        "loopback": True,
                    },
                    "python": {"supported": True},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert manager.adapter_self_check() is True


def test_adapter_self_check_detects_old_backend_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    hook = tmp_path / "codex-adapter" / "hook.py"
    hook.parent.mkdir()
    hook.write_text("", encoding="utf-8")
    monkeypatch.setattr("manager.process_manager.ROOT", tmp_path)
    monkeypatch.setattr("manager.process_manager.os.name", "nt")
    monkeypatch.setattr("manager.process_manager.which", lambda _name: "py.exe")
    monkeypatch.setattr(
        manager,
        "_settings",
        lambda: {"backend_host": "127.0.0.1", "backend_port": 8123},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "settings_loaded": True,
                    "endpoint": {
                        "host": "127.0.0.1",
                        "port": 8000,
                        "path": "/api/v1/events",
                        "loopback": True,
                    },
                    "python": {"supported": True},
                }
            ),
            stderr="",
        ),
    )
    assert manager.adapter_self_check() is False


@pytest.mark.parametrize("section", ["office", "board"])
def test_open_web_settings_uses_frontend_deep_link(
    monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    manager = _manager(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)

    manager.open_web_settings(section)

    assert opened == [f"http://127.0.0.1:3123?settings={section}"]


def test_open_web_settings_rejects_unknown_section(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)

    with pytest.raises(ValueError, match="種類"):
        manager.open_web_settings("unknown")


@pytest.mark.skipif(os.name != "nt", reason="Windows CTRL_BREAK fallback")
def test_stop_falls_back_when_ctrl_break_handle_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)

    class Process:
        terminated = False

        def poll(self) -> None:
            return None

        def send_signal(self, _signal: int) -> None:
            raise OSError(6, "The handle is invalid")

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            assert timeout == 5

    process = Process()
    manager.processes = {"backend": process}  # type: ignore[dict-item]
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(manager, "status", lambda service: service)

    assert manager.stop("backend") == "backend"
    assert process.terminated is True


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree shutdown")
def test_stop_terminates_the_entire_windows_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    commands: list[list[str]] = []

    class Process:
        pid = 43210

        def poll(self) -> None:
            return None

        def wait(self, timeout: int) -> None:
            assert timeout == 5

    process = Process()
    manager.processes = {"backend": process}  # type: ignore[dict-item]
    manager._log_streams = {}
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(manager, "status", lambda service: service)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command)
            or subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        ),
    )

    assert manager.stop("backend") == "backend"
    assert commands == [["taskkill.exe", "/PID", "43210", "/T", "/F"]]
