#!/usr/bin/env python3
"""Capture privacy-safe Qt screenshots for the local user manual.

This script never starts or stops the Viewer services and never reads session data.
It uses the real PySide6 widgets with fixed sample settings.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from manager import main
from manager.resources import manager_icon_path
from manager.settings import DEFAULTS


def _save_widget(widget: QWidget, destination: Path, *, max_width: int = 1440) -> None:
    pixmap = widget.grab()
    if pixmap.width() > max_width:
        pixmap = pixmap.scaledToWidth(
            max_width, Qt.TransformationMode.SmoothTransformation
        )
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"スクリーンショットを保存できませんでした: {destination}")


def capture(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    icon = QIcon(str(manager_icon_path()))
    safe_settings = {
        **DEFAULTS,
        "company_name": "Sample Company",
        "owner_name": "Owner",
        "main_agent_name_mode": "custom",
        "main_agent_custom_name": "Codex Main",
    }
    original_loader = main.load_settings
    main.load_settings = lambda: (dict(safe_settings), None)
    created: list[Path] = []
    try:
        manager_window = main.ManagerWindow(icon)
        manager_window._status_timer.stop()
        manager_window.show()
        app.processEvents()
        manager_path = output_dir / "manager-main.png"
        _save_widget(manager_window, manager_path)
        created.append(manager_path)

        settings_dialog = main.SettingsDialog(icon)
        settings_dialog.show()
        app.processEvents()
        settings_path = output_dir / "settings-main.png"
        _save_widget(settings_dialog, settings_path)
        created.append(settings_path)

        settings_dialog.close()
        manager_window._tray.hide()
        manager_window.close()
        manager_window._executor.shutdown(wait=False, cancel_futures=True)
    finally:
        main.load_settings = original_loader
    return created


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "help" / "assets" / "screenshots",
    )
    args = parser.parse_args()
    for path in capture(args.output_dir.resolve()):
        print(path)


if __name__ == "__main__":
    main_cli()
