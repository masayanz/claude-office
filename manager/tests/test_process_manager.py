"""Tests for the Manager's short-lived Codex restore API client."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import manager.process_manager as process_manager
from manager.process_manager import (
    CodexRestoreStatus,
    PortProcessInfo,
    GlobalHooksRepairResult,
    ProcessProbe,
    ProcessRecord,
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
    manager._records = {}
    manager._states = {"backend": "stopped", "frontend": "stopped"}
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
    (installer.parent / "hook.py").write_text("# test", encoding="utf-8")
    seen: list[object] = []

    monkeypatch.setattr("manager.process_manager.ROOT", tmp_path)
    monkeypatch.setattr("manager.process_manager.which", lambda name: "powershell.exe")
    monkeypatch.setattr(
        manager,
        "inspect_global_hooks",
        lambda: SimpleNamespace(state=SimpleNamespace(value="error")),
    )

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


def test_repair_hooks_skips_a_healthy_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(
        manager,
        "inspect_global_hooks",
        lambda: SimpleNamespace(state=SimpleNamespace(value="ok")),
    )
    monkeypatch.setattr("manager.process_manager.LOG_DIR", tmp_path)

    def run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("正常な設定ではインストーラを起動しない")

    monkeypatch.setattr(subprocess, "run", run)

    result = manager.repair_global_hooks()

    assert result.succeeded is True
    assert result.detail == "Global Hooksは正常です。修復は必要ありません。"
    assert "stage=none" in (tmp_path / "manager.log").read_text(encoding="utf-8")


def test_repair_hooks_reports_parser_failure_and_logs_safe_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    installer = tmp_path / "codex-adapter" / "install-global-hooks.ps1"
    installer.parent.mkdir()
    installer.write_text("# test", encoding="utf-8")
    (installer.parent / "hook.py").write_text("# test", encoding="utf-8")
    monkeypatch.setattr("manager.process_manager.ROOT", tmp_path)
    monkeypatch.setattr("manager.process_manager.LOG_DIR", tmp_path)
    monkeypatch.setattr("manager.process_manager.which", lambda _name: "powershell.exe")
    monkeypatch.setattr(
        manager,
        "inspect_global_hooks",
        lambda: SimpleNamespace(state=SimpleNamespace(value="error")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ParserError token=secret-prompt",
        ),
    )

    result = manager.repair_global_hooks()

    assert result.succeeded is False
    assert result.failure_stage == "parse_hooks"
    assert result.returncode == 1
    assert result.detail == "Codex Global Hooks設定を読み込めませんでした。"
    log = (tmp_path / "manager.log").read_text(encoding="utf-8")
    assert "returncode=1" in log
    assert "stage=parse_hooks" in log
    assert "secret-prompt" not in log


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


def test_open_normal_browser_uses_the_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    result = manager.open_normal_browser()

    assert result.succeeded is True
    assert result.mode == "normal"
    assert opened == ["http://127.0.0.1:3123"]


def test_find_dedicated_browser_prefers_edge_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "PROGRAMW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setattr(process_manager, "which", lambda _name: None)

    assert ServiceManager._find_dedicated_browser() == ("Edge", str(edge))


def test_find_dedicated_browser_falls_back_to_chrome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "PROGRAMW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(process_manager, "which", lambda _name: None)

    assert ServiceManager._find_dedicated_browser() == ("Chrome", str(chrome))


def test_dedicated_view_uses_app_mode_and_reuses_its_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(
        manager,
        "_find_dedicated_browser",
        lambda: ("Edge", r"C:\\Program Files\\Microsoft\\Edge\\msedge.exe"),
    )
    monkeypatch.setattr(process_manager, "RUNTIME_DIR", tmp_path / "runtime")
    commands: list[list[str]] = []
    popen_kwargs: list[dict[str, Any]] = []

    class Process:
        pid = 4321

        def poll(self) -> None:
            return None

    process = Process()
    def launch(command: list[str], **kwargs: Any) -> Process:
        commands.append(command)
        popen_kwargs.append(kwargs)
        return process

    first = manager.open_dedicated_view((100, 200, 1600, 900), browser_launcher=launch)
    second = manager.open_dedicated_view((100, 200, 1600, 900), browser_launcher=launch)

    assert first.succeeded is True
    assert first.browser == "Edge"
    assert second.succeeded is True
    assert second.reused is True
    assert len(commands) == 1
    assert commands[0][0].endswith("msedge.exe")
    assert "--app=http://127.0.0.1:3123" in commands[0]
    assert "--new-window" in commands[0]
    assert "--window-size=1408,792" in commands[0]
    assert "--window-position=196,254" in commands[0]
    assert any(item.startswith("--user-data-dir=") for item in commands[0])
    assert popen_kwargs[0]["shell"] is False
    assert not (
        popen_kwargs[0].get("creationflags", 0)
        & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def test_dedicated_view_keeps_server_pids_and_lifecycle_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(
        manager,
        "_find_dedicated_browser",
        lambda: ("Edge", r"C:\\Program Files\\Microsoft\\Edge\\msedge.exe"),
    )
    monkeypatch.setattr(process_manager, "RUNTIME_DIR", tmp_path / "runtime")
    server_pids = {
        "backend": SimpleNamespace(pid=1111, poll=lambda: None),
        "frontend": SimpleNamespace(pid=2222, poll=lambda: None),
    }
    manager.processes.update(server_pids)
    lifecycle_calls: list[str] = []
    manager.start = lambda _service: lifecycle_calls.append("start")  # type: ignore[attr-defined]
    manager.stop = lambda _service: lifecycle_calls.append("stop")  # type: ignore[attr-defined]
    manager.restart = lambda _service: lifecycle_calls.append("restart")  # type: ignore[attr-defined]

    class BrowserProcess:
        pid = 3333

        def poll(self) -> None:
            return None

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: BrowserProcess()
    )

    assert result.succeeded is True
    assert lifecycle_calls == []
    assert manager._server_pid_snapshot() == {"backend": 1111, "frontend": 2222}
    assert set(manager._dedicated_view_processes) == {3333}


def test_dedicated_view_postcheck_logs_backend_pid_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    manager._dedicated_view_server_pids_before = {"backend": 1111, "frontend": 2222}
    manager.processes["backend"] = SimpleNamespace(pid=4444, poll=lambda: None)
    manager.processes["frontend"] = SimpleNamespace(pid=2222, poll=lambda: None)
    logged: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        manager,
        "_log_process_event",
        lambda service, event, detail="": logged.append((service, event, detail)),
    )

    manager.log_dedicated_view_postcheck()

    assert logged == [
        (
            "viewer",
            "dedicated_server_stopped_after_open",
            "ERROR Dedicated View起動後にBackendが停止しました "
            "backend_pid_before=1111 frontend_pid_before=2222 "
            "backend_pid_after=4444 frontend_pid_after=2222",
        )
    ]


def test_dedicated_view_reports_unavailable_frontend_or_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: False)
    unavailable = manager.open_dedicated_view()
    assert unavailable.succeeded is False
    assert "Frontend" in unavailable.detail

    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(manager, "_find_dedicated_browser", lambda: None)
    missing_browser = manager.open_dedicated_view()
    assert missing_browser.succeeded is False
    assert "Edge" in missing_browser.detail


def test_dedicated_browser_start_failure_does_not_touch_server_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(
        manager,
        "_find_dedicated_browser",
        lambda: ("Edge", r"C:\\Program Files\\Microsoft\\Edge\\msedge.exe"),
    )
    monkeypatch.setattr(process_manager, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        process_manager.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("browser unavailable")),
    )

    result = manager.open_dedicated_view()

    assert result.succeeded is False
    assert manager.processes == {}
    assert manager._dedicated_view_processes == {}


class _LongRunningBrowser:
    pid = 9876

    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("専用画面起動でbrowser.wait()を呼んではいけない")


def _dedicated_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ServiceManager:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(
        manager,
        "_find_dedicated_browser",
        lambda: ("Edge", r"C:\\Program Files\\Microsoft\\Edge\\msedge.exe"),
    )
    monkeypatch.setattr(process_manager, "RUNTIME_DIR", tmp_path / "runtime")
    return manager


def _tracked_servers(manager: ServiceManager) -> None:
    manager.processes.update(
        {
            "backend": SimpleNamespace(pid=1111, poll=lambda: None),
            "frontend": SimpleNamespace(pid=2222, poll=lambda: None),
        }
    )


def test_dedicated_view_does_not_start_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    lifecycle_calls: list[str] = []
    monkeypatch.setattr(
        manager, "start", lambda service: lifecycle_calls.append(f"start:{service}")
    )

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert lifecycle_calls == []


def test_dedicated_view_does_not_stop_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    lifecycle_calls: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda service: lifecycle_calls.append(f"stop:{service}"))

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert lifecycle_calls == []


def test_dedicated_view_does_not_restart_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    lifecycle_calls: list[str] = []
    monkeypatch.setattr(
        manager, "restart", lambda service: lifecycle_calls.append(f"restart:{service}")
    )

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert lifecycle_calls == []


def test_dedicated_view_does_not_change_backend_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    before = manager._server_pid_snapshot()

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert manager._server_pid_snapshot() == before == {"backend": 1111, "frontend": 2222}


def test_dedicated_view_keeps_backend_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    health_calls: list[str] = []

    def healthy(service: str) -> bool:
        health_calls.append(service)
        return True

    monkeypatch.setattr(manager, "_healthy", healthy)
    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert health_calls == ["frontend"]
    assert manager._healthy("backend") is True


def test_dedicated_view_browser_error_keeps_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    before = manager._server_pid_snapshot()

    def launch(*_args: object, **_kwargs: object) -> _LongRunningBrowser:
        raise FileNotFoundError("msedge.exe")

    result = manager.open_dedicated_view(browser_launcher=launch)

    assert result.succeeded is False
    assert manager._server_pid_snapshot() == before
    assert manager._dedicated_view_processes == {}


def test_dedicated_view_browser_exit_keeps_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    before = manager._server_pid_snapshot()
    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser(returncode=1)
    )

    assert result.succeeded is False
    assert manager._server_pid_snapshot() == before
    assert manager._dedicated_view_processes == {}


def test_dedicated_view_long_running_browser_does_not_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    browser = _LongRunningBrowser()
    elapsed = []
    monkeypatch.setattr(process_manager, "monotonic", lambda: 100.0)

    result = manager.open_dedicated_view(browser_launcher=lambda *_args, **_kwargs: browser)
    elapsed.append(browser.returncode)

    assert result.succeeded is True
    assert elapsed == [None]
    assert browser.poll() is None


def test_dedicated_view_launch_does_not_use_pipe_or_server_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    captured: dict[str, Any] = {}

    def launch(_command: list[str], **kwargs: Any) -> _LongRunningBrowser:
        captured.update(kwargs)
        return _LongRunningBrowser()

    result = manager.open_dedicated_view(browser_launcher=launch)

    assert result.succeeded is True
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert not (
        captured.get("creationflags", 0)
        & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def test_dedicated_view_does_not_trigger_restore_or_backfill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(manager, "restore_codex_sessions", lambda: calls.append("restore"))
    monkeypatch.setattr(
        manager,
        "backfill_replay_history",
        lambda: calls.append("backfill"),
        raising=False,
    )

    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )

    assert result.succeeded is True
    assert calls == []


def test_dedicated_view_heartbeat_keeps_pid_and_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _dedicated_manager(monkeypatch, tmp_path)
    _tracked_servers(manager)
    result = manager.open_dedicated_view(
        browser_launcher=lambda *_args, **_kwargs: _LongRunningBrowser()
    )
    assert result.succeeded is True

    samples: list[tuple[int | None, bool]] = []
    for _ in range(20):
        time.sleep(0.5)
        samples.append((manager._server_pid_snapshot()["backend"], manager._healthy("backend")))

    assert len(samples) == 20
    assert {pid for pid, _healthy in samples} == {1111}
    assert all(healthy for _pid, healthy in samples)


def test_service_commands_do_not_use_shell_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    backend, _ = manager._command("backend")
    frontend, _ = manager._command("frontend")

    assert backend[0].lower().endswith(("python.exe", "python"))
    assert "uv" not in backend[:2]
    assert frontend[0].lower().endswith(("node.exe", "node"))
    assert "cmd.exe" not in [item.lower() for item in frontend]
    assert "powershell" not in " ".join(frontend).lower()


def test_status_marks_healthy_external_port_as_not_manager_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(manager, "_port_in_use", lambda _service: True)

    result = manager.status("backend")

    assert result.running is True
    assert result.healthy is True
    assert result.owned is False
    assert result.state == "external"
    assert "停止しません" in result.detail


def test_portable_frontend_status_paths_preserve_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    backend = ServiceStatus("backend", True, True, 1234, "稼働中", True, "running")
    monkeypatch.setattr(manager, "_is_portable", lambda: True)

    def status_for_service(service: str) -> ServiceStatus:
        if service == "backend":
            return backend
        return ServiceManager.status(manager, service)

    monkeypatch.setattr(manager, "status", status_for_service)
    status = ServiceManager.snapshot(manager)["frontend"]

    monkeypatch.setattr(manager, "observe_status", lambda _service: backend)
    observed = ServiceManager.observe_status(manager, "frontend")

    monkeypatch.setattr(manager, "_ensure_runtime_state", lambda: None)
    stopped = ServiceManager.stop(manager, "frontend")

    assert status.name == "frontend"
    assert status.running is True
    assert "Backend内蔵の静的Frontend" in status.detail
    assert observed.name == "frontend"
    assert observed.running is True
    assert stopped.name == "frontend"
    assert stopped.running is False
    assert stopped.state == "stopped"


def test_observe_status_does_not_promote_starting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    manager._states["backend"] = "starting"
    manager.processes["backend"] = SimpleNamespace(pid=1234, poll=lambda: None)
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)

    result = manager.observe_status("backend")

    assert result.state == "starting"
    assert manager._states["backend"] == "starting"


def test_observe_status_rehydrates_verified_persisted_process_as_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    manager._records["backend"] = ProcessRecord(
        "backend",
        1234,
        r"C:\Python\python.exe",
        (r"C:\Python\python.exe", "-m", "uvicorn"),
        r"C:\viewer\backend",
        "2026-08-13T00:00:00+00:00",
        123.5,
        "manager-test",
        port=8123,
        backend_instance_id="backend-instance",
        database_identifier="database-id",
    )
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(manager, "_record_verification", lambda _record: "match")

    result = manager.observe_status("backend")

    assert result.running is True
    assert result.owned is True
    assert result.pid == 1234
    assert result.state == "running"
    assert manager._states["backend"] == "stopped"


def test_status_rehydrates_verified_persisted_process_as_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    manager._records["backend"] = ProcessRecord(
        "backend",
        1234,
        r"C:\Python\python.exe",
        (r"C:\Python\python.exe", "-m", "uvicorn"),
        r"C:\viewer\backend",
        "2026-08-13T00:00:00+00:00",
        123.5,
        "manager-test",
        port=8123,
        backend_instance_id="backend-instance",
        database_identifier="database-id",
    )
    monkeypatch.setattr(manager, "_healthy", lambda _service: True)
    monkeypatch.setattr(manager, "_record_matches", lambda _record: True)
    monkeypatch.setattr(manager, "_record_verification", lambda _record: "match")
    monkeypatch.setattr(
        manager,
        "_backend_identity",
        lambda: {
            "instance_id": "backend-instance",
            "database_identifier": "database-id",
        },
    )
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)

    result = manager.status("backend")

    assert result.running is True
    assert result.owned is True
    assert result.pid == 1234
    assert result.state == "running"
    assert manager._states["backend"] == "running"


def test_backend_health_requires_ai_office_viewer_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _url, timeout: _Response(
            {
                "status": "ok",
                "app": "ai-office-viewer",
                "component": "backend",
                "instance_id": "backend-instance",
                "database_identifier": "db-identifier",
            }
        ),
    )
    assert manager._healthy("backend") is True

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _url, timeout: _Response({"status": "ok", "app": "yomica"}),
    )
    assert manager._healthy("backend") is False


def test_identity_mismatch_never_adopts_a_busy_backend_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: False)
    monkeypatch.setattr(manager, "_port_in_use", lambda _service: True)

    result = manager.status("backend")

    assert result.state == "external"
    assert result.owned is False
    assert "別アプリケーション" in result.detail


def test_restart_does_not_start_after_stop_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    failed = ServiceStatus("backend", True, False, 4321, "停止未確認", True, "error")
    monkeypatch.setattr(manager, "stop", lambda _service: failed)
    started = False

    def start(_service: str) -> ServiceStatus:
        nonlocal started
        started = True
        return failed

    monkeypatch.setattr(manager, "start", start)

    result = manager.restart("backend")

    assert result.running is True
    assert result.state == "error"
    assert started is False


def test_pid_file_contains_identity_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    manager._records = {
        "backend": ProcessRecord(
            "backend",
            4321,
            r"C:\Python\python.exe",
            (r"C:\Python\python.exe", "-m", "uvicorn"),
            r"C:\viewer\backend",
            "2026-08-13T00:00:00+00:00",
            123.5,
            "manager-test",
            port=8001,
            backend_instance_id="backend-instance",
            database_identifier="database-id",
        )
    }
    path = Path(__file__).resolve().parents[2] / "runtime" / ".manager-test-processes.json"
    monkeypatch.setattr("manager.process_manager.PID_PATH", path)
    monkeypatch.setattr("manager.process_manager.RUNTIME_DIR", path.parent)
    try:
        manager._save_pid_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == 2
        assert payload["backend"]["executable"].endswith("python.exe")
        assert payload["backend"]["cwd"].endswith("backend")
        assert payload["backend"]["creation_time"] == 123.5
        assert payload["backend"]["port"] == 8001
        assert payload["backend"]["backend_instance_id"] == "backend-instance"
        assert payload["backend"]["database_identifier"] == "database-id"
    finally:
        path.unlink(missing_ok=True)


def test_access_denied_probe_is_not_treated_as_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ServiceManager,
        "_windows_process_probe",
        staticmethod(lambda _pid: ProcessProbe("unknown")),
    )

    assert ServiceManager._pid_state(4321) == "unknown"
    assert ServiceManager._pid_exists(4321) is False


def test_status_keeps_unverifiable_pid_record(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    record = ProcessRecord(
        "backend",
        4321,
        r"C:\Python\python.exe",
        (r"C:\Python\python.exe", "-m", "uvicorn"),
        r"C:\viewer\backend",
        "2026-08-13T00:00:00+00:00",
        123.5,
        "manager-test",
    )
    manager._records = {"backend": record}
    monkeypatch.setattr(manager, "_record_matches", lambda _record: False)
    monkeypatch.setattr(manager, "_record_verification", lambda _record: "unknown")
    monkeypatch.setattr(manager, "_healthy", lambda _service: False)
    monkeypatch.setattr(manager, "_port_in_use", lambda _service: False)

    result = manager.status("backend")

    assert result.state == "unknown"
    assert result.owned is False
    assert result.pid == 4321
    assert manager._records["backend"] == record


def test_stop_waits_for_tracked_child_after_root_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)

    class Process:
        pid = 4321

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> None:
            return None

    process = Process()
    monkeypatch.setattr(manager, "_extend_tracked_tree", lambda _pid, _tracked: None)
    monkeypatch.setattr(
        manager,
        "_pid_state",
        lambda pid: "alive" if pid == 9876 else "dead",
    )

    assert manager._wait_for_exit(process, 4321, 0.1, {9876}) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows CTRL_BREAK fallback")
def test_stop_falls_back_when_ctrl_break_handle_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)

    class Process:
        terminated = False
        alive = True
        pid = 43209

        def poll(self) -> int | None:
            return None if self.alive else 0

        def send_signal(self, _signal: int) -> None:
            raise OSError(6, "The handle is invalid")

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def wait(self, timeout: int) -> None:
            assert 0 < timeout <= 5
            self.alive = False

    process = Process()
    manager.processes = {"backend": process}  # type: ignore[dict-item]
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)

    result = manager.stop("backend")
    assert result.running is False
    assert process.terminated is True


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree shutdown")
def test_stop_terminates_the_entire_windows_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    commands: list[list[str]] = []

    class Process:
        pid = 43210
        alive = True

        def poll(self) -> int | None:
            return None if self.alive else 0

        def wait(self, timeout: int) -> None:
            assert 0 < timeout <= 5
            if self.alive:
                raise subprocess.TimeoutExpired("test", timeout)

    process = Process()
    manager.processes = {"backend": process}  # type: ignore[dict-item]
    manager._log_streams = {}
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-1] == "/F":
            process.alive = False
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    result = manager.stop("backend")
    assert result.running is False
    assert commands == [
        ["taskkill.exe", "/PID", "43210", "/T"],
        ["taskkill.exe", "/PID", "43210", "/T", "/F"],
    ]


def test_normal_stop_never_targets_an_external_port_occupant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_healthy", lambda _service: False)
    monkeypatch.setattr(manager, "_port_in_use", lambda _service: True)
    monkeypatch.setattr(
        manager,
        "_taskkill",
        lambda *_args, **_kwargs: pytest.fail("通常停止で外部PIDを終了してはいけない"),
    )

    result = manager.stop("backend")

    assert result.state == "external"
    assert result.owned is False


def test_configured_port_pid_detection_uses_get_nettcpconnection_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(process_manager.sys, "platform", "win32")
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="7101\n7101\n7102\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    assert manager._detect_listening_pids(8123) == [7101, 7102]
    assert commands[0][0].lower() == "powershell.exe"
    assert "Get-NetTCPConnection" in commands[0][-1]
    assert "-LocalPort 8123" in commands[0][-1]


def test_port_identity_rejects_pid_reuse_and_accepts_matching_manager_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    record = ProcessRecord(
        "backend",
        7050,
        r"C:\Python\python.exe",
        (r"C:\Python\python.exe", "-m", "uvicorn"),
        str(process_manager.ROOT / "backend"),
        "2026-08-13T00:00:00+00:00",
        123.5,
        "manager-test",
        port=8123,
    )
    manager._records["backend"] = record
    monkeypatch.setattr(manager, "_record_verification", lambda _record: "match")
    monkeypatch.setattr(
        process_manager.ServerLifecycleManager,
        "_windows_process_probe",
        staticmethod(lambda _pid: ProcessProbe("alive", r"C:\Python\python.exe", 123.5)),
    )

    matching = manager._port_process_info("backend", 7050)
    assert matching.identity_verified is True
    assert matching.manager_owned is True

    monkeypatch.setattr(manager, "_record_verification", lambda _record: "mismatch")
    reused = manager._port_process_info("backend", 7050)
    assert reused.identity_verified is False
    assert reused.identity == "AI Office Viewerと確認できません"


def test_emergency_stop_releases_backend_and_frontend_using_shared_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    ports = {8123: [7101], 3123: [7102]}
    infos = {
        ("backend", 7101): PortProcessInfo(
            "backend", "127.0.0.1", 8123, 7101, "python.exe", "backend"
        ),
        ("frontend", 7102): PortProcessInfo(
            "frontend", "127.0.0.1", 3123, 7102, "node.exe", "frontend"
        ),
    }
    monkeypatch.setattr(manager, "_port_pid_provider", lambda port: list(ports[port]))
    monkeypatch.setattr(
        manager,
        "_port_process_info",
        lambda service, pid: infos[(service, pid)],
    )
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(manager, "_log_process_event", lambda *_args: None)
    monkeypatch.setattr(
        manager,
        "_pid_state",
        lambda pid: "alive" if any(pid in values for values in ports.values()) else "dead",
    )
    calls: list[tuple[str, int]] = []

    def terminate(info: PortProcessInfo) -> tuple[bool, bool]:
        calls.append((info.service, info.pid))
        ports[info.port] = []
        return True, False

    monkeypatch.setattr(manager, "_emergency_terminate_pid", terminate)

    report = manager.emergency_stop(
        ("backend", "frontend"),
        expected_pids={"backend": (7101,), "frontend": (7102,)},
    )

    assert report.succeeded is True
    assert calls == [("backend", 7101), ("frontend", 7102)]
    assert report.results["backend"].released is True
    assert report.results["frontend"].released is True


def test_emergency_stop_does_not_kill_a_pid_that_changed_after_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    calls = 0

    def provider(port: int) -> list[int]:
        nonlocal calls
        if port != 8123:
            return []
        calls += 1
        return [7201] if calls == 1 else [7202]

    infos = {
        ("backend", 7201): PortProcessInfo("backend", "127.0.0.1", 8123, 7201),
        ("backend", 7202): PortProcessInfo("backend", "127.0.0.1", 8123, 7202),
    }
    monkeypatch.setattr(manager, "_port_pid_provider", provider)
    monkeypatch.setattr(
        manager,
        "_port_process_info",
        lambda service, pid: infos[(service, pid)],
    )
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(manager, "_log_process_event", lambda *_args: None)
    monkeypatch.setattr(manager, "_pid_state", lambda _pid: "alive")
    monkeypatch.setattr(
        manager,
        "_emergency_terminate_pid",
        lambda _info: pytest.fail("確認後に現れたPIDを終了してはいけない"),
    )

    report = manager.emergency_stop("backend", expected_pids={"backend": (7201,)})

    result = report.results["backend"]
    assert report.succeeded is False
    assert result.skipped_pids == (7202,)
    assert result.remaining[0].pid == 7202


def test_emergency_stop_removes_dead_stale_record_after_port_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch)
    manager._records["backend"] = ProcessRecord(
        "backend",
        7301,
        r"C:\Python\python.exe",
        (r"C:\Python\python.exe", "-m", "uvicorn"),
        str(process_manager.ROOT / "backend"),
        "2026-08-13T00:00:00+00:00",
        123.5,
        "manager-test",
        port=8123,
    )
    monkeypatch.setattr(manager, "_port_pid_provider", lambda _port: [])
    monkeypatch.setattr(manager, "_save_pid_file", lambda: None)
    monkeypatch.setattr(manager, "_log_process_event", lambda *_args: None)
    monkeypatch.setattr(manager, "_pid_state", lambda _pid: "dead")

    report = manager.emergency_stop("backend")

    assert report.succeeded is True
    assert "backend" not in manager._records
    assert manager._states["backend"] == "stopped"
