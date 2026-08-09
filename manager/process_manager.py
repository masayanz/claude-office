"""Safe local process control for the Claude Office backend and frontend."""

from __future__ import annotations

import json
import os
import signal
import subprocess
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


class ServiceManager:
    """Start only commands owned by this application and track their PIDs."""

    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _settings(self) -> dict[str, Any]:
        return load_settings()[0]

    def hooks_installed(self) -> bool:
        """Return whether the user-level Codex hook references Claude Office."""
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        hooks_path = codex_home / "hooks.json"
        try:
            return "claude-office-hook" in hooks_path.read_text(encoding="utf-8")
        except OSError:
            return False

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
                process.send_signal(signal.CTRL_BREAK_EVENT)
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
