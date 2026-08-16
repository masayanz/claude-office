"""Focused lifecycle tests for the Manager window without starting Qt."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from manager import main
from manager.process_manager import ServiceStatus, ViewerLaunchResult


def test_user_manual_path_uses_frozen_executable_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from manager import resources

    executable = tmp_path / "AI Office Viewer" / "AI-Office-Viewer-Manager.exe"
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "executable", str(executable))

    manual = resources.user_manual_path()

    assert manual == executable.parent / "help" / "index.html"
    assert manual.resolve().as_uri().startswith("file:///")


def test_open_user_manual_opens_existing_local_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manual = tmp_path / "help" / "index.html"
    manual.parent.mkdir()
    manual.write_text("<!doctype html>", encoding="utf-8")
    opened: list[str] = []
    window = main.ManagerWindow.__new__(main.ManagerWindow)
    monkeypatch.setattr(main, "user_manual_path", lambda: manual)
    monkeypatch.setattr(main.webbrowser, "open", lambda url: opened.append(url) or True)

    main.ManagerWindow._open_user_manual(window)

    assert manual.is_file()
    assert opened == [manual.resolve().as_uri()]


def test_open_user_manual_warns_when_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    messages: list[str] = []
    window = main.ManagerWindow.__new__(main.ManagerWindow)
    monkeypatch.setattr(main, "user_manual_path", lambda: tmp_path / "help" / "index.html")
    monkeypatch.setattr(
        main.QMessageBox,
        "warning",
        lambda _parent, _title, message: messages.append(message),
    )

    main.ManagerWindow._open_user_manual(window)

    assert messages == ["利用者マニュアルが見つかりません。\n配布キットを再展開してください。"]


class _Timer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Executor:
    def __init__(self) -> None:
        self.shutdown_args: tuple[bool, bool] | None = None

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_args = (wait, cancel_futures)


class _Tray:
    def __init__(self) -> None:
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True


def _window(manager: object) -> object:
    application = QApplication.instance() or QApplication([])
    del application
    window = main.ManagerWindow.__new__(main.ManagerWindow)
    window._is_quitting = False
    window._service_action_in_flight = False
    window._service_buttons = []
    window._last_backend_healthy = False
    window._last_frontend_healthy = False
    window._last_backend_state = "stopped"
    window._last_frontend_state = "stopped"
    window._pending_dedicated_view = False
    window._dedicated_view_deadline = 0.0
    window.manager = manager
    window._status_timer = _Timer()
    window._executor = _Executor()
    window._tray = _Tray()
    return window


def test_tray_exit_stops_frontend_then_backend_before_quitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Manager:
        def stop(self, service: str) -> ServiceStatus:
            calls.append(service)
            return ServiceStatus(service, False, False)

    window = _window(Manager())
    monkeypatch.setattr(main.ManagerWindow, "_save_window_geometry", lambda _self: None)
    monkeypatch.setattr(main.ManagerWindow, "_refresh_status", lambda _self: None)
    monkeypatch.setattr(
        main.ManagerWindow,
        "_run_manager_action",
        lambda self, callback, label: main.ManagerWindow._apply_manager_action(
            self, (label, callback())
        ),
    )
    monkeypatch.setattr(
        main,
        "load_settings",
        lambda: ({"stop_servers_on_manager_exit": True}, None),
    )
    quit_called: list[bool] = []
    monkeypatch.setattr(main.QApplication, "quit", lambda: quit_called.append(True))

    main.ManagerWindow._quit_from_tray(window)

    assert calls == ["frontend", "backend"]
    assert window._is_quitting is True
    assert window._status_timer.stopped is True
    assert window._executor.shutdown_args == (False, True)
    assert window._tray.hidden is True
    assert quit_called == [True]


def test_tray_exit_keeps_manager_open_when_stop_is_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def stop(self, service: str) -> ServiceStatus:
            return ServiceStatus(
                service, service == "backend", False, 1234, "停止未確認", True, "error"
            )

    window = _window(Manager())
    monkeypatch.setattr(main.ManagerWindow, "_save_window_geometry", lambda _self: None)
    monkeypatch.setattr(main.ManagerWindow, "_refresh_status", lambda _self: None)
    monkeypatch.setattr(
        main.ManagerWindow,
        "_run_manager_action",
        lambda self, callback, label: main.ManagerWindow._apply_manager_action(
            self, (label, callback())
        ),
    )
    monkeypatch.setattr(
        main,
        "load_settings",
        lambda: ({"stop_servers_on_manager_exit": True}, None),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        main.QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append(f"{title}:{text}"),
    )
    monkeypatch.setattr(main.QApplication, "quit", lambda: pytest.fail("停止失敗時は終了しない"))

    main.ManagerWindow._quit_from_tray(window)

    assert window._is_quitting is False
    assert window._status_timer.stopped is False
    assert warnings


def test_failed_manager_exit_can_open_confirmed_emergency_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def emergency_stop(self, *_args: object, **_kwargs: object) -> None:
            return None

    window = _window(Manager())
    started: list[bool] = []
    monkeypatch.setattr(main.ManagerWindow, "_refresh_status", lambda _self: None)
    monkeypatch.setattr(
        main.QMessageBox,
        "question",
        lambda *_args, **_kwargs: main.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main.ManagerWindow,
        "_begin_emergency_stop",
        lambda _self, *, for_quit: started.append(for_quit),
    )

    main.ManagerWindow._apply_manager_action(
        window,
        ("終了", ["backend: 停止未確認"]),
    )

    assert started == [True]
    assert window._is_quitting is False


def test_close_style_status_distinguishes_external_process() -> None:
    window = _window(object())
    assert main.ManagerWindow._status_text(
        window, "backend", True, True, state="external", detail="外部"
    ) == "外部で稼働中（停止対象外）"


def test_status_text_shows_transient_liveness_delay_before_no_response() -> None:
    window = _window(object())
    assert (
        main.ManagerWindow._status_text(
            window,
            "backend",
            True,
            False,
            state="running",
            process_alive=True,
            consecutive_failures=1,
        )
        == "稼働中（応答遅延）"
    )
    assert (
        main.ManagerWindow._status_text(
            window,
            "backend",
            True,
            False,
            state="degraded",
            process_alive=True,
            consecutive_failures=3,
        )
        == "稼働中（応答なし）"
    )


def test_restart_backend_uses_atomic_service_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Manager:
        def restart(self, service: str) -> ServiceStatus:
            calls.append(service)
            return ServiceStatus(service, True, False, 1234, "起動中", True, "starting")

    window = _window(Manager())
    window._startup_grace_until = 0.0
    window._start_requested = set()
    monkeypatch.setattr(
        window,
        "_run_manager_action",
        lambda callback, _label: callback(),
    )
    monkeypatch.setattr(main.QTimer, "singleShot", lambda *_args: None)

    main.ManagerWindow._restart_backend(window)

    assert calls == ["backend"]


def test_restart_backend_reports_stop_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Manager:
        def restart(self, service: str) -> ServiceStatus:
            return ServiceStatus(service, True, False, 1234, "停止未確認", True, "error")

    window = _window(Manager())
    window._startup_grace_until = 0.0
    window._start_requested = set()
    errors: list[Exception] = []

    def run(callback: object, _label: str) -> None:
        try:
            callback()  # type: ignore[operator]
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(window, "_run_manager_action", run)
    monkeypatch.setattr(main.QTimer, "singleShot", lambda *_args: None)

    main.ManagerWindow._restart_backend(window)

    assert errors and "停止未確認" in str(errors[0])


def test_dedicated_view_does_not_touch_running_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle_calls: list[str] = []
    opened: list[tuple[int, int, int, int] | None] = []

    class Manager:
        def status(self, service: str) -> ServiceStatus:
            pid = 1000 if service == "backend" else 2000
            return ServiceStatus(service, True, True, pid, "", True, "running")

        def open_dedicated_view(
            self, geometry: tuple[int, int, int, int] | None
        ) -> ViewerLaunchResult:
            opened.append(geometry)
            return ViewerLaunchResult(True, "dedicated", "http://127.0.0.1:3123")

        def start(self, _service: str) -> None:
            lifecycle_calls.append("start")

        def stop(self, _service: str) -> None:
            lifecycle_calls.append("stop")

        def restart(self, _service: str) -> None:
            lifecycle_calls.append("restart")

        # Keep the regression contract explicit for every server lifecycle
        # entry point that has existed in Manager implementations.
        def start_backend(self) -> None:
            lifecycle_calls.append("start_backend")

        def stop_backend(self) -> None:
            lifecycle_calls.append("stop_backend")

        def restart_backend(self) -> None:
            lifecycle_calls.append("restart_backend")

        def stop_all(self) -> None:
            lifecycle_calls.append("stop_all")

        def restart_all(self) -> None:
            lifecycle_calls.append("restart_all")

        def restore(self) -> None:
            lifecycle_calls.append("restore")

        def cleanup(self) -> None:
            lifecycle_calls.append("cleanup")

    window = _window(Manager())
    monkeypatch.setattr(window, "_viewer_screen_geometry", lambda: (0, 0, 100, 100))
    monkeypatch.setattr(
        main.QMessageBox,
        "warning",
        lambda *_args: pytest.fail("unexpected warning"),
    )
    monkeypatch.setattr(
        main.ManagerWindow,
        "_launch_dedicated_view",
        lambda self: opened.append((0, 0, 100, 100)),
    )

    main.ManagerWindow._open_app(window)

    assert lifecycle_calls == []
    assert opened == [(0, 0, 100, 100)]


def test_dedicated_view_waits_for_starting_servers_without_double_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_calls: list[str] = []
    messages: list[str] = []

    class Manager:
        def status(self, service: str) -> ServiceStatus:
            return ServiceStatus(service, True, False, 1000, "起動中", True, "starting")

        def start(self, _service: str) -> None:
            lifecycle_calls.append("start")

    window = _window(Manager())
    monkeypatch.setattr(
        main.QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )

    main.ManagerWindow._open_app(window)

    assert lifecycle_calls == []
    assert window._pending_dedicated_view is True
    assert messages and "起動完了" in messages[0]


def test_dedicated_view_never_starts_stopped_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_calls: list[str] = []
    messages: list[str] = []

    class Manager:
        def status(self, service: str) -> ServiceStatus:
            return ServiceStatus(service, False, False, None, "停止中", False, "stopped")

        def start(self, _service: str) -> None:
            lifecycle_calls.append("start")

        def stop(self, _service: str) -> None:
            lifecycle_calls.append("stop")

        def restart(self, _service: str) -> None:
            lifecycle_calls.append("restart")

        def restore_codex_sessions(self) -> None:
            lifecycle_calls.append("restore")

    window = _window(Manager())
    monkeypatch.setattr(
        main.QMessageBox, "warning", lambda _parent, _title, text: messages.append(text)
    )

    main.ManagerWindow._open_app(window)

    assert lifecycle_calls == []
    assert window._pending_dedicated_view is False
    assert messages and "起動しません" in messages[0]


def test_dedicated_view_does_not_open_when_backend_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[bool] = []
    messages: list[str] = []

    class Manager:
        def status(self, service: str) -> ServiceStatus:
            if service == "backend":
                return ServiceStatus(service, False, False, None, "停止中", False, "stopped")
            return ServiceStatus(service, True, True, 2000, "", True, "running")

    window = _window(Manager())
    monkeypatch.setattr(window, "_launch_dedicated_view", lambda: opened.append(True))
    monkeypatch.setattr(
        main.QMessageBox,
        "warning",
        lambda _parent, _title, text: messages.append(text),
    )

    main.ManagerWindow._open_app(window)

    assert opened == []
    assert messages and "Backendが停止" in messages[0]


def test_pending_dedicated_view_requires_frontend_health_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(object())
    window._pending_dedicated_view = True
    window._dedicated_view_deadline = 9999999999.0
    window._service_action_in_flight = False
    window._last_frontend_healthy = True
    window._last_backend_healthy = False
    window._last_frontend_state = "running"
    window._last_backend_state = "running"
    launched: list[bool] = []
    monkeypatch.setattr(window, "_launch_dedicated_view", lambda: launched.append(True))

    main.ManagerWindow._maybe_open_pending_dedicated_view(window)

    assert launched == [True]
