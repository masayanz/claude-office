"""Safe local process control for the AI Office Viewer backend and frontend."""

from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import webbrowser
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from time import monotonic, sleep
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
_WIN32_KERNEL32: Any | None = None


def _win32_kernel32() -> Any:
    """Load Win32 process APIs with explicit 64-bit-safe signatures."""
    global _WIN32_KERNEL32
    if _WIN32_KERNEL32 is not None:
        return _WIN32_KERNEL32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    _WIN32_KERNEL32 = kernel32
    return kernel32


@dataclass
class ServiceStatus:
    name: str
    running: bool
    healthy: bool
    pid: int | None = None
    detail: str = ""
    owned: bool = False
    state: str = "stopped"


@dataclass(frozen=True, slots=True)
class ProcessProbe:
    state: str
    executable: str | None = None
    creation_time: float | None = None


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    """Persisted identity for a process started by this Manager instance."""

    service: str
    pid: int
    executable: str
    command: tuple[str, ...]
    cwd: str
    started_at: str
    creation_time: float | None
    manager_instance_id: str
    descendant_pids: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "pid": self.pid,
            "executable": self.executable,
            "command": list(self.command),
            "cwd": self.cwd,
            "started_at": self.started_at,
            "creation_time": self.creation_time,
            "manager_instance_id": self.manager_instance_id,
            "descendant_pids": list(self.descendant_pids),
            "schema": 2,
        }

    @classmethod
    def from_dict(cls, service: str, value: object) -> ProcessRecord | None:
        if not isinstance(value, dict):
            return None
        pid = value.get("pid")
        executable = value.get("executable")
        command = value.get("command")
        cwd = value.get("cwd")
        started_at = value.get("started_at")
        creation_time = value.get("creation_time")
        instance_id = value.get("manager_instance_id")
        descendant_pids = value.get("descendant_pids", [])
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(executable, str)
            or not executable
            or not isinstance(command, list)
            or not all(isinstance(item, str) for item in command)
            or not isinstance(cwd, str)
            or not isinstance(started_at, str)
            or not isinstance(instance_id, str)
            or not isinstance(descendant_pids, list)
            or not all(
                isinstance(item, int) and not isinstance(item, bool) and item > 0
                for item in descendant_pids
            )
        ):
            return None
        if creation_time is not None and (
            isinstance(creation_time, bool) or not isinstance(creation_time, (int, float))
        ):
            return None
        return cls(
            service=service,
            pid=pid,
            executable=executable,
            command=tuple(command),
            cwd=cwd,
            started_at=started_at,
            creation_time=float(creation_time) if creation_time is not None else None,
            manager_instance_id=instance_id,
            descendant_pids=tuple(descendant_pids),
        )


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
    returncode: int | None = None
    failure_stage: str = ""
    exception_type: str = ""
    stderr_summary: str = ""


