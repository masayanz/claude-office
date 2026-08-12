"""Safe local process control for the AI Office Viewer backend and frontend."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

from .settings import ROOT, load_settings

RUNTIME_DIR = ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
PID_PATH = RUNTIME_DIR / "processes.json"


@dataclass
class ServiceStatus:
    name: str
    running: bool
    healthy: bool
    pid: int | None = None
    detail: str = ""


@dataclass
class CodexRestoreStatus:
    """Normalized result returned by the backend Codex restore API."""

    state: str
    session_count: int = 0
    detail: str = ""


class ServiceManager:
    """Start only commands owned by this application and track their PIDs."""

    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _settings(self) -> dict[str, Any]:
        return load_settings()[0]

    def hooks_installed(self) -> bool:
        """Return whether the user-level Codex hook references this viewer."""
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        hooks_path = codex_home / "hooks.json"
        try:
            return "claude-office-hook" in hooks_path.read_text(encoding="utf-8")
        except OSError:
            return False

    def adapter_available(self) -> bool:
        """Return whether the shared Codex adapter launcher exists."""
        return (ROOT / "codex-adapter" / "hook.py").is_file()

    def _backend_api_request(
        self, path: str, *, method: str = "GET", timeout: float = 0.75
    ) -> dict[str, Any]:
        """Call a local backend API with a short timeout.

        The Manager never persists or displays the API key. When the user has
        explicitly configured one, forwarding it from the environment keeps
        the manual restore action compatible with the backend middleware.
        """
        settings = self._settings()
        url = f"http://{settings['backend_host']}:{settings['backend_port']}{path}"
        headers = {"Accept": "application/json"}
        api_key = os.environ.get("CLAUDE_OFFICE_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        data = b"{}" if method == "POST" else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Backend APIがHTTP {exc.code}を返しました") from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError("Backend APIへ接続できませんでした") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Backend APIから不正な応答を受信しました")
        return payload

    @staticmethod
    def _restore_status(payload: dict[str, Any], default_state: str) -> CodexRestoreStatus:
        raw_state = str(payload.get("state", payload.get("status", default_state))).lower()
        state_aliases = {
            "accepted": "checking",
            "pending": "checking",
            "running": "checking",
            "in_progress": "checking",
            "completed": "succeeded",
            "complete": "succeeded",
            "success": "succeeded",
            "error": "failed",
            "failure": "failed",
        }
        state = state_aliases.get(raw_state, raw_state)
        count_value = payload.get(
            "session_count",
            payload.get("restored_sessions", payload.get("restored_count", 0)),
        )
        session_count = (
            count_value
            if isinstance(count_value, int) and not isinstance(count_value, bool)
            else 0
        )
        detail_value = payload.get("message", payload.get("detail", payload.get("error", "")))
        detail = detail_value if isinstance(detail_value, str) else ""
        return CodexRestoreStatus(state=state, session_count=max(session_count, 0), detail=detail)

    def codex_restore_status(self) -> CodexRestoreStatus:
        payload = self._backend_api_request("/api/v1/codex/restore/status")
        return self._restore_status(payload, "idle")

    def restore_codex_sessions(self) -> CodexRestoreStatus:
        payload = self._backend_api_request("/api/v1/codex/restore", method="POST")
        return self._restore_status(payload, "checking")

    def _url(self, service: str) -> str:
        settings = self._settings()
        if service == "backend":
            return f"http://{settings['backend_host']}:{settings['backend_port']}/health"
        return f"http://{settings['frontend_host']}:{settings['frontend_port']}"

    def _healthy(self, service: str) -> bool:
        try:
            with urllib.request.urlopen(self._url(service), timeout=1) as response:
                body = response.read().decode("utf-8", errors="ignore")
                return response.status == 200 and (service != "backend" or '"ok"' in body)
        except (OSError, ValueError):
            return False

    def status(self, service: str) -> ServiceStatus:
        process = self.processes.get(service)
        if process is not None and process.poll() is not None:
            self.processes.pop(service, None)
            process = None
        healthy = self._healthy(service)
        return ServiceStatus(
            service, process is not None or healthy, healthy, process.pid if process else None
        )

    def _command(self, service: str) -> tuple[list[str], Path]:
        settings = self._settings()
        if service == "backend":
            return (
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    str(settings["backend_host"]),
                    "--port",
                    str(settings["backend_port"]),
                ],
                ROOT / "backend",
            )
        return (
            [
                "bun",
                "run",
                "dev",
                "--",
                "--hostname",
                str(settings["frontend_host"]),
                "--port",
                str(settings["frontend_port"]),
            ],
            ROOT / "frontend",
        )

    def start(self, service: str) -> ServiceStatus:
        current = self.status(service)
        if current.running:
            return current
        command, cwd = self._command(service)
        log = (LOG_DIR / f"{service}.log").open("a", encoding="utf-8")
        environment = os.environ.copy()
        environment["CLAUDE_OFFICE_ROOT"] = str(ROOT)
        settings = self._settings()
        if service == "frontend":
            environment["NEXT_PUBLIC_API_URL"] = (
                f"http://{settings['backend_host']}:{settings['backend_port']}"
            )
            environment["NEXT_PUBLIC_WS_URL"] = (
                f"ws://{settings['backend_host']}:{settings['backend_port']}"
            )
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        self.processes[service] = process
        self._save_pid_file()
        return ServiceStatus(service, True, False, process.pid, "starting")

    def stop(self, service: str) -> ServiceStatus:
        process = self.processes.pop(service, None)
        if process is not None and process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    # A detached/frozen Windows process can reject CTRL_BREAK
                    # even though its process handle is still alive. Fall back
                    # to the normal terminate path so Manager restart remains
                    # usable instead of surfacing WinError 6 to the user.
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
        self._save_pid_file()
        return self.status(service)

    def restart(self, service: str) -> ServiceStatus:
        self.stop(service)
        return self.start(service)

    def open_office(self, app_mode: bool = False) -> None:
        settings = self._settings()
        url = f"http://{settings['frontend_host']}:{settings['frontend_port']}"
        if app_mode:
            browser = which("msedge.exe") or which("chrome.exe")
            if browser:
                subprocess.Popen([browser, f"--app={url}"])
                return
        webbrowser.open(url)

    def open_web_settings(self, section: str = "office") -> None:
        """Open a stable deep link to the Web settings screen.

        Settings themselves remain in the shared app-settings.json file; this
        only gives Manager users access to settings that require the Web UI,
        such as an owner image and whiteboard content.
        """
        if section not in {"office", "board"}:
            raise ValueError("設定画面の種類が不正です")
        settings = self._settings()
        url = (
            f"http://{settings['frontend_host']}:{settings['frontend_port']}"
            f"?settings={section}"
        )
        webbrowser.open(url)

    def read_logs(self, service: str, lines: int = 200) -> str:
        path = LOG_DIR / f"{service}.log"
        try:
            return "".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[
                    -lines:
                ]
            )
        except OSError:
            return "ログはまだありません。"

    def _save_pid_file(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            name: {"pid": process.pid, "updated_at": datetime.now(UTC).isoformat()}
            for name, process in self.processes.items()
        }
        PID_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
