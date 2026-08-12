"""Safe local process control for the AI Office Viewer backend and frontend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any, TextIO

from .codex_diagnostics import (
    CodexBackendStatus,
    CodexCliDiscovery,
    CodexCliValidation,
    CodexDiagnosticReport,
    GlobalHooksInspection,
    build_diagnostic_report,
    discover_codex_cli,
    inspect_global_hooks,
    normalize_backend_status,
)
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


@dataclass(frozen=True, slots=True)
class GlobalHooksRepairResult:
    """Result of an explicit, user-triggered global hooks repair."""

    succeeded: bool
    detail: str


class ServiceManager:
    """Start only commands owned by this application and track their PIDs."""

    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        # Keep the parent's file handle alive for the lifetime of each child.
        # The child receives its own inheritable handle on Windows, but retaining
        # this one also makes the intended append-only log lifetime explicit.
        self._log_streams: dict[str, TextIO] = {}
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hidden_subprocess_kwargs(
        *, new_process_group: bool = False
    ) -> dict[str, Any]:
        """Return Windows-only options that prevent a console window flashing.

        Manager is normally a windowed executable.  Its backend, frontend and
        short-lived helper commands are console applications, so Windows would
        otherwise create a visible console for each one.  ``CREATE_NO_WINDOW``
        hides that console without redirecting output: callers can still send
        stdout and stderr to their persistent log files.
        """
        if os.name != "nt":
            return {}

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if new_process_group:
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        result: dict[str, Any] = {"creationflags": creationflags}

        # STARTUPINFO provides an extra safeguard for command launchers such as
        # uv.exe and bun.exe.  Guarding it retains import/test compatibility on
        # non-Windows Python builds whose os.name is monkeypatched.
        startup_info_factory = getattr(subprocess, "STARTUPINFO", None)
        if startup_info_factory is not None:
            startupinfo = startup_info_factory()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            result["startupinfo"] = startupinfo
        return result

    def _settings(self) -> dict[str, Any]:
        return load_settings()[0]

    def hooks_installed(self) -> bool:
        """Return whether the user-level Codex hook references this viewer."""
        return self.inspect_global_hooks().state == "ok"

    @staticmethod
    def _codex_home() -> Path:
        configured = os.environ.get("CODEX_HOME")
        return Path(configured) if configured else Path.home() / ".codex"

    def inspect_global_hooks(self) -> GlobalHooksInspection:
        """Validate the configured user-level hook chain without changing it."""
        return inspect_global_hooks(codex_home=self._codex_home(), viewer_root=ROOT)

    def adapter_available(self) -> bool:
        """Return whether the shared Codex adapter launcher exists."""
        return (ROOT / "codex-adapter" / "hook.py").is_file()

    def adapter_self_check(self) -> bool:
        """Run the adapter's event-free check and validate its shared endpoint."""
        hook = ROOT / "codex-adapter" / "hook.py"
        if not hook.is_file():
            return False
        launcher = which("py") if os.name == "nt" else None
        command = (
            [launcher, "-3.13", str(hook), "--check"]
            if launcher
            else [sys.executable, str(hook), "--check"]
        )
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=4,
                **self._hidden_subprocess_kwargs(),
            )
            payload = json.loads(completed.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return False
        endpoint = payload.get("endpoint") if isinstance(payload, dict) else None
        python_status = payload.get("python") if isinstance(payload, dict) else None
        expected = self._settings()
        return bool(
            completed.returncode == 0
            and isinstance(endpoint, dict)
            and isinstance(python_status, dict)
            and payload.get("settings_loaded") is True
            and endpoint.get("loopback") is True
            and endpoint.get("host") == expected["backend_host"]
            and endpoint.get("port") == expected["backend_port"]
            and endpoint.get("path") == "/api/v1/events"
            and python_status.get("supported") is True
        )

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
            count_value if isinstance(count_value, int) and not isinstance(count_value, bool) else 0
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

    def codex_integration_status(self) -> CodexBackendStatus:
        """Read the lightweight Backend telemetry used by live-event monitoring."""
        payload = self._backend_api_request("/api/v1/system/integration-status")
        return normalize_backend_status(payload)

    def _codex_cli(self) -> CodexCliDiscovery:
        """Discover and validate Codex without depending on the process PATH."""
        def validate(executable: Path) -> CodexCliValidation:
            try:
                completed = subprocess.run(
                    [str(executable), "--version"],
                    capture_output=True,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                    text=True,
                    timeout=3,
                    **self._hidden_subprocess_kwargs(),
                )
            except subprocess.TimeoutExpired:
                return CodexCliValidation(False, error="version_timeout")
            except (OSError, subprocess.SubprocessError):
                return CodexCliValidation(False, error="version_start_failed")
            if completed.returncode != 0:
                return CodexCliValidation(False, error="version_failed")
            lines = (completed.stdout or completed.stderr).strip().splitlines()
            if not lines:
                return CodexCliValidation(False, error="version_empty")
            return CodexCliValidation(True, version=lines[0][:120])

        return discover_codex_cli(validator=validate)

    def diagnose_codex_integration(self) -> CodexDiagnosticReport:
        """Run the Manager's explicit Codex diagnosis; safe to call from a worker."""
        cli_discovery = self._codex_cli()
        hooks = self.inspect_global_hooks()
        try:
            backend = self.codex_integration_status()
        except (RuntimeError, ValueError):
            backend = CodexBackendStatus(reachable=False)
        return build_diagnostic_report(
            cli_available=cli_discovery.available,
            cli_version=cli_discovery.version,
            cli_discovery=cli_discovery,
            hooks_inspection=hooks,
            adapter_available=self.adapter_self_check(),
            backend_status=backend,
        )

    def repair_global_hooks(self) -> GlobalHooksRepairResult:
        """Run only this application's idempotent hook installer.

        This method neither starts nor stops VS Code or Codex.  It is intended
        for an explicit Manager repair action and avoids a shell command so a
        moved Viewer root cannot change the executable invocation.
        """
        installer = ROOT / "codex-adapter" / "install-global-hooks.ps1"
        if not installer.is_file():
            return GlobalHooksRepairResult(False, "Global Hooks修復スクリプトが見つかりません")
        powershell = which("powershell.exe") or which("pwsh.exe")
        if powershell is None:
            return GlobalHooksRepairResult(False, "PowerShellが見つかりません")
        try:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installer),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                **self._hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return GlobalHooksRepairResult(False, "Global Hooks修復が時間切れになりました")
        except OSError:
            return GlobalHooksRepairResult(False, "Global Hooks修復を開始できませんでした")
        if completed.returncode == 0:
            return GlobalHooksRepairResult(True, "Codex global hooksを修復しました")
        return GlobalHooksRepairResult(False, "Global Hooks修復に失敗しました")

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
            log = getattr(self, "_log_streams", {}).pop(service, None)
            if log is not None:
                log.close()
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
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **self._hidden_subprocess_kwargs(new_process_group=True),
            )
        except (OSError, ValueError):
            log.close()
            raise
        self.processes[service] = process
        self._log_streams[service] = log
        self._save_pid_file()
        return ServiceStatus(service, True, False, process.pid, "starting")

    def stop(self, service: str) -> ServiceStatus:
        process = self.processes.pop(service, None)
        try:
            if process is not None and process.poll() is None:
                if os.name == "nt":
                    pid = getattr(process, "pid", None)
                    if isinstance(pid, int) and pid > 0:
                        completed = subprocess.run(
                            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                            text=True,
                            timeout=8,
                            **self._hidden_subprocess_kwargs(),
                        )
                        if completed.returncode != 0 and process.poll() is None:
                            process.terminate()
                    else:
                        # Compatibility for process-like test doubles and rare
                        # launchers without a usable PID.
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
        finally:
            log = getattr(self, "_log_streams", {}).pop(service, None)
            if log is not None:
                log.close()
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
                subprocess.Popen([browser, f"--app={url}"], **self._hidden_subprocess_kwargs())
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
        url = f"http://{settings['frontend_host']}:{settings['frontend_port']}?settings={section}"
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

    def log_codex_diagnostic(self, report: CodexDiagnosticReport) -> None:
        """Append one sanitized diagnostic summary to the Manager log."""
        status = report.backend_status
        line = (
            f"{datetime.now(UTC).isoformat()} Codex diagnostic: "
            f"CLI={report.cli.state.value.upper()} "
            f"Hooks={report.hooks.state.value.upper()} "
            f"Adapter={report.adapter.state.value.upper()} "
            f"Backend={report.backend.state.value.upper()} "
            f"Restore={status.restored_sessions} "
            f"LiveEvents={status.live_event_count}\n"
        )
        try:
            with (LOG_DIR / "manager.log").open("a", encoding="utf-8") as stream:
                stream.write(line)
        except OSError:
            pass

    def _save_pid_file(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            name: {"pid": process.pid, "updated_at": datetime.now(UTC).isoformat()}
            for name, process in self.processes.items()
        }
        PID_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