class ServiceManager:
    """Start only commands owned by this application and track their PIDs."""

    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        # Keep the parent's file handle alive for the lifetime of each child.
        # The child receives its own inheritable handle on Windows, but retaining
        # this one also makes the intended append-only log lifetime explicit.
        self._log_streams: dict[str, TextIO] = {}
        self._records: dict[str, ProcessRecord] = self._load_pid_file()
        self._states: dict[str, str] = {"backend": "stopped", "frontend": "stopped"}
        self._manager_instance_id = uuid.uuid4().hex
        self._lifecycle_lock = threading.RLock()
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_pid_file() -> dict[str, ProcessRecord]:
        """Load only the new, identity-bearing PID format.

        Old files intentionally are not adopted: a PID by itself is not safe
        enough to stop after the Manager has been restarted.
        """
        try:
            payload = json.loads(PID_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        records: dict[str, ProcessRecord] = {}
        for service in ("backend", "frontend"):
            record = ProcessRecord.from_dict(service, payload.get(service))
            if record is not None:
                records[service] = record
        return records

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
        if launcher:
            command = [launcher, "-3.13", str(hook), "--check"]
        elif os.name == "nt":
            # A frozen Manager must not try to execute its own EXE as Python.
            launcher = which("python.exe") or which("python3.exe")
            if launcher is None:
                return False
            command = [launcher, str(hook), "--check"]
        else:
            command = [sys.executable, str(hook), "--check"]
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
        hooks_path = self._codex_home() / "hooks.json"
        launcher_path = self._codex_home() / "claude-office-hook.ps1"
        adapter_path = ROOT / "codex-adapter" / "hook.py"
        current = self.inspect_global_hooks()
        if current.state.value == "ok":
            result = GlobalHooksRepairResult(True, "Global Hooksは正常です。修復は必要ありません。")
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result

        installer = ROOT / "codex-adapter" / "install-global-hooks.ps1"
        if not installer.is_file():
            result = GlobalHooksRepairResult(
                False,
                "Global Hooks修復スクリプトが見つかりません",
                failure_stage="locate_installer",
            )
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result
        if not adapter_path.is_file():
            result = GlobalHooksRepairResult(
                False,
                "Codex Adapterが見つかりません。",
                failure_stage="locate_adapter",
            )
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result
        powershell = which("powershell.exe") or which("pwsh.exe")
        if powershell is None:
            result = GlobalHooksRepairResult(
                False,
                "PowerShellが見つかりません",
                failure_stage="locate_powershell",
            )
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result
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
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=30,
                **self._hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            result = GlobalHooksRepairResult(
                False,
                "Global Hooks修復が時間切れになりました",
                failure_stage="execute_timeout",
                exception_type="TimeoutExpired",
            )
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result
        except OSError as exc:
            result = GlobalHooksRepairResult(
                False,
                "Global Hooks修復を開始できませんでした",
                failure_stage="execute_start",
                exception_type=type(exc).__name__,
            )
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result
        if completed.returncode == 0:
            result = GlobalHooksRepairResult(True, "Codex global hooksを修復しました")
            self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
            return result

        stderr_summary = self._safe_subprocess_summary(completed.stderr)
        combined = f"{completed.stdout}\n{completed.stderr}".lower()
        if "json" in combined or "parsererror" in combined:
            detail = "Codex Global Hooks設定を読み込めませんでした。"
            stage = "parse_hooks"
        elif "access" in combined or "denied" in combined or "permission" in combined:
            detail = (
                "Codex設定ファイルを更新できませんでした。\n"
                "ファイルのアクセス権を確認してください。"
            )
            stage = "write_hooks"
        elif "launcher" in combined:
            detail = "AI Office Viewer用Hook Launcherが見つかりません。"
            stage = "write_launcher"
        elif "adapter" in combined:
            detail = "Codex Adapterが見つかりません。"
            stage = "locate_adapter"
        else:
            detail = "Global Hooksの修復に失敗しました。\n詳細はManagerログを確認してください。"
            stage = "execute"
        result = GlobalHooksRepairResult(
            False,
            detail,
            returncode=completed.returncode,
            failure_stage=stage,
            stderr_summary=stderr_summary,
        )
        self.log_global_hooks_repair(result, hooks_path, launcher_path, adapter_path)
        return result

    @staticmethod
    def _safe_subprocess_summary(value: object, limit: int = 240) -> str:
        """Keep only a short, non-sensitive diagnostic fragment from stderr."""
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        text = re.sub(r"(?i)\b(token|auth|authorization|prompt)\b\s*[:=].*", r"\1=[redacted]", text)
        return text[:limit]

    def log_global_hooks_repair(
        self,
        result: GlobalHooksRepairResult,
        hooks_path: Path,
        launcher_path: Path,
        adapter_path: Path,
    ) -> None:
        """Record repair diagnostics without hook payloads or credentials."""
        line = (
            f"{datetime.now(UTC).isoformat()} Global hooks repair: "
            f"result={'success' if result.succeeded else 'failure'} "
            f"returncode={result.returncode if result.returncode is not None else '-'} "
            f"stage={result.failure_stage or 'none'} "
            f"exception={result.exception_type or 'none'} "
            f"hooks_file={hooks_path} launcher_exists={launcher_path.is_file()} "
            f"adapter_exists={adapter_path.is_file()}"
        )
        if result.stderr_summary:
            line += f" stderr={result.stderr_summary}"
        try:
            with (LOG_DIR / "manager.log").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

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

    def _port_in_use(self, service: str) -> bool:
        settings = self._settings()
        host_key = "backend_host" if service == "backend" else "frontend_host"
        port_key = "backend_port" if service == "backend" else "frontend_port"
        try:
            with socket.create_connection(
                (str(settings[host_key]), int(settings[port_key])), timeout=0.25
            ):
                return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _normalise_path(value: str) -> str:
        return os.path.normcase(os.path.abspath(value))

    @staticmethod
    def _windows_process_probe(pid: int) -> ProcessProbe:
        """Read process identity and distinguish dead from access denied."""
        if sys.platform != "win32" or pid <= 0:
            return ProcessProbe("dead")
        kernel32 = _win32_kernel32()
        process_query_limited_information = 0x1000
        process_synchronize = 0x00100000
        handle = kernel32.OpenProcess(
            process_query_limited_information | process_synchronize, False, pid
        )
        if not handle:
            error = ctypes.get_last_error()
            return ProcessProbe("dead" if error == 87 else "unknown")
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return ProcessProbe("unknown")
            if exit_code.value != 259:  # STILL_ACTIVE
                return ProcessProbe("dead")
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ProcessProbe("unknown")
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return ProcessProbe("unknown", buffer.value)
            filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            creation_time = filetime / 10_000_000 - 11_644_473_600
            return ProcessProbe("alive", buffer.value, creation_time)
        finally:
            kernel32.CloseHandle(handle)

    @classmethod
    def _windows_process_identity(cls, pid: int) -> tuple[str, float | None] | None:
        probe = cls._windows_process_probe(pid)
        if probe.state != "alive" or probe.executable is None:
            return None
        return probe.executable, probe.creation_time

    @staticmethod
    def _windows_descendant_pids(root_pid: int) -> list[int]:
        """Return descendants using Toolhelp32Snapshot, without PowerShell."""
        if sys.platform != "win32" or root_pid <= 0:
            return []

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = _win32_kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot in (0, ctypes.c_void_p(-1).value):
            return []
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            parents: dict[int, int] = {}
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return []
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                entry.dwSize = ctypes.sizeof(ProcessEntry)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)

        descendants: list[int] = []
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            children = [pid for pid, parent_pid in parents.items() if parent_pid == parent]
            descendants.extend(children)
            pending.extend(children)
        return descendants

    @classmethod
    def _terminate_windows_process_tree(cls, root_pid: int) -> int:
        """Terminate a validated root and descendants through Win32 APIs."""
        if sys.platform != "win32" or root_pid <= 0:
            return 0
        kernel32 = _win32_kernel32()
        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        process_synchronize = 0x00100000
        candidates = [root_pid, *cls._windows_descendant_pids(root_pid)]
        terminated = 0
        for pid in reversed(candidates):
            handle = kernel32.OpenProcess(
                process_terminate | process_query_limited_information | process_synchronize,
                False,
                pid,
            )
            if not handle:
                continue
            try:
                if kernel32.TerminateProcess(handle, 1):
                    terminated += 1
            finally:
                kernel32.CloseHandle(handle)
        return terminated

    @classmethod
    def _process_creation_time(cls, pid: int) -> float | None:
        identity = cls._windows_process_identity(pid)
        return identity[1] if identity is not None else None

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return ServiceManager._pid_state(pid) == "alive"

    @staticmethod
    def _pid_state(pid: int) -> str:
        if pid <= 0:
            return "dead"
        if sys.platform == "win32":
            return ServiceManager._windows_process_probe(pid).state
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return "dead"
        return "alive"

    def _record_matches(self, record: ProcessRecord) -> bool:
        if self._pid_state(record.pid) != "alive":
            return False
        if sys.platform != "win32":
            # A persisted PID cannot be adopted safely without an equivalent
            # executable/creation-time query. Current-process Popen handles
            # remain safe on POSIX and are handled separately.
            return False
        identity = self._windows_process_identity(record.pid)
        if identity is None:
            return False
        if self._normalise_path(identity[0]) != self._normalise_path(record.executable):
            return False
        if record.creation_time is None or identity[1] is None:
            return False
        return abs(identity[1] - record.creation_time) < 2.0

    def _record_verification(self, record: ProcessRecord) -> str:
        probe = self._windows_process_probe(record.pid)
        if probe.state != "alive":
            return probe.state
        if probe.executable is None or probe.creation_time is None:
            return "unknown"
        if self._normalise_path(probe.executable) != self._normalise_path(record.executable):
            return "mismatch"
        if abs(probe.creation_time - (record.creation_time or 0)) >= 2.0:
            return "mismatch"
        return "match"

    def _active_process(
        self, service: str
    ) -> tuple[subprocess.Popen[Any] | None, ProcessRecord | None]:
        changed = False
        processes = getattr(self, "processes", {})
        process = processes.get(service)
        if process is not None:
            try:
                alive = process.poll() is None
            except (OSError, AttributeError):
                alive = False
            if alive:
                return process, getattr(self, "_records", {}).get(service)
            processes.pop(service, None)
            changed = True
            logs = getattr(self, "_log_streams", {})
            log = logs.pop(service, None)
            if log is not None:
                log.close()

        record = getattr(self, "_records", {}).get(service)
        if record is not None:
            if self._record_matches(record):
                return None, record
            if self._record_verification(record) == "dead":
                getattr(self, "_records", {}).pop(service, None)
                getattr(self, "_states", {})[service] = "stopped"
                changed = True
        if changed and hasattr(self, "_manager_instance_id"):
            self._save_pid_file()
        return None, None

    def status(self, service: str) -> ServiceStatus:
        if service not in {"backend", "frontend"}:
            raise ValueError("サービス名が不正です")
        process, record = self._active_process(service)
        healthy = self._healthy(service)
        state = getattr(self, "_states", {}).get(service, "stopped")
        if healthy and state == "starting":
            getattr(self, "_states", {})[service] = "running"
            state = "running"
        persisted = getattr(self, "_records", {}).get(service)
        if persisted is not None:
            verification = self._record_verification(persisted)
            if verification == "unknown":
                return ServiceStatus(
                    service,
                    True,
                    healthy,
                    persisted.pid,
                    "プロセスの所有確認ができないため、安全のため停止しません。",
                    False,
                    "unknown",
                )
            if verification == "mismatch":
                getattr(self, "_records", {}).pop(service, None)
                self._save_pid_file()
        if process is not None or record is not None:
            pid = process.pid if process is not None else record.pid
            detail = "Manager管理"
            if process is None:
                detail = "前回Managerから引き継いだプロセス"
            elif not healthy and state == "starting":
                detail = "起動中"
            return ServiceStatus(service, True, healthy, pid, detail, True, state)
        if healthy or self._port_in_use(service):
            return ServiceStatus(
                service,
                True,
                healthy,
                None,
                "外部プロセスがポートを使用中（Managerは停止しません）",
                False,
                "external",
            )
        return ServiceStatus(service, False, False, None, "停止中", False, "stopped")

    def _command(self, service: str) -> tuple[list[str], Path]:
        settings = self._settings()
        if service == "backend":
            cwd = ROOT / "backend"
            python_candidates = (
                cwd / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python.exe",
                cwd / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python",
            )
            python = next((path for path in python_candidates if path.is_file()), None)
            if python is not None:
                executable = str(python)
                command = [executable, "-m", "uvicorn", "app.main:app"]
            else:
                executable = which("uv.exe" if os.name == "nt" else "uv")
                if executable is None:
                    raise FileNotFoundError("Backend起動用のPythonまたはuvが見つかりません")
                command = [executable, "run", "uvicorn", "app.main:app"]
            command.extend(
                [
                    "--host",
                    str(settings["backend_host"]),
                    "--port",
                    str(settings["backend_port"]),
                ]
            )
            return command, cwd

        cwd = ROOT / "frontend"
        # Invoke Next directly and use its webpack development mode. This
        # avoids the extra Turbopack worker process that can fail with EPERM
        # when a windowed Manager launches the dev server on Windows.
        node = which("node.exe" if os.name == "nt" else "node")
        next_cli = cwd / "node_modules" / "next" / "dist" / "bin" / "next"
        if node is not None and next_cli.is_file():
            return (
                [
                    node,
                    str(next_cli),
                    "dev",
                    "--webpack",
                    "--hostname",
                    str(settings["frontend_host"]),
                    "--port",
                    str(settings["frontend_port"]),
                ],
                cwd,
            )
        bun = which("bun.exe" if os.name == "nt" else "bun")
        if bun is not None:
            return (
                [
                    bun,
                    "run",
                    "dev",
                    "--",
                    "--hostname",
                    str(settings["frontend_host"]),
                    "--port",
                    str(settings["frontend_port"]),
                ],
                cwd,
            )
        raise FileNotFoundError("Frontend起動用のNodeまたはBunが見つかりません")

    def _log_process_event(self, service: str, event: str, detail: str = "") -> None:
        process = getattr(self, "processes", {}).get(service)
        pid = getattr(process, "pid", None)
        if pid is None:
            pid = getattr(getattr(self, "_records", {}).get(service), "pid", None)
        line = (
            f"{datetime.now(UTC).isoformat()} process service={service} event={event}"
            f" pid={pid if pid is not None else '-'}"
        )
        if detail:
            line += f" detail={self._safe_subprocess_summary(detail)}"
        try:
            with (LOG_DIR / "manager.log").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def _record_for_process(
        self, service: str, process: subprocess.Popen[Any], command: list[str], cwd: Path
    ) -> ProcessRecord:
        executable = Path(command[0]).resolve()
        return ProcessRecord(
            service=service,
            pid=process.pid,
            executable=str(executable),
            command=tuple(command),
            cwd=str(cwd.resolve()),
            started_at=datetime.now(UTC).isoformat(),
            creation_time=self._process_creation_time(process.pid),
            manager_instance_id=getattr(self, "_manager_instance_id", "legacy"),
            descendant_pids=tuple(self._windows_descendant_pids(process.pid)),
        )

    def start(self, service: str) -> ServiceStatus:
        if service not in {"backend", "frontend"}:
            raise ValueError("サービス名が不正です")
        with getattr(self, "_lifecycle_lock", threading.RLock()):
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
                getattr(self, "_states", {})[service] = "error"
                self._log_process_event(service, "start_failed")
                raise
            self.processes[service] = process
            self._log_streams[service] = log
            getattr(self, "_records", {})[service] = self._record_for_process(
                service, process, command, cwd
            )
            getattr(self, "_states", {})[service] = "starting"
            self._save_pid_file()
            self._log_process_event(service, "started")
            return ServiceStatus(service, True, False, process.pid, "起動中", True, "starting")

    @staticmethod
    def _process_alive(process: subprocess.Popen[Any] | None, pid: int | None) -> bool:
        if process is not None:
            try:
                return process.poll() is None
            except (OSError, AttributeError):
                return True
        return bool(pid and ServiceManager._pid_state(pid) != "dead")

    def _tree_alive(
        self,
        process: subprocess.Popen[Any] | None,
        pid: int | None,
        tracked_pids: set[int],
    ) -> bool:
        if self._process_alive(process, pid):
            return True
        return any(self._pid_state(child_pid) != "dead" for child_pid in tracked_pids)

    def _extend_tracked_tree(self, pid: int | None, tracked_pids: set[int]) -> None:
        if os.name == "nt" and isinstance(pid, int) and pid > 0:
            tracked_pids.update(self._windows_descendant_pids(pid))

    def _wait_for_exit(
        self,
        process: subprocess.Popen[Any] | None,
        pid: int | None,
        timeout: float,
        tracked_pids: set[int] | None = None,
    ) -> bool:
        tracked = tracked_pids if tracked_pids is not None else set()
        self._extend_tracked_tree(pid, tracked)
        if process is not None:
            wait = getattr(process, "wait", None)
            if callable(wait):
                try:
                    wait(timeout=min(timeout, 5))
                except subprocess.TimeoutExpired:
                    pass
                except (OSError, ValueError):
                    pass
        deadline = monotonic() + timeout
        while self._tree_alive(process, pid, tracked) and monotonic() < deadline:
            self._extend_tracked_tree(pid, tracked)
            sleep(0.1)
        return not self._tree_alive(process, pid, tracked)

    def _taskkill(self, pid: int, *, force: bool) -> subprocess.CompletedProcess[str]:
        command = ["taskkill.exe", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=8,
            **self._hidden_subprocess_kwargs(),
        )

    def _close_service_log(self, service: str) -> None:
        log = getattr(self, "_log_streams", {}).pop(service, None)
        if log is not None:
            log.close()

    def _forget_service(self, service: str) -> None:
        getattr(self, "processes", {}).pop(service, None)
        getattr(self, "_records", {}).pop(service, None)
        getattr(self, "_states", {})[service] = "stopped"
        self._close_service_log(service)

    def stop(self, service: str) -> ServiceStatus:
        if service not in {"backend", "frontend"}:
            raise ValueError("サービス名が不正です")
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            process, record = self._active_process(service)
            current = self.status(service)
            if process is None and record is None:
                # External processes are deliberately never stopped by port.
                return current

            pid = getattr(process, "pid", None) if process is not None else record.pid
            tracked_pids = set(record.descendant_pids if record is not None else ())
            self._extend_tracked_tree(pid, tracked_pids)
            getattr(self, "_states", {})[service] = "stopping"
            self._log_process_event(service, "stop_requested")
            graceful_detail = ""
            if process is not None and os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    graceful_detail = "CTRL_BREAK送信済み"
                    self._log_process_event(service, "graceful_signal_sent")
                except (OSError, ValueError, AttributeError) as exc:
                    graceful_detail = f"CTRL_BREAK送信不可: {type(exc).__name__}"
                    self._log_process_event(service, "graceful_signal_failed", graceful_detail)
                    with suppress(OSError, ValueError, AttributeError):
                        process.terminate()
            elif process is not None:
                try:
                    process.terminate()
                    graceful_detail = "SIGTERM送信済み"
                except (OSError, ValueError, AttributeError) as exc:
                    graceful_detail = f"SIGTERM送信不可: {type(exc).__name__}"

            exited = self._wait_for_exit(process, pid, 8, tracked_pids)
            if process is not None and pid is None and not exited:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                    exited = True
                except (OSError, subprocess.SubprocessError, AttributeError):
                    exited = False
            if not exited and os.name == "nt" and isinstance(pid, int) and pid > 0:
                # /T without /F gives the process tree a graceful termination
                # opportunity before the last-resort force kill.
                try:
                    result = self._taskkill(pid, force=False)
                    self._log_process_event(
                        service, "tree_terminate_requested", str(result.returncode)
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    self._log_process_event(service, "tree_terminate_failed", type(exc).__name__)
                exited = self._wait_for_exit(process, pid, 4, tracked_pids)

            if not exited and os.name == "nt" and isinstance(pid, int) and pid > 0:
                try:
                    result = self._taskkill(pid, force=True)
                    self._log_process_event(
                        service, "tree_force_kill_requested", str(result.returncode)
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    self._log_process_event(service, "tree_force_kill_failed", type(exc).__name__)
                exited = self._wait_for_exit(process, pid, 8, tracked_pids)

            if not exited and os.name == "nt" and isinstance(pid, int) and pid > 0:
                try:
                    terminated = self._terminate_windows_process_tree(pid)
                    self._log_process_event(
                        service, "win32_tree_terminate_requested", str(terminated)
                    )
                except (OSError, ValueError):
                    terminated = 0
                exited = self._wait_for_exit(process, pid, 4, tracked_pids)

            if not exited and process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except (OSError, subprocess.SubprocessError, AttributeError):
                    with suppress(Exception):
                        process.kill()
                exited = self._wait_for_exit(process, pid, 2, tracked_pids)

            if not exited:
                getattr(self, "_states", {})[service] = "error"
                detail = "停止を確認できませんでした。プロセスは管理下に残しています。"
                if graceful_detail:
                    detail += f" ({graceful_detail})"
                self._save_pid_file()
                self._log_process_event(service, "stop_failed", detail)
                return ServiceStatus(
                    service, True, self._healthy(service), pid, detail, True, "error"
                )

            self._forget_service(service)
            self._save_pid_file()
            final = self.status(service)
            if not isinstance(final, ServiceStatus):
                return final
            if final.running:
                # The owned PID is gone, but a different process still answers
                # the port. Report the conflict instead of claiming success.
                detail = "Managerプロセスは停止しましたが、別プロセスがポートを使用中です。"
                final = ServiceStatus(
                    service, True, final.healthy, final.pid, detail, False, "external"
                )
            self._log_process_event(service, "stopped", final.detail)
            return final

    def restart(self, service: str) -> ServiceStatus:
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            stopped = self.stop(service)
            if stopped.running:
                return ServiceStatus(
                    service,
                    True,
                    stopped.healthy,
                    stopped.pid,
                    "停止完了を確認できないため、再起動を中止しました。",
                    stopped.owned,
                    "error",
                )
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

    def open_replay(self) -> None:
        """Open the Viewer directly in its Replay history mode."""
        settings = self._settings()
        url = f"http://{settings['frontend_host']}:{settings['frontend_port']}?mode=replay"
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
            f"LiveEvents={status.live_event_count} "
            f"TailEvents={status.tail_event_count} "
            f"Mode={status.current_input_mode} "
            f"Monitored={status.monitored_sessions} "
            f"Deduped={status.deduplicated_events} "
            f"ParseErrors={status.jsonl_parse_errors}\n"
        )
        try:
            with (LOG_DIR / "manager.log").open("a", encoding="utf-8") as stream:
                stream.write(line)
        except OSError:
            pass

    def _save_pid_file(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        records = getattr(self, "_records", {})
        data = {name: record.as_dict() for name, record in records.items()}
        data["schema"] = 2
        data["updated_at"] = datetime.now(UTC).isoformat()
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(
                prefix="processes-", suffix=".json", dir=RUNTIME_DIR
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, PID_PATH)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
