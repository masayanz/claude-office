"""Dependency-free reader/writer for the shared app-settings.json file."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
_IANA_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)+$")
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
    "owner_title": "",
    "owner_message": "",
    "owner_image_filename": None,
    "board_mode": "todo",
    "daily_goals": [],
    "weekly_goals": [],
    "board_memo": "",
    "custom_board_title": "",
    "custom_board_message": "",
    "board_auto_rotate": False,
    "board_rotate_seconds": 10,
    "stop_servers_on_manager_exit": False,
    "restore_codex_sessions": True,
    "restore_window_minutes": 30,
    "clock_timezone_mode": "local",
    "clock_timezone": "",
    "main_agent_name_mode": "auto",
    "main_agent_custom_name": "",
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
    for key, maximum in (
        ("company_name", 120),
        ("owner_name", 50),
        ("owner_title", 50),
        ("owner_message", 200),
        ("board_memo", 500),
        ("custom_board_title", 50),
        ("custom_board_message", 500),
    ):
        if (
            not isinstance(result[key], str)
            or len(result[key].strip()) > maximum
            or (key == "owner_name" and not result[key].strip())
        ):
            raise ValueError(f"{key}が不正です")
        result[key] = result[key].strip()
    if result["board_mode"] not in {
        "todo",
        "daily_goals",
        "weekly_goals",
        "memo",
        "custom",
    }:
        raise ValueError("board_modeが不正です")
    for key in ("daily_goals", "weekly_goals"):
        goals = result[key]
        if (
            not isinstance(goals, list)
            or len(goals) > 50
            or any(
                not isinstance(goal, str)
                or not goal.strip()
                or len(goal.strip()) > 100
                for goal in goals
            )
        ):
            raise ValueError(f"{key}が不正です")
        result[key] = [goal.strip() for goal in goals]
    if not isinstance(result["board_auto_rotate"], bool):
        raise ValueError("board_auto_rotateが不正です")
    rotate_seconds = result["board_rotate_seconds"]
    if (
        not isinstance(rotate_seconds, int)
        or isinstance(rotate_seconds, bool)
        or not 5 <= rotate_seconds <= 3600
    ):
        raise ValueError("board_rotate_secondsが不正です")
    if not isinstance(result["stop_servers_on_manager_exit"], bool):
        raise ValueError("stop_servers_on_manager_exitが不正です")
    if not isinstance(result["restore_codex_sessions"], bool):
        raise ValueError("restore_codex_sessionsが不正です")
    restore_window = result["restore_window_minutes"]
    if (
        not isinstance(restore_window, int)
        or isinstance(restore_window, bool)
        or not 1 <= restore_window <= 1440
    ):
        raise ValueError("復元対象時間は1分から1440分の範囲で指定してください")
    if result["clock_timezone_mode"] not in {"local", "iana"}:
        raise ValueError("clock_timezone_modeが不正です")
    timezone = result["clock_timezone"]
    if not isinstance(timezone, str) or len(timezone.strip()) > 100:
        raise ValueError("clock_timezoneが不正です")
    timezone = timezone.strip()
    if timezone:
        try:
            valid_timezone = bool(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError):
            valid_timezone = bool(_IANA_TIMEZONE_RE.fullmatch(timezone))
        if not valid_timezone:
            raise ValueError("clock_timezoneが不正です")
    result["clock_timezone"] = timezone
    if result["main_agent_name_mode"] not in {"auto", "custom"}:
        raise ValueError("main_agent_name_modeが不正です")
    custom_name = result["main_agent_custom_name"]
    if not isinstance(custom_name, str) or len(custom_name.strip()) > 50:
        raise ValueError("main_agent_custom_nameが不正です")
    result["main_agent_custom_name"] = custom_name.strip()
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
