"""PySide6 Windows GUI and task-tray host for AI Office Viewer Manager."""

from __future__ import annotations

import ctypes
import sys
import time
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime

from PySide6.QtCore import QIODevice, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .branding import (
    APP_USER_MODEL_ID,
    MANAGER_NAME,
    PRODUCT_NAME,
    PRODUCT_SUBTITLE_JA,
    SINGLE_INSTANCE_NAME,
)
from .codex_diagnostics import (
    CodexBackendStatus,
    CodexDiagnosticReport,
    DiagnosticCheck,
    DiagnosticState,
    build_diagnostic_report,
)
from .process_manager import (
    CodexRestoreStatus,
    EmergencyStopReport,
    GlobalHooksRepairResult,
    PortProcessInfo,
    ServerLifecycleManager,
    ServiceStatus,
)
from .resources import manager_icon_path, user_manual_path
from .settings import load_settings, save_settings

DEDICATED_VIEW_STARTING = "DEDICATED_VIEW_STARTING"
DEDICATED_VIEW_OPEN = "DEDICATED_VIEW_OPEN"
DEDICATED_VIEW_CLOSED = "DEDICATED_VIEW_CLOSED"
DEDICATED_VIEW_ERROR = "DEDICATED_VIEW_ERROR"


def _set_windows_app_id() -> None:
    """Set a stable Windows taskbar identity without affecting other platforms."""
    if sys.platform != "win32":
        return
    with suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


class SingleInstance(QObject):
    """Use a local Qt socket to restore the existing Manager on second launch."""

    show_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)

    def acquire(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(SINGLE_INSTANCE_NAME, QIODevice.OpenModeFlag.WriteOnly)
        if socket.waitForConnected(350):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(350)
            socket.disconnectFromServer()
            return False

        QLocalServer.removeServer(SINGLE_INSTANCE_NAME)
        return self._server.listen(SINGLE_INSTANCE_NAME)

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda current=socket: self._read_socket(current))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _read_socket(self, socket: QLocalSocket) -> None:
        if bytes(socket.readAll()).startswith(b"show"):
            self.show_requested.emit()

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(SINGLE_INSTANCE_NAME)


