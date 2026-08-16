"""Shared AI Office Viewer settings stored outside the backend database."""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_configured_root = os.environ.get("AI_OFFICE_ROOT") or os.environ.get("CLAUDE_OFFICE_ROOT")
ROOT_DIR = (
    Path(_configured_root).expanduser().resolve()
    if _configured_root
    else Path(__file__).resolve().parents[3]
)
SETTINGS_PATH = ROOT_DIR / "config" / "app-settings.json"
OWNER_IMAGE_DIR = ROOT_DIR / "config" / "owner-image"
_IANA_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)+$")

DEFAULT_SETTINGS: dict[str, Any] = {
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
    "replay_history_enabled": True,
    "replay_retention_days": 30,
    "replay_compress_idle": True,
    "replay_default_speed": 1,
    "replay_clock_mode": "recorded",
}


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 65535


def _validate_text(value: object, key: str, maximum: int, *, required: bool = False) -> str:
    """Validate a user-facing text setting and return its trimmed value."""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if len(normalized) > maximum or (required and not normalized):
        qualifier = f"between 1 and {maximum}" if required else f"at most {maximum}"
        raise ValueError(f"{key} must be a string of {qualifier} characters")
    return normalized


def _validate_goals(value: object, key: str) -> list[str]:
    """Validate a bounded list of plain-text board goals."""
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list with at most 50 items")
    goals = cast(list[object], value)
    if len(goals) > 50:
        raise ValueError(f"{key} must be a list with at most 50 items")
    return [_validate_text(goal, key, 100, required=True) for goal in goals]


def _valid_iana_timezone(value: str) -> bool:
    """Validate IANA names on systems with or without an installed tz database."""
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        # Windows Python installations may not ship tzdata. Keep validation
        # portable by enforcing the IANA name shape when the database is absent.
        return bool(_IANA_TIMEZONE_RE.fullmatch(value))


def validate_settings(raw: object) -> dict[str, Any]:
    """Validate and normalize a settings object, raising ValueError on bad data."""
    if not isinstance(raw, dict):
        raise ValueError("settings must be a JSON object")
    raw_settings = cast(dict[str, Any], raw)
    result = deepcopy(DEFAULT_SETTINGS)
    result.update(raw_settings)
    if result["language"] not in {"ja", "en", "es", "pt-BR"}:
        raise ValueError("language must be ja, en, es, or pt-BR")
    if not _valid_port(result["backend_port"]) or not _valid_port(result["frontend_port"]):
        raise ValueError("ports must be integers between 1024 and 65535")
    if result["backend_port"] == result["frontend_port"]:
        raise ValueError("backend_port and frontend_port must be different")
    for key in ("backend_host", "frontend_host"):
        if not isinstance(result[key], str) or not result[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if result["browser_mode"] not in {"normal", "app"}:
        raise ValueError("browser_mode must be normal or app")
    result["company_name"] = _validate_text(result["company_name"], "company_name", 120)
    result["owner_name"] = _validate_text(result["owner_name"], "owner_name", 50, required=True)
    result["owner_title"] = _validate_text(result["owner_title"], "owner_title", 50)
    result["owner_message"] = _validate_text(result["owner_message"], "owner_message", 200)
    if result["board_mode"] not in {"todo", "daily_goals", "weekly_goals", "memo", "custom"}:
        raise ValueError("board_mode must be todo, daily_goals, weekly_goals, memo, or custom")
    result["daily_goals"] = _validate_goals(result["daily_goals"], "daily_goals")
    result["weekly_goals"] = _validate_goals(result["weekly_goals"], "weekly_goals")
    result["board_memo"] = _validate_text(result["board_memo"], "board_memo", 500)
    result["custom_board_title"] = _validate_text(
        result["custom_board_title"], "custom_board_title", 50
    )
    result["custom_board_message"] = _validate_text(
        result["custom_board_message"], "custom_board_message", 500
    )
    if not isinstance(result["board_auto_rotate"], bool):
        raise ValueError("board_auto_rotate must be boolean")
    rotate_seconds = result["board_rotate_seconds"]
    if (
        not isinstance(rotate_seconds, int)
        or isinstance(rotate_seconds, bool)
        or not 5 <= rotate_seconds <= 3600
    ):
        raise ValueError("board_rotate_seconds must be an integer between 5 and 3600")
    if not isinstance(result["open_browser_on_start"], bool):
        raise ValueError("open_browser_on_start must be boolean")
    if not isinstance(result["stop_servers_on_manager_exit"], bool):
        raise ValueError("stop_servers_on_manager_exit must be boolean")
    if not isinstance(result["restore_codex_sessions"], bool):
        raise ValueError("restore_codex_sessions must be boolean")
    restore_window = result["restore_window_minutes"]
    if (
        not isinstance(restore_window, int)
        or isinstance(restore_window, bool)
        or not 1 <= restore_window <= 1440
    ):
        raise ValueError("restore_window_minutes must be an integer between 1 and 1440")
    if result["clock_timezone_mode"] not in {"local", "iana"}:
        raise ValueError("clock_timezone_mode must be local or iana")
    timezone = _validate_text(result["clock_timezone"], "clock_timezone", 100)
    if timezone and not _valid_iana_timezone(timezone):
        raise ValueError("clock_timezone must be a valid IANA timezone or empty")
    result["clock_timezone"] = timezone
    if result["main_agent_name_mode"] not in {"auto", "custom"}:
        raise ValueError("main_agent_name_mode must be auto or custom")
    result["main_agent_custom_name"] = _validate_text(
        result["main_agent_custom_name"], "main_agent_custom_name", 50
    )
    if not isinstance(result["replay_history_enabled"], bool):
        raise ValueError("replay_history_enabled must be boolean")
    if result["replay_retention_days"] not in {0, 7, 30, 90}:
        raise ValueError("replay_retention_days must be 0, 7, 30, or 90")
    if not isinstance(result["replay_compress_idle"], bool):
        raise ValueError("replay_compress_idle must be boolean")
    if result["replay_default_speed"] not in {0.5, 1, 2, 4, 8}:
        raise ValueError("replay_default_speed must be 0.5, 1, 2, 4, or 8")
    if result["replay_clock_mode"] not in {"recorded", "current"}:
        raise ValueError("replay_clock_mode must be recorded or current")
    image = result.get("owner_image_filename")
    if image is not None and (
        not isinstance(image, str)
        or Path(image).name != image
        or Path(image).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
    ):
        raise ValueError("owner_image_filename must be a safe image filename")
    return result


def load_settings(path: Path = SETTINGS_PATH) -> tuple[dict[str, Any], str | None]:
    """Load settings and return ``(settings, warning)``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return validate_settings(raw), None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return deepcopy(DEFAULT_SETTINGS), f"Using default app settings: {exc}"


def save_settings(updates: dict[str, Any], path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Merge, validate, and atomically write shared settings."""
    current, _ = load_settings(path)
    current.update(updates)
    validated = validate_settings(current)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="app-settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(validated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return validated
