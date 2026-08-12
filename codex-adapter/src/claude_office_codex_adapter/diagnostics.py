"""Read-only diagnostics for the optional Codex adapter."""

from __future__ import annotations

import http.client
import json
import sys
from contextlib import suppress
from typing import TypedDict

from claude_office_codex_adapter.config import (
    HTTP_TIMEOUT_SECONDS,
    _settings_path,
    get_event_endpoint,
    is_loopback_host,
)


class EndpointDiagnostic(TypedDict):
    host: str
    port: int
    path: str
    loopback: bool


class BackendDiagnostic(TypedDict):
    reachable: bool
    status_code: int | None


class AdapterDiagnostic(TypedDict):
    ok: bool
    settings_loaded: bool
    endpoint: EndpointDiagnostic
    python: dict[str, bool | str]
    modules: dict[str, bool]
    backend: BackendDiagnostic


def _settings_loaded() -> bool:
    """Check that the shared settings file is valid without exposing its contents."""
    try:
        value = json.loads(_settings_path().read_text(encoding="utf-8"))
        return isinstance(value, dict)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _backend_health(host: str, port: int) -> BackendDiagnostic:
    """Probe only the configured local health endpoint, once and without retries."""
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(host, port, timeout=HTTP_TIMEOUT_SECONDS)
        connection.request("GET", "/health")
        response = connection.getresponse()
        return {"reachable": response.status == 200, "status_code": response.status}
    except (http.client.HTTPException, TimeoutError, OSError, ValueError):
        return {"reachable": False, "status_code": None}
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()


def run_check() -> AdapterDiagnostic:
    """Return a JSON-serializable, metadata-only adapter diagnostic.

    This deliberately does not read stdin, append a journal record, or submit
    an event.  It is safe for Manager to invoke as a standalone process.
    """
    host, port, path = get_event_endpoint()
    loopback = is_loopback_host(host)
    backend = _backend_health(host, port) if loopback else {
        "reachable": False,
        "status_code": None,
    }
    supported_python = sys.version_info >= (3, 13)
    settings_loaded = _settings_loaded()
    modules = {"json": True, "http_client": True}
    adapter_ready = settings_loaded and loopback and supported_python and all(modules.values())
    return {
        "ok": adapter_ready and backend["reachable"],
        "settings_loaded": settings_loaded,
        "endpoint": {"host": host, "port": port, "path": path, "loopback": loopback},
        "python": {
            "version": ".".join(str(part) for part in sys.version_info[:3]),
            "supported": supported_python,
        },
        "modules": modules,
        "backend": backend,
    }
