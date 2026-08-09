"""PySide6 Windows GUI and task-tray host for AI Office Viewer Manager."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from contextlib import suppress

from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
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
from .process_manager import ServiceManager
from .resources import manager_icon_path
from .settings import load_settings, save_settings


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
        self.owner_name = QLineEdit(str(settings["owner_name"]))
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
        self.stop_on_exit = QCheckBox("Manager終了時にBackend/Frontendも停止する")
        self.stop_on_exit.setChecked(bool(settings.get("stop_servers_on_manager_exit", False)))

        form = QFormLayout()
        form.addRow("会社名", self.company_name)
        form.addRow("オーナー名", self.owner_name)
        form.addRow("Backendポート", self.backend_port)
        form.addRow("Frontendポート", self.frontend_port)
        form.addRow("ブラウザ表示", self.browser_mode)
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
                    "stop_servers_on_manager_exit": self.stop_on_exit.isChecked(),
                }
            )
        except ValueError as exc:
            QMessageBox.critical(self, "設定エラー", str(exc))
            return
        self.accept()


class ManagerWindow(QMainWindow):
    def __init__(self, icon: QIcon) -> None:
        super().__init__()
        self.setWindowTitle(MANAGER_NAME)
        self.setWindowIcon(icon)
        self.resize(820, 500)
        self.manager = ServiceManager()
        self._icon = icon
        self._is_quitting = False
        self._tray_notice_shown = False
        self._status_labels: dict[str, QLabel] = {}
        self._last_backend_healthy = False
        self._last_frontend_healthy = False
        self._build_window()
        self._build_tray()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(3000)
        self._refresh_status()

    def _build_window(self) -> None:
        central = QWidget(self)
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
                ("adapter", "Codex Adapter"),
                ("hooks", "Codex global hooks"),
            )
        ):
            card = QGroupBox(label)
            card_layout = QVBoxLayout(card)
            status = QLabel("確認中…")
            card_layout.addWidget(status)
            self._status_labels[service] = status
            cards.addWidget(card, column // 2, column % 2)
        layout.addLayout(cards)

        buttons = QHBoxLayout()
        for text, callback in (
            ("起動", self._start),
            ("停止", self._stop),
            ("再起動", self._restart),
            ("AIオフィスを開く", self._open_normal),
            ("専用表示", self._open_app),
            ("設定", self._settings_dialog),
            ("ログ", self._logs_dialog),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, fn=callback: fn())
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("× / Alt+F4: タスクトレイへ収納　　終了: トレイメニューから"))
        layout.addStretch(1)
        self.setCentralWidget(central)

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon, self)
        self._tray.setToolTip(MANAGER_NAME)
        menu = QMenu(self)
        self._add_tray_action(menu, f"{MANAGER_NAME}を開く", self.restore_window)
        menu.addSeparator()
        self._add_tray_action(menu, f"{PRODUCT_NAME}を起動", self._start)
        self._add_tray_action(menu, f"{PRODUCT_NAME}を停止", self._stop)
        self._add_tray_action(menu, f"{PRODUCT_NAME}を再起動", self._restart)
        menu.addSeparator()
        self._add_tray_action(menu, "通常ブラウザで開く", self._open_normal)
        self._add_tray_action(menu, f"{PRODUCT_NAME}専用表示", self._open_app)
        menu.addSeparator()
        self._add_tray_action(menu, "設定", self._settings_dialog)
        self._add_tray_action(menu, "ログ", self._logs_dialog)
        menu.addSeparator()
        self._add_tray_action(menu, "終了", self._quit_from_tray)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

    @staticmethod
    def _add_tray_action(menu: QMenu, text: str, callback: Callable[[], None]) -> QAction:
        action = QAction(text, menu)
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_window()

    def restore_window(self) -> None:
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        if self._is_quitting:
            event.accept()
            return
        event.ignore()
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
        backend = self.manager.status("backend")
        frontend = self.manager.status("frontend")
        self._last_backend_healthy = backend.healthy
        self._last_frontend_healthy = frontend.healthy
        self._status_labels["backend"].setText(self._status_text(backend.running, backend.healthy))
        self._status_labels["frontend"].setText(
            self._status_text(frontend.running, frontend.healthy)
        )
        self._status_labels["adapter"].setText(
            "利用可能" if self.manager.adapter_available() else "見つかりません"
        )
        self._status_labels["hooks"].setText(
            "インストール済み" if self.manager.hooks_installed() else "未インストール"
        )
        self._tray.setToolTip(
            f"{MANAGER_NAME}\n"
            f"Backend: {'稼働中' if backend.healthy else '停止'}\n"
            f"Frontend: {'稼働中' if frontend.healthy else '停止'}"
        )

    @staticmethod
    def _status_text(running: bool, healthy: bool) -> str:
        if healthy:
            return "稼働中"
        return "起動中" if running else "停止中"

    def _run_manager_action(self, callback: Callable[[], None], label: str) -> None:
        try:
            callback()
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, f"{label}エラー", str(exc))
        self._refresh_status()

    def _start(self) -> None:
        def action() -> None:
            self.manager.start("backend")
            self.manager.start("frontend")

        self._run_manager_action(action, "起動")

    def _stop(self) -> None:
        def action() -> None:
            self.manager.stop("frontend")
            self.manager.stop("backend")

        self._run_manager_action(action, "停止")

    def _restart(self) -> None:
        def action() -> None:
            self.manager.stop("frontend")
            self.manager.stop("backend")
            self.manager.start("backend")
            self.manager.start("frontend")

        self._run_manager_action(action, "再起動")

    def _open_normal(self) -> None:
        self.manager.open_office(False)

    def _open_app(self) -> None:
        self.manager.open_office(True)

    def _settings_dialog(self) -> None:
        SettingsDialog(self._icon, self).exec()
        self._refresh_status()

    def _logs_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{PRODUCT_NAME} ログ")
        dialog.setWindowIcon(self._icon)
        dialog.resize(900, 580)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            self.manager.read_logs("backend") + "\n\n" + self.manager.read_logs("frontend")
        )
        layout.addWidget(text)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _quit_from_tray(self) -> None:
        self._is_quitting = True
        settings, _ = load_settings()
        if settings.get("stop_servers_on_manager_exit", False):
            self.manager.stop("frontend")
            self.manager.stop("backend")
        self._status_timer.stop()
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
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