class SettingsDialog(QDialog):
    def __init__(self, icon: QIcon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PRODUCT_NAME}設定")
        self.setWindowIcon(icon)
        settings, warning = load_settings()

        self.company_name = QLineEdit(str(settings["company_name"]))
        self.company_name.setMaxLength(120)
        self.owner_name = QLineEdit(str(settings["owner_name"]))
        self.owner_name.setMaxLength(50)
        self.backend_port = QSpinBox()
        self.backend_port.setRange(1024, 65535)
        self.backend_port.setValue(int(settings["backend_port"]))
        self.frontend_port = QSpinBox()
        self.frontend_port.setRange(1024, 65535)
        self.frontend_port.setValue(int(settings["frontend_port"]))
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("通常ブラウザ", "normal")
        self.browser_mode.addItem(f"{PRODUCT_NAME}専用表示", "app")
        index = self.browser_mode.findData(settings["browser_mode"])
        self.browser_mode.setCurrentIndex(max(index, 0))
        self.stop_on_exit = QComboBox()
        self.stop_on_exit.addItem("AI Office Viewerも停止する", True)
        self.stop_on_exit.addItem("AI Office Viewerは動作を続ける", False)
        stop_on_exit_index = self.stop_on_exit.findData(
            bool(settings.get("stop_servers_on_manager_exit", True))
        )
        self.stop_on_exit.setCurrentIndex(max(stop_on_exit_index, 0))
        self.restore_codex_sessions = QCheckBox("起動時に進行中のCodexセッションを復元する")
        self.restore_codex_sessions.setChecked(bool(settings.get("restore_codex_sessions", True)))
        self.restore_window_minutes = QSpinBox()
        self.restore_window_minutes.setRange(1, 1440)
        self.restore_window_minutes.setSuffix(" 分")
        self.restore_window_minutes.setValue(int(settings.get("restore_window_minutes", 30)))
        self.restore_window_minutes.setEnabled(self.restore_codex_sessions.isChecked())
        self.restore_codex_sessions.toggled.connect(self.restore_window_minutes.setEnabled)
        self.clock_timezone_mode = QComboBox()
        self.clock_timezone_mode.addItem("PCのローカル時刻", "local")
        self.clock_timezone_mode.addItem("IANAタイムゾーン", "iana")
        timezone_mode_index = self.clock_timezone_mode.findData(
            settings.get("clock_timezone_mode", "local")
        )
        self.clock_timezone_mode.setCurrentIndex(max(timezone_mode_index, 0))
        self.clock_timezone = QLineEdit(str(settings.get("clock_timezone", "")))
        self.clock_timezone.setPlaceholderText("例: Asia/Tokyo")
        self.clock_timezone.setMaxLength(100)
        self.main_agent_name_mode = QComboBox()
        self.main_agent_name_mode.addItem("自動", "auto")
        self.main_agent_name_mode.addItem("カスタム", "custom")
        name_mode_index = self.main_agent_name_mode.findData(
            settings.get("main_agent_name_mode", "auto")
        )
        self.main_agent_name_mode.setCurrentIndex(max(name_mode_index, 0))
        self.main_agent_custom_name = QLineEdit(
            str(settings.get("main_agent_custom_name", ""))
        )
        self.main_agent_custom_name.setMaxLength(50)
        self.main_agent_custom_name.setPlaceholderText("例: My AI")
        self.main_agent_custom_name.setEnabled(
            self.main_agent_name_mode.currentData() == "custom"
        )
        self.main_agent_name_mode.currentIndexChanged.connect(
            lambda _index: self.main_agent_custom_name.setEnabled(
                self.main_agent_name_mode.currentData() == "custom"
            )
        )

        form = QFormLayout()
        form.addRow("会社名", self.company_name)
        form.addRow("オーナー名", self.owner_name)
        form.addRow("時計のタイムゾーン", self.clock_timezone_mode)
        form.addRow("IANAタイムゾーン", self.clock_timezone)
        form.addRow("メインAI名", self.main_agent_name_mode)
        form.addRow("カスタムメインAI名", self.main_agent_custom_name)
        owner_image_hint = QLabel(
            "オーナー画像はWeb設定から変更できます。\n"
            "推奨: 512×512px / 形式: PNG・JPEG・WebP / 最大: 5MB"
        )
        owner_image_hint.setWordWrap(True)
        form.addRow("オーナー画像", owner_image_hint)
        form.addRow("Backendポート", self.backend_port)
        form.addRow("Frontendポート", self.frontend_port)
        form.addRow("ブラウザ表示", self.browser_mode)
        form.addRow("", self.restore_codex_sessions)
        form.addRow("復元対象（直近）", self.restore_window_minutes)
        form.addRow("", self.stop_on_exit)
        if warning:
            warning_label = QLabel(f"設定警告: {warning}")
            warning_label.setStyleSheet("color: #b91c1c")
            form.addRow(warning_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def _save(self) -> None:
        try:
            save_settings(
                {
                    "company_name": self.company_name.text(),
                    "owner_name": self.owner_name.text(),
                    "backend_port": self.backend_port.value(),
                    "frontend_port": self.frontend_port.value(),
                    "browser_mode": self.browser_mode.currentData(),
                    "stop_servers_on_manager_exit": bool(self.stop_on_exit.currentData()),
                    "restore_codex_sessions": self.restore_codex_sessions.isChecked(),
                    "restore_window_minutes": self.restore_window_minutes.value(),
                    "clock_timezone_mode": self.clock_timezone_mode.currentData(),
                    "clock_timezone": self.clock_timezone.text(),
                    "main_agent_name_mode": self.main_agent_name_mode.currentData(),
                    "main_agent_custom_name": self.main_agent_custom_name.text(),
                }
            )
        except ValueError as exc:
            QMessageBox.critical(self, "設定エラー", str(exc))
            return
        self.accept()


class ManagerWindow(QMainWindow):
    _status_received = Signal(object)
    _restore_status_received = Signal(object)
    _restore_request_finished = Signal(object)
    _diagnostic_received = Signal(object)
    _repair_finished = Signal(object)
    _service_action_finished = Signal(object)
    _emergency_candidates_received = Signal(object)
    _emergency_finished = Signal(object)

    def __init__(self, icon: QIcon) -> None:
        super().__init__()
        self.setWindowTitle(MANAGER_NAME)
        self.setWindowIcon(icon)
        self.resize(860, 680)
        self.manager = ServerLifecycleManager()
        self._icon = icon
        self._is_quitting = False
        self._tray_notice_shown = False
        self._status_labels: dict[str, QLabel] = {}
        self._last_backend_healthy = False
        self._last_frontend_healthy = False
        self._last_backend_state = "stopped"
        self._last_frontend_state = "stopped"
        self._last_server_snapshot: dict[str, ServiceStatus] = {}
        self._startup_grace_until = 0.0
        self._start_requested: set[str] = set()
        self._service_action_in_flight = False
        self._emergency_in_flight = False
        self._emergency_quit_after = False
        self._pending_dedicated_view = False
        self._dedicated_view_deadline = 0.0
        self._dedicated_view_state = DEDICATED_VIEW_CLOSED
        self._service_buttons: list[QPushButton] = []
        self._restore_maximized = False
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="manager-api")
        self._restore_status_in_flight = False
        self._status_poll_in_flight = False
        self._restore_request_in_flight = False
        self._diagnostic_in_flight = False
        self._next_diagnostic_at = 0.0
        self._repair_in_flight = False
        self._codex_report: CodexDiagnosticReport | None = None
        self._last_notified_codex_state: DiagnosticState | None = None
        self._last_diagnostic_log_key: tuple[str, int] | None = None
        self._is_quitting = False
        self._restore_status_received.connect(self._apply_restore_status)
        self._restore_request_finished.connect(self._apply_restore_request)
        self._diagnostic_received.connect(self._apply_codex_diagnostic)
        self._repair_finished.connect(self._apply_repair_result)
        self._service_action_finished.connect(self._apply_manager_action)
        self._emergency_candidates_received.connect(self._apply_emergency_candidates)
        self._emergency_finished.connect(self._apply_emergency_result)
        self._status_received.connect(self._apply_status_snapshot)
        self._build_window()
        self._build_tray()
        self._restore_window_geometry()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(1000)
        self._refresh_status()

    def _build_window(self) -> None:
        help_menu = self.menuBar().addMenu("ヘルプ")
        manual_action = QAction("利用者マニュアル", self)
        manual_action.triggered.connect(self._open_user_manual)
        help_menu.addAction(manual_action)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        central = QWidget(scroll)
        layout = QVBoxLayout(central)
        title = QLabel(PRODUCT_NAME)
        title.setStyleSheet("font-size: 24px; font-weight: 700")
        layout.addWidget(title)
        layout.addWidget(QLabel(PRODUCT_SUBTITLE_JA))

        cards = QGridLayout()
        for column, (service, label) in enumerate(
            (
                ("backend", "Backend"),
                ("frontend", "Frontend"),
            )
        ):
            card = QGroupBox(label)
            card_layout = QVBoxLayout(card)
            status = QLabel("確認中…")
            card_layout.addWidget(status)
            self._status_labels[service] = status
            cards.addWidget(card, column // 2, column % 2)
        layout.addLayout(cards)

        codex_group = QGroupBox("Codex連携状態")
        codex_layout = QVBoxLayout(codex_group)
        self._codex_overall = QLabel("● 確認中…")
        self._codex_overall.setStyleSheet("font-size: 16px; font-weight: 700")
        codex_layout.addWidget(self._codex_overall)
        self._codex_reason = QLabel("Codex CLI・Global Hooks・Adapter・Backendを確認しています。")
        self._codex_reason.setWordWrap(True)
        codex_layout.addWidget(self._codex_reason)
        codex_cards = QGridLayout()
        for index, (service, label) in enumerate(
            (
                ("codex_cli", "Codex CLI"),
                ("hooks", "Global Hooks"),
                ("adapter", "Codex Adapter"),
                ("codex_backend", "Backend API"),
                ("codex_restore", "Session Restore"),
                ("codex_live", "Live Events"),
                ("codex_jsonl", "JSONL Monitor"),
            )
        ):
            card = QGroupBox(label)
            card_layout = QVBoxLayout(card)
            status = QLabel("確認中…")
            status.setWordWrap(True)
            card_layout.addWidget(status)
            self._status_labels[service] = status
            codex_cards.addWidget(card, index // 2, index % 2)
        codex_layout.addLayout(codex_cards)
        layout.addWidget(codex_group)

        buttons = QHBoxLayout()
        for text, callback in (
            ("起動", self._start),
            ("停止", self._stop),
            ("再起動", self._restart),
            ("ブラウザで開く", self._open_normal),
            ("専用画面で開く", self._open_app),
            ("作業履歴を再生", self._open_replay),
            ("設定", self._settings_dialog),
            ("Web設定を開く", self._open_web_settings),
            ("ログ", self._logs_dialog),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, fn=callback: fn())
            if text == "ブラウザで開く":
                button.setToolTip("既定のWebブラウザでAI Office Viewerを開きます")
            elif text == "専用画面で開く":
                button.setToolTip(
                    "ブラウザのタブやアドレスバーを表示せず、"
                    "AI Office Viewer専用ウィンドウで開きます"
                )
            buttons.addWidget(button)
            if text in {"起動", "停止", "再起動"}:
                self._service_buttons.append(button)
        layout.addLayout(buttons)
        restore_actions = QHBoxLayout()
        self._diagnose_button = QPushButton("Codex連携を診断")
        self._diagnose_button.clicked.connect(lambda: self._poll_codex_diagnostic(full=True))
        restore_actions.addWidget(self._diagnose_button)
        self._repair_button = QPushButton("Global Hooksを修復")
        self._repair_button.clicked.connect(self._repair_global_hooks)
        restore_actions.addWidget(self._repair_button)
        self._restore_button = QPushButton("Codexセッションを再読込")
        self._restore_button.clicked.connect(self._restore_codex_sessions)
        restore_actions.addWidget(self._restore_button)
        codex_backend_restart = QPushButton("Backendを再起動")
        codex_backend_restart.clicked.connect(self._restart_backend)
        restore_actions.addWidget(codex_backend_restart)
        detail_button = QPushButton("詳細")
        detail_button.clicked.connect(self._codex_details_dialog)
        restore_actions.addWidget(detail_button)
        board_settings_button = QPushButton("ホワイトボード設定")
        board_settings_button.clicked.connect(self._open_board_settings)
        restore_actions.addWidget(board_settings_button)
        restore_actions.addStretch(1)
        layout.addLayout(restore_actions)
        emergency_group = QGroupBox("危険操作")
        emergency_layout = QHBoxLayout(emergency_group)
        emergency_hint = QLabel(
            "通常停止で解放できないポートを、確認したPIDだけ非常停止します。"
        )
        emergency_hint.setWordWrap(True)
        emergency_layout.addWidget(emergency_hint, 1)
        self._emergency_button = QPushButton("非常停止")
        self._emergency_button.setToolTip(
            "設定済みポートの占有PIDを再検査し、確認後に強制停止します"
        )
        self._emergency_button.setStyleSheet(
            "QPushButton { background:#b91c1c; color:white; font-weight:700; "
            "padding:8px 18px; }"
        )
        self._emergency_button.clicked.connect(self._open_emergency_stop)
        emergency_layout.addWidget(self._emergency_button)
        layout.addWidget(emergency_group)
        layout.addWidget(QLabel("× / Alt+F4: タスクトレイへ収納　　終了: トレイメニューから"))
        layout.addStretch(1)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

    @staticmethod
    def _window_settings() -> QSettings:
        return QSettings("AI Office Viewer", "Manager")

    def _restore_window_geometry(self) -> None:
        """Restore the last position, then make it safe for today's screens."""
        window_settings = self._window_settings()
        saved = window_settings.value("windowGeometry")
        self._restore_maximized = window_settings.value(
            "windowMaximized", False, type=bool
        )
        restored = bool(saved) and self.restoreGeometry(saved)
        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        if primary is None:
            return

        frame = self.frameGeometry()
        screen = QApplication.screenAt(frame.center())
        if screen is None:
            intersections = [
                (
                    frame.intersected(item.availableGeometry()).width()
                    * frame.intersected(item.availableGeometry()).height(),
                    item,
                )
                for item in screens
            ]
            area, screen = max(intersections, default=(0, primary), key=lambda value: value[0])
            if area <= 0:
                screen = primary
                restored = False

        available = screen.availableGeometry().adjusted(16, 16, -16, -16)
        maximum_width = max(1, int(available.width() * 0.95))
        maximum_height = max(1, int(available.height() * 0.95))
        self.resize(min(self.width(), maximum_width), min(self.height(), maximum_height))
        frame = self.frameGeometry()
        if not restored:
            frame.moveCenter(available.center())
        else:
            frame.moveLeft(
                min(
                    max(frame.left(), available.left()),
                    available.right() - frame.width() + 1,
                )
            )
            frame.moveTop(
                min(
                    max(frame.top(), available.top()),
                    available.bottom() - frame.height() + 1,
                )
            )
        self.move(frame.topLeft())

    def _save_window_geometry(self) -> None:
        window_settings = self._window_settings()
        window_settings.setValue("windowGeometry", self.saveGeometry())
        window_settings.setValue("windowMaximized", self.isMaximized())

    def show_initial(self) -> None:
        if self._restore_maximized:
            self.showMaximized()
        else:
            self.show()

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon, self)
        self._tray.setToolTip(MANAGER_NAME)
        menu = QMenu(self)
        self._add_tray_action(menu, f"{MANAGER_NAME}を開く", self.restore_window)
        menu.addSeparator()
        self._add_tray_action(menu, f"{PRODUCT_NAME}を起動", self._start)
        self._add_tray_action(menu, f"{PRODUCT_NAME}を停止", self._stop)
        self._add_tray_action(menu, f"{PRODUCT_NAME}を再起動", self._restart)
        self._add_tray_action(
            menu, "Codex連携を再診断", lambda: self._poll_codex_diagnostic(full=True)
        )
        self._add_tray_action(menu, "Codexセッションを再読込", self._restore_codex_sessions)
        menu.addSeparator()
        self._add_tray_action(
            menu,
            "ブラウザで開く",
            self._open_normal,
            "既定のWebブラウザでAI Office Viewerを開きます",
        )
        self._add_tray_action(
            menu,
            f"{PRODUCT_NAME}専用表示",
            self._open_app,
            "ブラウザのタブやアドレスバーを表示せず、AI Office Viewer専用ウィンドウで開きます",
        )
        self._add_tray_action(menu, "Web設定を開く", self._open_web_settings)
        self._add_tray_action(menu, "作業履歴を再生", self._open_replay)
        self._add_tray_action(menu, "ホワイトボード設定", self._open_board_settings)
        menu.addSeparator()
        self._add_tray_action(
            menu,
            "非常停止（確認あり）",
            self._open_emergency_stop,
            "設定済みポートの占有PIDを確認してから非常停止します",
        )
        menu.addSeparator()
        self._add_tray_action(menu, "設定", self._settings_dialog)
        self._add_tray_action(menu, "ログ", self._logs_dialog)
        menu.addSeparator()
        self._add_tray_action(menu, "終了", self._quit_from_tray)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

    @staticmethod
    def _add_tray_action(
        menu: QMenu,
        text: str,
        callback: Callable[[], None],
        tooltip: str = "",
    ) -> QAction:
        action = QAction(text, menu)
        if tooltip:
            action.setToolTip(tooltip)
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_window()

    def restore_window(self) -> None:
        if self.isMaximized():
            self.showMaximized()
        else:
            self._restore_window_geometry()
            self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        if self._is_quitting:
            event.accept()
            return
        event.ignore()
        self._save_window_geometry()
        self.hide()
        if not self._tray_notice_shown and self._tray.isVisible():
            self._tray_notice_shown = True
            self._tray.showMessage(
                MANAGER_NAME,
                f"{MANAGER_NAME}はタスクトレイで動作を続けます。\n"
                "終了する場合はトレイアイコンを右クリックし、「終了」を選択してください。",
                QSystemTrayIcon.MessageIcon.Information,
                7000,
            )

    def _refresh_status(self) -> None:
        if self._status_poll_in_flight:
            return
        self._status_poll_in_flight = True

        def read_snapshot() -> object:
            snapshot = getattr(self.manager, "snapshot", None)
            if callable(snapshot):
                return snapshot()
            return {
                "backend": self.manager.status("backend"),
                "frontend": self.manager.status("frontend"),
            }

        future = self._executor.submit(read_snapshot)
        future.add_done_callback(
            lambda completed: self._status_received.emit(self._future_result(completed))
        )

    def _apply_status_snapshot(self, result: object) -> None:
        self._status_poll_in_flight = False
        if isinstance(result, Exception) or not isinstance(result, dict):
            return
        backend = result.get("backend")
        frontend = result.get("frontend")
        if not isinstance(backend, ServiceStatus) or not isinstance(frontend, ServiceStatus):
            return
        self._last_server_snapshot = {"backend": backend, "frontend": frontend}
        self._last_backend_healthy = backend.healthy
        self._last_frontend_healthy = frontend.healthy
        self._last_backend_state = backend.state
        self._last_frontend_state = frontend.state
        in_startup_grace = time.monotonic() < self._startup_grace_until
        self._status_labels["backend"].setText(
            self._status_text(
                "backend", backend.running, backend.healthy, in_startup_grace,
                backend.state, backend.detail,
                backend.readiness_ok, backend.readiness_known, backend.process_alive,
                backend.consecutive_failures,
            )
        )
        self._status_labels["frontend"].setText(
            self._status_text(
                "frontend", frontend.running, frontend.healthy, in_startup_grace,
                frontend.state, frontend.detail,
                frontend.readiness_ok, frontend.readiness_known, frontend.process_alive,
                frontend.consecutive_failures,
            )
        )
        self._maybe_open_pending_dedicated_view()
        codex_text = self._codex_report.overall.summary if self._codex_report else "確認中"
        backend_text = (
            "稼働中"
            if backend.healthy
            else (
                "応答遅延"
                if backend.process_alive and backend.consecutive_failures < 3
                else "応答なし"
            )
        )
        self._tray.setToolTip(
            f"{MANAGER_NAME}\n"
            f"Viewer: {'稼働中' if frontend.healthy else '停止'}\n"
            f"Backend: {backend_text}\n"
            f"Codex: {codex_text}"
        )
        # Codex diagnostic is telemetry, not lifecycle health.  Poll it on a
        # slower independent cadence and never launch it merely because a
        # liveness probe failed.
        diagnostic_due = time.monotonic() >= getattr(self, "_next_diagnostic_at", 0.0)
        if (
            backend.process_alive
            and (backend.healthy or backend.state != "stopped")
            and diagnostic_due
        ):
            self._poll_codex_diagnostic(full=self._codex_report is None)

    def _status_text(
        self,
        service: str,
        running: bool,
        healthy: bool,
        starting: bool = False,
        state: str = "stopped",
        detail: str = "",
        readiness_ok: bool = False,
        readiness_known: bool = False,
        process_alive: bool = False,
        consecutive_failures: int = 0,
    ) -> str:
        if state == "stopping":
            return "停止中…"
        if state == "starting" and not healthy:
            return "起動中…"
        if state == "error" and (not process_alive or consecutive_failures == 0):
            return detail or "処理に失敗しました。\n「ログ」を確認してください。"
        if state == "external":
            return "外部で稼働中（停止対象外）"
        if healthy and readiness_known and not readiness_ok:
            return "稼働中（準備未完了）"
        if healthy:
            return "稼働中"
        if state in {"running", "degraded", "error"} and (process_alive or running):
            return "稼働中（応答遅延）" if consecutive_failures < 3 else "稼働中（応答なし）"
        if running or (starting and service in self._start_requested):
            return "起動中…"
        if service in self._start_requested:
            return f"{service.capitalize()}の起動に失敗しました。\n「ログ」を確認してください。"
        return "停止中"

    def _run_manager_action(self, callback: Callable[[], object], label: str) -> None:
        if self._service_action_in_flight or self._is_quitting:
            return
        self._service_action_in_flight = True
        for button in self._service_buttons:
            button.setEnabled(False)
        self._status_labels["backend"].setText(f"{label}中…")
        self._status_labels["frontend"].setText(f"{label}中…")
        future = self._executor.submit(callback)
        future.add_done_callback(
            lambda completed: self._service_action_finished.emit(
                (label, self._future_result(completed))
            )
        )

    def _open_emergency_stop(self) -> None:
        self._begin_emergency_stop(for_quit=False)

    def _begin_emergency_stop(self, *, for_quit: bool) -> None:
        if self._emergency_in_flight or self._service_action_in_flight:
            return
        inspect = getattr(self.manager, "inspect_configured_ports", None)
        stop = getattr(self.manager, "emergency_stop", None)
        if not callable(inspect) or not callable(stop):
            QMessageBox.warning(
                self,
                "非常停止を利用できません",
                "このManagerでは非常停止機能を利用できません。ログを確認してください。",
            )
            return
        self._emergency_in_flight = True
        self._emergency_quit_after = for_quit
        button = getattr(self, "_emergency_button", None)
        if button is not None:
            button.setEnabled(False)
        future = self._executor.submit(inspect)
        future.add_done_callback(
            lambda completed: self._emergency_candidates_received.emit(
                self._future_result(completed)
            )
        )

    @staticmethod
    def _emergency_candidate_text(info: PortProcessInfo) -> str:
        return (
            f"port={info.port}  PID={info.pid}  "
            f"process={info.process_name or '-'}  cwd={info.cwd_name or '-'}\n"
            f"identity={info.identity}"
        )

    def _apply_emergency_candidates(self, result: object) -> None:
        if isinstance(result, Exception) or not isinstance(result, dict):
            self._emergency_in_flight = False
            self._emergency_quit_after = False
            button = getattr(self, "_emergency_button", None)
            if button is not None:
                button.setEnabled(True)
            QMessageBox.warning(
                self,
                "ポート検査エラー",
                str(result) if isinstance(result, Exception) else "ポートを検査できませんでした。",
            )
            return

        for_quit = self._emergency_quit_after
        dialog = QDialog(self)
        dialog.setWindowTitle("AI Office Viewer 非常停止")
        dialog.setWindowIcon(self._icon)
        dialog.resize(760, 440)
        layout = QVBoxLayout(dialog)
        warning = QLabel(
            "これは通常停止ではありません。選択したポートの占有プロセスを終了します。\n"
            "AI Office Viewerと確認できないプロセスは、他のアプリケーションの可能性があります。\n"
            "PID・プロセス名・作業フォルダ名を確認してから選択してください。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#991b1b; font-weight:700")
        layout.addWidget(warning)

        checkboxes: dict[str, QCheckBox] = {}
        candidates: dict[str, tuple[PortProcessInfo, ...]] = {}
        for service, label in (("backend", "Backend"), ("frontend", "Frontend")):
            values = tuple(
                item
                for item in result.get(service, ())
                if isinstance(item, PortProcessInfo)
            )
            candidates[service] = values
            group = QGroupBox(label)
            group_layout = QVBoxLayout(group)
            if values:
                checkbox = QCheckBox(f"{label}を非常停止の対象にする")
                # A normal emergency click starts with no service selected.  A
                # quit fallback has already received an explicit confirmation,
                # so it is convenient to preselect the still-listening services.
                checkbox.setChecked(for_quit)
                checkboxes[service] = checkbox
                group_layout.addWidget(checkbox)
                for info in values:
                    detail = QLabel(self._emergency_candidate_text(info))
                    detail.setWordWrap(True)
                    detail.setStyleSheet(
                        "color:#991b1b" if not info.identity_verified else "color:#166534"
                    )
                    group_layout.addWidget(detail)
            else:
                group_layout.addWidget(QLabel("設定済みポートの占有プロセスはありません。"))
            layout.addWidget(group)

        controls = QHBoxLayout()
        recheck = QPushButton("ポートを再検査")
        recheck.clicked.connect(lambda: dialog.done(2))
        controls.addWidget(recheck)
        controls.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        force_button = buttons.addButton(
            "非常停止を実行", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        force_button.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        controls.addWidget(buttons)
        layout.addLayout(controls)

        code = dialog.exec()
        if code == 2:
            self._emergency_in_flight = False
            self._begin_emergency_stop(for_quit=for_quit)
            return
        if code != QDialog.DialogCode.Accepted:
            self._emergency_in_flight = False
            self._emergency_quit_after = False
            button = getattr(self, "_emergency_button", None)
            if button is not None:
                button.setEnabled(True)
            return

        selected = tuple(
            service for service, checkbox in checkboxes.items() if checkbox.isChecked()
        )
        if not selected:
            self._emergency_in_flight = False
            self._emergency_quit_after = False
            button = getattr(self, "_emergency_button", None)
            if button is not None:
                button.setEnabled(True)
            QMessageBox.information(
                self,
                "対象が選択されていません",
                "非常停止するBackendまたはFrontendを選択してください。",
            )
            return

        unverified = [
            info
            for service in selected
            for info in candidates[service]
            if not info.identity_verified
        ]
        extra_warning = ""
        if unverified:
            extra_warning = (
                "\n\nAI Office Viewerと確認できないPIDが含まれています。"
                "他のアプリケーションを停止する可能性があります。"
            )
        answer = QMessageBox.warning(
            self,
            "非常停止の最終確認",
            "選択したプロセスを終了します。通常停止に戻すことはできません。"
            + extra_warning
            + "\n\n実行する場合は「はい」、取り消す場合は「いいえ」を選択してください。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._emergency_in_flight = False
            self._emergency_quit_after = False
            button = getattr(self, "_emergency_button", None)
            if button is not None:
                button.setEnabled(True)
            return
        expected = {
            service: tuple(info.pid for info in candidates[service])
            for service in selected
        }
        self._start_emergency_action(selected, expected)

    def _start_emergency_action(
        self,
        services: tuple[str, ...],
        expected_pids: dict[str, tuple[int, ...]],
    ) -> None:
        stop = getattr(self.manager, "emergency_stop")
        future = self._executor.submit(
            stop,
            services,
            expected_pids=expected_pids,
        )
        future.add_done_callback(
            lambda completed: self._emergency_finished.emit(self._future_result(completed))
        )

    def _apply_emergency_result(self, result: object) -> None:
        for_quit = self._emergency_quit_after
        self._emergency_in_flight = False
        self._emergency_quit_after = False
        button = getattr(self, "_emergency_button", None)
        if button is not None:
            button.setEnabled(True)
        if isinstance(result, Exception) or not isinstance(result, EmergencyStopReport):
            QMessageBox.warning(
                self,
                "非常停止エラー",
                str(result) if isinstance(result, Exception) else "非常停止の結果を取得できませんでした。",
            )
            return

        lines: list[str] = []
        for service, item in result.results.items():
            state = "解放済み" if item.succeeded else "未解放"
            lines.append(f"{service}: {state} / {item.detail}")
        self._refresh_status()
        if result.succeeded and for_quit:
            self._finalize_quit()
            return
        if result.succeeded:
            QMessageBox.information(self, "非常停止完了", "\n".join(lines))
        else:
            QMessageBox.warning(
                self,
                "非常停止を確認できません",
                "Managerは終了せず、残存プロセスの管理を継続します。\n\n"
                + "\n".join(lines),
            )

    def _apply_manager_action(self, result: object) -> None:
        self._service_action_in_flight = False
        for button in self._service_buttons:
            button.setEnabled(True)
        if isinstance(result, tuple) and len(result) == 2:
            label, value = result
            if label == "終了":
                failures = [str(item) for item in value] if isinstance(value, list) else []
                if isinstance(value, Exception):
                    failures = [str(value)]
                if failures:
                    self._refresh_status()
                    if callable(getattr(self.manager, "emergency_stop", None)):
                        answer = QMessageBox.question(
                            self,
                            "通常停止に失敗しました",
                            "通常停止でサーバーの停止を確認できませんでした。\n\n"
                            + "\n".join(failures)
                            + "\n\n非常停止してManagerを終了しますか？",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No,
                        )
                        if answer == QMessageBox.StandardButton.Yes:
                            self._begin_emergency_stop(for_quit=True)
                            return
                    QMessageBox.warning(
                        self,
                        "AI Office Viewerを停止できません",
                        "Managerは終了せず、サーバーの管理を継続します。\n\n"
                        + "\n".join(failures),
                    )
                    return
                self._finalize_quit()
                return
            if label == "停止":
                failures: list[str] = []
                if isinstance(value, Exception):
                    failures = [str(value)]
                elif isinstance(value, dict):
                    for service, status in value.items():
                        if isinstance(status, ServiceStatus) and (
                            status.running
                            or status.state in {"error", "unknown", "stopping"}
                        ):
                            failures.append(f"{service}: {status.detail or '停止未確認'}")
                if failures:
                    self._refresh_status()
                    if callable(getattr(self.manager, "emergency_stop", None)):
                        answer = QMessageBox.question(
                            self,
                            "通常停止に失敗しました",
                            "通常停止でサーバーの停止を確認できませんでした。\n\n"
                            + "\n".join(failures)
                            + "\n\n非常停止の確認画面を開きますか？",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No,
                        )
                        if answer == QMessageBox.StandardButton.Yes:
                            self._begin_emergency_stop(for_quit=False)
                            return
                    QMessageBox.warning(
                        self,
                        "AI Office Viewerを停止できません",
                        "通常停止を確認できませんでした。\n\n" + "\n".join(failures),
                    )
                    return
            if label == "起動" and isinstance(value, dict):
                conflicts = [
                    f"{service}: {status.detail or 'ポート使用中'}"
                    for service, status in value.items()
                    if isinstance(status, ServiceStatus) and status.state == "external"
                ]
                if conflicts and callable(
                    getattr(self.manager, "inspect_configured_ports", None)
                ):
                    answer = QMessageBox.question(
                        self,
                        "起動できないポートがあります",
                        "設定済みポートが別プロセスにより使用中です。\n\n"
                        + "\n".join(conflicts)
                        + "\n\n占有PIDを確認しますか？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        self._begin_emergency_stop(for_quit=False)
                        return
            if isinstance(value, Exception):
                QMessageBox.critical(self, f"{label}エラー", str(value))
        self._refresh_status()

    @staticmethod
    def _restore_status_text(status: CodexRestoreStatus) -> str:
        if status.state in {"checking", "running", "pending"}:
            return "確認中…"
        if status.state in {"succeeded", "completed", "success"}:
            return f"{status.session_count}件復元" if status.session_count else "復元対象なし"
        if status.state == "disabled":
            return "自動復元: OFF"
        if status.state in {"failed", "error"}:
            return "復元に失敗しました\nCodex自体の動作には影響ありません"
        return "待機中"

    @staticmethod
    def _future_result(future: Future[object]) -> object:
        try:
            return future.result()
        except Exception as exc:  # The exception is rendered on the GUI thread.
            return exc

    def _poll_restore_status(self) -> None:
        if self._restore_status_in_flight or self._restore_request_in_flight:
            return
        self._restore_status_in_flight = True
        future = self._executor.submit(self.manager.codex_restore_status)
        future.add_done_callback(
            lambda completed: (
                self._restore_status_received.emit(self._future_result(completed))
                if not self._is_quitting
                else None
            )
        )

    def _apply_restore_status(self, result: object) -> None:
        if self._is_quitting:
            return
        self._restore_status_in_flight = False
        if self._restore_request_in_flight:
            return
        if not self._last_backend_healthy:
            self._status_labels["codex_restore"].setText("Backend停止中")
            return
        if isinstance(result, CodexRestoreStatus):
            self._status_labels["codex_restore"].setText(self._restore_status_text(result))
        else:
            self._status_labels["codex_restore"].setText(
                "状態を取得できません\nCodex自体の動作には影響ありません"
            )

    def _restore_codex_sessions(self) -> None:
        if self._restore_request_in_flight:
            return
        if not self._last_backend_healthy:
            QMessageBox.information(
                self,
                "Codexセッション復元",
                "Backendを起動してから再読込してください。",
            )
            return
        self._restore_request_in_flight = True
        self._restore_button.setEnabled(False)
        self._status_labels["codex_restore"].setText("確認中…")
        future = self._executor.submit(self.manager.restore_codex_sessions)
        future.add_done_callback(
            lambda completed: (
                self._restore_request_finished.emit(self._future_result(completed))
                if not self._is_quitting
                else None
            )
        )

    def _apply_restore_request(self, result: object) -> None:
        if self._is_quitting:
            return
        self._restore_request_in_flight = False
        self._restore_button.setEnabled(True)
        if isinstance(result, CodexRestoreStatus):
            self._status_labels["codex_restore"].setText(self._restore_status_text(result))
            QTimer.singleShot(500, lambda: self._poll_codex_diagnostic(full=False))
            return
        self._status_labels["codex_restore"].setText(
            "復元に失敗しました\nCodex自体の動作には影響ありません"
        )
        QMessageBox.warning(
            self,
            "Codexセッション復元",
            "Codexセッションの復元に失敗しました。\nCodex自体の動作には影響ありません。",
        )

    def _poll_codex_diagnostic(self, *, full: bool) -> None:
        """Run heavy static checks only initially/manually; otherwise poll telemetry."""
        if self._diagnostic_in_flight or self._is_quitting:
            return
        self._diagnostic_in_flight = True
        self._next_diagnostic_at = time.monotonic() + (1.0 if full else 5.0)
        self._diagnose_button.setEnabled(False)
        if full or self._codex_report is None:
            future = self._executor.submit(self.manager.diagnose_codex_integration)
        else:
            previous = self._codex_report

            def refresh_live() -> CodexDiagnosticReport:
                try:
                    backend_status = self.manager.codex_integration_status()
                except (RuntimeError, ValueError):
                    previous_status = previous.backend_status
                    backend_status = replace(
                        previous_status,
                        reachable=False,
                        detail="Codex診断APIを一時的に確認できません",
                    )
                return build_diagnostic_report(
                    cli_available=previous.cli.state == DiagnosticState.OK,
                    cli_version=previous.cli_version,
                    cli_discovery=previous.cli_discovery,
                    hooks_inspection=previous.hooks_inspection,
                    adapter_available=previous.adapter.state == DiagnosticState.OK,
                    backend_status=backend_status,
                    now=datetime.now(UTC),
                )

            future = self._executor.submit(refresh_live)
        future.add_done_callback(
            lambda completed: (
                self._diagnostic_received.emit(self._future_result(completed))
                if not self._is_quitting
                else None
            )
        )

    @staticmethod
    def _check_text(check: DiagnosticCheck) -> str:
        marker = {
            DiagnosticState.OK: "●",
            DiagnosticState.WAITING: "●",
            DiagnosticState.WARNING: "⚠",
            DiagnosticState.ERROR: "●",
        }[check.state]
        return f"{marker} {check.summary}" + (f"\n{check.detail}" if check.detail else "")

    @staticmethod
    def _state_color(state: DiagnosticState) -> str:
        return {
            DiagnosticState.OK: "#15803d",
            DiagnosticState.WAITING: "#64748b",
            DiagnosticState.WARNING: "#b45309",
            DiagnosticState.ERROR: "#b91c1c",
        }[state]

    def _apply_codex_diagnostic(self, result: object) -> None:
        if self._is_quitting:
            return
        self._diagnostic_in_flight = False
        self._diagnose_button.setEnabled(True)
        if not isinstance(result, CodexDiagnosticReport):
            self._codex_overall.setText("● 一時的に確認できません")
            self._codex_overall.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: "
                f"{self._state_color(DiagnosticState.WARNING)}"
            )
            self._codex_reason.setText(
                "Codex診断の応答を取得できません。Backend本体の稼働状態とは別です。"
            )
            return

        # A failed diagnostic request is not a new zero-valued telemetry
        # snapshot.  Keep the last known counters and mark only the diagnostic
        # channel unavailable.
        if not result.backend_status.reachable and self._codex_report is not None:
            previous_status = self._codex_report.backend_status
            result = build_diagnostic_report(
                cli_available=result.cli.state == DiagnosticState.OK,
                cli_version=result.cli_version,
                cli_discovery=result.cli_discovery,
                hooks_inspection=result.hooks_inspection,
                adapter_available=result.adapter.state == DiagnosticState.OK,
                backend_status=replace(
                    previous_status,
                    reachable=False,
                    detail=result.backend_status.detail
                    or "Codex診断APIを一時的に確認できません",
                ),
                now=datetime.now(UTC),
            )

        previous_state = self._codex_report.overall.state if self._codex_report else None
        self._codex_report = result
        self._codex_overall.setText(f"● {result.overall.summary}")
        self._codex_overall.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {self._state_color(result.overall.state)}"
        )
        self._codex_reason.setText(result.recommendation or result.overall.detail)
        for key, check in (
            ("codex_cli", result.cli),
            ("hooks", result.hooks),
            ("adapter", result.adapter),
            ("codex_backend", result.backend),
            ("codex_restore", result.restore),
            ("codex_live", result.live_events),
            ("codex_jsonl", result.jsonl_monitor),
        ):
            label = self._status_labels[key]
            label.setText(self._check_text(check))
            label.setStyleSheet(f"color: {self._state_color(check.state)}")

        log_key = (result.overall.state.value, result.backend_status.live_event_count)
        if log_key != self._last_diagnostic_log_key:
            self._last_diagnostic_log_key = log_key
            self.manager.log_codex_diagnostic(result)

        if (
            result.overall.state in {DiagnosticState.WARNING, DiagnosticState.ERROR}
            and result.overall.state != previous_state
            and self._tray.isVisible()
        ):
            self._tray.showMessage(
                "Codex連携: 要確認"
                if result.overall.state == DiagnosticState.WARNING
                else "Codex連携エラー",
                result.overall.detail or result.recommendation,
                QSystemTrayIcon.MessageIcon.Warning,
                7000,
            )
        self._last_notified_codex_state = result.overall.state

    def _repair_global_hooks(self) -> None:
        if self._repair_in_flight:
            return
        self._repair_in_flight = True
        self._repair_button.setEnabled(False)
        future = self._executor.submit(self.manager.repair_global_hooks)
        future.add_done_callback(
            lambda completed: (
                self._repair_finished.emit(self._future_result(completed))
                if not self._is_quitting
                else None
            )
        )

    def _apply_repair_result(self, result: object) -> None:
        self._repair_in_flight = False
        self._repair_button.setEnabled(True)
        if isinstance(result, GlobalHooksRepairResult) and result.succeeded:
            QMessageBox.information(self, "Global Hooks修復", result.detail)
            self._poll_codex_diagnostic(full=True)
            return
        detail = (
            result.detail
            if isinstance(result, GlobalHooksRepairResult)
            else "修復結果を取得できませんでした"
        )
        QMessageBox.warning(self, "Global Hooks修復", detail)

    def _codex_details_dialog(self) -> None:
        report = self._codex_report
        if report is None:
            QMessageBox.information(self, "Codex連携の詳細", "診断を実行しています。")
            return
        status = report.backend_status
        cli_source = "未検出"
        if report.cli_discovery is not None and report.cli_discovery.source is not None:
            cli_source = report.cli_discovery.detail
        settings, _ = load_settings()
        server_snapshot = self._last_server_snapshot

        def server_details(service: str) -> str:
            item = server_snapshot.get(service)
            if item is None:
                return "状態未取得"
            uptime = "-" if item.uptime_seconds is None else f"{item.uptime_seconds:.0f}s"
            live_probe = item.liveness_probe
            probe_text = (
                f"probe={live_probe.result} {live_probe.duration_ms:.1f}ms"
                if live_probe is not None
                else "probe=未取得"
            )
            return (
                f"PID={item.pid or '-'} / process={'OK' if item.process_alive else '停止'} / "
                f"liveness={'OK' if item.liveness_ok else 'NG'} / "
                "readiness="
                f"{'OK' if item.readiness_ok else ('NG' if item.readiness_known else '未確認')} / "
                f"uptime={uptime} / last={item.last_success_at or '-'} / "
                f"failures={item.consecutive_failures} / {probe_text} / "
                f"reason={item.state_reason or '-'}"
            )

        path_status = (
            "存在確認済み（場所は非表示）"
            if report.cli.state == DiagnosticState.OK
            else "未確認"
        )
        details = "\n".join(
            (
                f"Codex CLI\n{self._check_text(report.cli)}",
                f"検出方法: {cli_source}",
                f"Path: {path_status}",
                f"Version: {report.cli_version or '未確認'}",
                f"\nGlobal Hooks\n{self._check_text(report.hooks)}",
                f"設定数: {report.hooks_inspection.configured_events}/8",
                f"\nCodex Adapter\n{self._check_text(report.adapter)}",
                f"\nBackend API\n{self._check_text(report.backend)}",
                f"接続先: {settings['backend_host']}:{settings['backend_port']}",
                f"\nBackend process\n{server_details('backend')}",
                f"\nFrontend process\n{server_details('frontend')}",
                f"\nHealth probe metrics\n{self._health_probe_metrics_text()}",
                f"\nRestored Sessions\n{status.restored_sessions}",
                f"\nLive Events（今回起動以降）\n{status.live_event_count}",
                f"\nJSONL Monitor\n{self._check_text(report.jsonl_monitor)}",
                f"監視session: {status.monitored_sessions}",
                f"取得方法: {status.current_input_mode}",
                f"Last Hook event: {status.last_hook_event_at or '未受信'}",
                f"Last JSONL event: {status.last_jsonl_event_at or '未受信'}",
                f"重複排除: {status.deduplicated_events}",
                f"Parse errors: {status.jsonl_parse_errors}",
                f"\n最終受信\n{report.live_events.detail or '未受信'}",
                f"\n推奨対応\n{report.recommendation or '対応は不要です'}",
            )
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Codex連携の詳細")
        dialog.setWindowIcon(self._icon)
        dialog.resize(620, 540)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(details)
        layout.addWidget(text)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _health_probe_metrics_text(self) -> str:
        metrics = getattr(self.manager, "health_probe_metrics", lambda: {})()
        if not isinstance(metrics, dict) or not metrics:
            return "未取得"
        lines: list[str] = []
        for probe, values in sorted(metrics.items()):
            if not isinstance(values, dict):
                continue
            lines.append(
                f"{probe}: count={values.get('count', 0)} "
                f"avg={values.get('average_ms', 0)}ms max={values.get('max_ms', 0)}ms "
                f"timeout={values.get('timeouts', 0)}"
            )
        return "\n".join(lines) or "未取得"

    def _start(self) -> None:
        self._startup_grace_until = time.monotonic() + 30
        self._start_requested.update(("backend", "frontend"))

        def action() -> None:
            start_all = getattr(self.manager, "start_all", None)
            if callable(start_all):
                start_all()
                return
            self.manager.start("backend")
            self.manager.start("frontend")

        self._run_manager_action(action, "起動")

    def _stop(self) -> None:
        self._start_requested.clear()

        def action() -> object:
            stop_all = getattr(self.manager, "stop_all", None)
            if callable(stop_all):
                return stop_all()
            return {
                "frontend": self.manager.stop("frontend"),
                "backend": self.manager.stop("backend"),
            }

        self._run_manager_action(action, "停止")

    def _restart(self) -> None:
        self._startup_grace_until = time.monotonic() + 30
        self._start_requested.update(("backend", "frontend"))

        def action() -> None:
            restart_all = getattr(self.manager, "restart_all", None)
            if callable(restart_all):
                results = restart_all()
                if any(
                    status.running and status.state == "error"
                    for status in results.values()
                ):
                    raise RuntimeError(
                        "サーバーの停止完了を確認できないため、再起動を中止しました。"
                    )
                return
            self.manager.stop("frontend")
            restart_backend = getattr(self.manager, "restart_backend", None)
            backend = (
                restart_backend()
                if callable(restart_backend)
                else self.manager.restart("backend")
            )
            if backend.state not in {"starting", "running"}:
                raise RuntimeError(backend.detail or "Backendを再起動できませんでした。")
            frontend = self.manager.start("frontend")
            if frontend.state not in {"starting", "running"}:
                raise RuntimeError(frontend.detail or "Frontendを起動できませんでした。")

        self._run_manager_action(action, "再起動")

    def _restart_backend(self) -> None:
        self._startup_grace_until = time.monotonic() + 30
        self._start_requested.add("backend")

        def action() -> None:
            restart_backend = getattr(self.manager, "restart_backend", None)
            result = (
                restart_backend()
                if callable(restart_backend)
                else self.manager.restart("backend")
            )
            if result.state not in {"starting", "running"}:
                raise RuntimeError(result.detail or "Backendを再起動できませんでした。")

        self._run_manager_action(action, "Backend再起動")
        QTimer.singleShot(1500, lambda: self._poll_codex_diagnostic(full=False))

    def _open_normal(self) -> None:
        result = self.manager.open_normal_browser()
        if not result.succeeded:
            QMessageBox.warning(self, "ブラウザ起動エラー", result.detail)

    def _open_app(self) -> None:
        observe_status = getattr(self.manager, "observe_status", self.manager.status)
        backend = observe_status("backend")
        frontend = observe_status("frontend")
        self._last_backend_healthy = backend.healthy
        self._last_frontend_healthy = frontend.healthy
        self._last_backend_state = backend.state
        self._last_frontend_state = frontend.state
        log_request = getattr(self.manager, "log_dedicated_view_request", None)
        if callable(log_request):
            log_request(backend, frontend)

        if backend.state == "starting" or frontend.state == "starting":
            self._dedicated_view_state = DEDICATED_VIEW_STARTING
            self._pending_dedicated_view = True
            self._dedicated_view_deadline = time.monotonic() + 30
            QMessageBox.information(
                self,
                "専用画面を開く",
                "サーバーの起動完了を待っています。\n"
                "専用画面操作ではサーバーを起動・再起動しません。",
            )
            return

        if backend.state == "stopping" or frontend.state == "stopping":
            self._dedicated_view_state = DEDICATED_VIEW_ERROR
            QMessageBox.warning(
                self,
                "専用画面を開けません",
                "サーバーを停止しています。完了後に専用画面を開いてください。",
            )
            return

        if backend.state == "stopped":
            self._dedicated_view_state = DEDICATED_VIEW_ERROR
            QMessageBox.warning(
                self,
                "専用画面を開けません",
                "AI Office ViewerのBackendが停止しています。\n"
                "専用画面操作ではサーバーを起動しません。先に「起動」を実行してください。",
            )
            return

        # A healthy Frontend is sufficient to display the Viewer when the
        # Backend process is still present.  In particular, a temporary
        # Backend/API failure must not turn a display action into a lifecycle
        # action.
        if frontend.healthy:
            self._launch_dedicated_view()
            return

        self._dedicated_view_state = DEDICATED_VIEW_ERROR
        QMessageBox.warning(
            self,
            "専用画面を開けません",
            "AI Office Viewerが停止しています。\n"
            "専用画面操作ではサーバーを起動しません。先に「起動」を実行してください。",
        )

    def _viewer_screen_geometry(self) -> tuple[int, int, int, int] | None:
        """Use the Manager's current monitor for the dedicated app window."""
        screen = QApplication.screenAt(self.frameGeometry().center())
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return None
        available = screen.availableGeometry()
        return available.x(), available.y(), available.width(), available.height()

    def _launch_dedicated_view(self) -> None:
        self._dedicated_view_state = DEDICATED_VIEW_STARTING
        self._pending_dedicated_view = False
        self._dedicated_view_deadline = 0.0
        result = self.manager.open_dedicated_view(self._viewer_screen_geometry())
        if not result.succeeded:
            self._dedicated_view_state = DEDICATED_VIEW_ERROR
            QMessageBox.warning(self, "専用画面起動エラー", result.detail)
            return
        self._dedicated_view_state = DEDICATED_VIEW_OPEN

    def _maybe_open_pending_dedicated_view(self) -> None:
        if not getattr(self, "_pending_dedicated_view", False):
            return
        if self._service_action_in_flight:
            return
        if (
            self._last_frontend_healthy
            and self._last_backend_state not in {"starting", "stopping", "stopped"}
            and self._last_frontend_state not in {"starting", "stopping"}
        ):
            self._launch_dedicated_view()
            return
        if time.monotonic() >= getattr(self, "_dedicated_view_deadline", 0.0):
            self._pending_dedicated_view = False
            QMessageBox.warning(
                self,
                "専用画面起動エラー",
                "Frontendの起動を確認できませんでした。\n"
                "「ログ」で起動エラーを確認してください。",
            )

    def _open_replay(self) -> None:
        self.manager.open_replay()

    def _open_web_settings(self) -> None:
        self.manager.open_web_settings("office")

    def _open_board_settings(self) -> None:
        self.manager.open_web_settings("board")

    def _open_user_manual(self) -> None:
        manual = user_manual_path()
        if not manual.is_file():
            QMessageBox.warning(
                self,
                "利用者マニュアル",
                "利用者マニュアルが見つかりません。\n配布キットを再展開してください。",
            )
            return
        try:
            opened = webbrowser.open(manual.resolve().as_uri())
        except (OSError, ValueError, webbrowser.Error) as exc:
            QMessageBox.warning(
                self,
                "利用者マニュアル",
                f"利用者マニュアルを開けませんでした。\n{exc}",
            )
            return
        if not opened:
            QMessageBox.warning(
                self,
                "利用者マニュアル",
                "既定のブラウザで利用者マニュアルを開けませんでした。",
            )

    def _settings_dialog(self) -> None:
        SettingsDialog(self._icon, self).exec()
        self._refresh_status()

    def _logs_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{PRODUCT_NAME} ログ")
        dialog.setWindowIcon(self._icon)
        dialog.resize(900, 580)
        layout = QVBoxLayout(dialog)
        selector = QComboBox()
        selector.addItem("Manager", "manager")
        selector.addItem("Backend", "backend")
        selector.addItem("Frontend", "frontend")
        layout.addWidget(selector)
        text = QTextEdit()
        text.setReadOnly(True)

        def refresh() -> None:
            text.setPlainText(self.manager.read_logs(str(selector.currentData())))

        selector.currentIndexChanged.connect(lambda _index: refresh())
        refresh()
        layout.addWidget(text)
        refresh_button = QPushButton("更新")
        refresh_button.clicked.connect(refresh)
        layout.addWidget(refresh_button)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _quit_from_tray(self) -> None:
        if self._is_quitting:
            return
        if self._service_action_in_flight:
            QMessageBox.information(
                self,
                "終了できません",
                "起動・停止・再起動の完了を待ってから、もう一度「終了」を選択してください。",
            )
            return
        self._save_window_geometry()
        settings, _ = load_settings()
        if settings.get("stop_servers_on_manager_exit", True):
            self._run_manager_action(self._stop_services_for_quit, "終了")
            return
        self._finalize_quit()

    def _stop_services_for_quit(self) -> list[str]:
        stop_all = getattr(self.manager, "stop_all", None)
        if callable(stop_all):
            results = stop_all()
            return [
                f"{service}: {status.detail or '停止未確認'}"
                for service, status in results.items()
                if status.running or status.state in {"error", "unknown", "stopping"}
            ]
        failures: list[str] = []
        for service in ("frontend", "backend"):
            try:
                result = self.manager.stop(service)
                if result.running or result.state in {"error", "unknown", "stopping"}:
                    failures.append(f"{service}: {result.detail or '停止未確認'}")
            except (OSError, RuntimeError, ValueError) as exc:
                failures.append(f"{service}: {exc}")
        return failures

    def _finalize_quit(self) -> None:
        self._is_quitting = True
        self._status_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._tray.hide()
        QApplication.quit()


def run() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(MANAGER_NAME)
    app.setApplicationDisplayName(MANAGER_NAME)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(manager_icon_path()))
    app.setWindowIcon(icon)

    instance = SingleInstance()
    if not instance.acquire():
        return 0

    window = ManagerWindow(icon)
    instance.show_requested.connect(window.restore_window)
    app.aboutToQuit.connect(instance.close)
    window.show_initial()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
