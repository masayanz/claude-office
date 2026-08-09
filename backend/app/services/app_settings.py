"""Shared AI Office Viewer settings stored outside the backend database."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT_DIR / "config" / "app-settings.json"
OWNER_IMAGE_DIR = ROOT_DIR / "config" / "owner-image"

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
    "owner_image_filename": None,
    "stop_servers_on_manager_exit": False,
}


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 65535


def validate_settings(raw: object) -> dict[str, Any]:
    """Validate and normalize a settings object, raising ValueError on bad data."""
    if not isinstance(raw, dict):
        raise ValueError("settings must be a JSON object")
    result = dict(DEFAULT_SETTINGS)
    result.update(raw)
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
    for key in ("company_name", "owner_name"):
        if not isinstance(result[key], str) or len(result[key].strip()) > 120:
            raise ValueError(f"{key} must be a string of at most 120 characters")
        result[key] = result[key].strip()
    if not isinstance(result["open_browser_on_start"], bool):
        raise ValueError("open_browser_on_start must be boolean")
    if not isinstance(result["stop_servers_on_manager_exit"], bool):
        raise ValueError("stop_servers_on_manager_exit must be boolean")
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
        return dict(DEFAULT_SETTINGS), f"Using default app settings: {exc}"


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
