"""Conservative adapter settings with a shared Claude Office config file."""

from __future__ import annotations

import json
import os
from pathlib import Path

EVENTS_HOST = "127.0.0.1"
EVENTS_PORT = 8000
EVENTS_PATH = "/api/v1/events"
HTTP_TIMEOUT_SECONDS = 0.5


def _settings_path() -> Path:
    configured_root = os.environ.get("CLAUDE_OFFICE_ROOT")
    if configured_root:
        return Path(configured_root) / "config" / "app-settings.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "app-settings.json"
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[3] / "config" / "app-settings.json"


def get_event_endpoint() -> tuple[str, int, str]:
    """Read the current backend endpoint without ever breaking the hook."""
    try:
        settings = json.loads(_settings_path().read_text(encoding="utf-8"))
        host = settings.get("backend_host", EVENTS_HOST)
        port = settings.get("backend_port", EVENTS_PORT)
        if not isinstance(host, str) or not host.strip():
            raise ValueError("invalid backend_host")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("invalid backend_port")
        return host, port, EVENTS_PATH
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return EVENTS_HOST, EVENTS_PORT, EVENTS_PATH
