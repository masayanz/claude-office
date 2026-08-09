"""Dependency-free reader/writer for the shared app-settings.json file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def _find_root() -> Path:
    """Find the AI Office Viewer root when running from source or a frozen EXE."""
    start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    start = start.parent if start.is_file() else start
    for candidate in (start, *start.parents):
        if (
            (candidate / "config" / "app-settings.json").is_file()
            and (candidate / "backend").is_dir()
            and (candidate / "frontend").is_dir()
        ):
            return candidate
    return start


ROOT = _find_root()
SETTINGS_PATH = ROOT / "config" / "app-settings.json"
DEFAULTS: dict[str, Any] = {
    "language": "ja",
    "backend_host": "127.0.0.1",
    "backend_port": 8000,
    "frontend_host": "127.0.0.1",
    "frontend_port": 3000,
    "open_browser_on_start": True,
    "browser_mode": "normal",
    "company_name": "",
    "owner_name": "Owner",
    "owner_image_filename": None,
    "stop_servers_on_manager_exit": False,
}


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(DEFAULTS)
    result.update(values)
    if not all(
        isinstance(result[key], int) and 1024 <= result[key] <= 65535
        for key in ("backend_port", "frontend_port")
    ):
        raise ValueError("ポートは1024から65535の範囲で指定してください")
    if result["backend_port"] == result["frontend_port"]:
        raise ValueError("BackendとFrontendに同じポートは指定できません")
    if result["browser_mode"] not in {"normal", "app"}:
        raise ValueError("browser_modeが不正です")
    if result["language"] not in {"ja", "en", "es", "pt-BR"}:
        raise ValueError("languageが不正です")
    if not isinstance(result["stop_servers_on_manager_exit"], bool):
        raise ValueError("stop_servers_on_manager_exitが不正です")
    return result


def load_settings() -> tuple[dict[str, Any], str | None]:
    try:
        return _validate(json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))), None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return dict(DEFAULTS), str(exc)


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    current, _ = load_settings()
    current.update(updates)
    values = _validate(current)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix="app-settings-", suffix=".json", dir=SETTINGS_PATH.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, SETTINGS_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return values
