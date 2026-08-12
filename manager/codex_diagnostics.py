"""Codex integration diagnostics shared by the Manager UI and tray host.

The module deliberately keeps the classification logic independent from Qt and
subprocess calls.  Filesystem and process probes can supply their results to
``build_diagnostic_report`` and the result is safe to render or log: it never
contains hook payloads, prompts, credentials, or full session paths.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from shutil import which
from typing import Any, Callable, Mapping

REQUIRED_CODEX_HOOK_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)
LIVE_EVENT_STALE_SECONDS = 60


class DiagnosticState(StrEnum):
    """States used for individual checks and the overall integration state."""

    OK = "ok"
    WAITING = "waiting"
    WARNING = "warning"
    ERROR = "error"


class CodexCliSource(StrEnum):
    """Places where the Manager can safely look for the Codex executable."""

    ENVIRONMENT = "environment"
    CONFIG = "config"
    DESKTOP = "desktop"
    VSCODE_EXTENSION = "vscode_extension"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class CodexCliValidation:
    """Result of an event-free ``codex --version`` probe."""

    available: bool
    version: str | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class CodexCliDiscovery:
    """Payload-free result of locating and validating the Codex CLI.

    ``detail_data`` deliberately contains only the source and failure category;
    it must not expose a user's complete local path in the Manager UI or logs.
    """

    available: bool
    version: str | None = None
    source: CodexCliSource | None = None
    cause: str = "not_found"
    detail: str = ""
    detail_data: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """A compact, Japanese-renderable check result."""

    state: DiagnosticState
    summary: str
    detail: str = ""
    detail_data: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GlobalHooksInspection:
    """Validated state of the AI Office Viewer handlers in ``hooks.json``."""

    state: DiagnosticState
    configured_events: int
    missing_events: tuple[str, ...] = ()
    detail: str = ""
    launcher_exists: bool = False
    config_exists: bool = False
    root_matches: bool = False
    adapter_path_matches: bool = False
    configured_root: str | None = None


@dataclass(frozen=True, slots=True)
class CodexBackendStatus:
    """Normalized, payload-free response from the Backend integration API."""

    reachable: bool
    live_event_count: int = 0
    last_live_event_at: datetime | None = None
    backend_started_at: datetime | None = None
    restore_state: str = "idle"
    restored_sessions: int = 0
    last_restored_at: datetime | None = None
    active_codex_sessions: int = 0
    current_input_mode: str = "IDLE"
    monitored_sessions: int = 0
    tail_event_count: int = 0
    deduplicated_events: int = 0
    last_hook_event_at: datetime | None = None
    last_jsonl_event_at: datetime | None = None
    jsonl_monitor: str = "disabled"
    jsonl_monitor_health: str = "idle"
    jsonl_parse_errors: int = 0
    jsonl_file_access_failures: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CodexDiagnosticReport:
    """Complete result for a Manager diagnosis run."""

    cli: DiagnosticCheck
    hooks: DiagnosticCheck
    adapter: DiagnosticCheck
    backend: DiagnosticCheck
    restore: DiagnosticCheck
    live_events: DiagnosticCheck
    jsonl_monitor: DiagnosticCheck
    overall: DiagnosticCheck
    recommendation: str
    backend_status: CodexBackendStatus
    hooks_inspection: GlobalHooksInspection
    cli_version: str | None = None
    cli_discovery: CodexCliDiscovery | None = None


_SOURCE_LABELS = {
    CodexCliSource.ENVIRONMENT: "環境変数 CODEX_CLI_PATH",
    CodexCliSource.CONFIG: "Codex設定",
    CodexCliSource.DESKTOP: "Codex Desktop",
    CodexCliSource.VSCODE_EXTENSION: "VS Code拡張機能",
    CodexCliSource.PATH: "PATH",
}


def _executable_names() -> tuple[str, ...]:
    return ("codex.exe", "codex") if os.name == "nt" else ("codex", "codex.exe")


def _unique_candidates(
    candidates: list[tuple[CodexCliSource, Path]],
) -> tuple[tuple[CodexCliSource, Path], ...]:
    """Keep discovery deterministic while preserving its documented order."""
    result: list[tuple[CodexCliSource, Path]] = []
    seen: set[str] = set()
    for source, candidate in candidates:
        try:
            key = str(candidate.expanduser().resolve()).casefold()
        except OSError:
            key = str(candidate.expanduser()).casefold()
        if key not in seen:
            seen.add(key)
            result.append((source, candidate.expanduser()))
    return tuple(result)


def _config_cli_values(value: object, *, under_codex: bool = False) -> list[str]:
    """Extract only explicitly named Codex CLI path hints from a config object."""
    if not isinstance(value, dict):
        return []
    hints: list[str] = []
    for key, child in value.items():
        key_text = str(key).casefold().replace("-", "_")
        child_under_codex = under_codex or "codex" in key_text
        if isinstance(child, str) and (
            key_text in {"codex_cli_path", "codex_path"}
            or (child_under_codex and key_text in {"cli_path", "path", "executable"})
        ):
            hints.append(child)
        elif isinstance(child, dict):
            hints.extend(_config_cli_values(child, under_codex=child_under_codex))
    return hints


def _read_config_cli_hints(config_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    hints: list[Path] = []
    for config_path in config_paths:
        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            if config_path.suffix.casefold() == ".toml":
                document = tomllib.loads(text)
            else:
                document = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        for hint in _config_cli_values(document):
            expanded = Path(hint).expanduser()
            if not expanded.is_absolute():
                expanded = config_path.parent / expanded
            hints.append(expanded)
    return tuple(hints)


def _desktop_candidates(home: Path, environment: Mapping[str, str]) -> tuple[Path, ...]:
    local_app_data = environment.get("LOCALAPPDATA")
    bases = [Path(local_app_data)] if local_app_data else [home / "AppData" / "Local"]
    candidates: list[Path] = []
    for base in bases:
        for product in ("Codex", "codex"):
            for name in _executable_names():
                candidates.extend(
                    (
                        base / "Programs" / product / name,
                        base / "Programs" / product / "resources" / name,
                        base / product / name,
                    )
                )
    return tuple(candidates)


def _vscode_extension_candidates(home: Path, environment: Mapping[str, str]) -> tuple[Path, ...]:
    """Find the extension-bundled CLI without assuming a versioned directory."""
    extension_root = home / ".vscode" / "extensions"
    configured_root = environment.get("VSCODE_EXTENSIONS")
    if configured_root:
        extension_root = Path(configured_root)
    try:
        extensions = sorted(extension_root.glob("openai.chatgpt-*"))
    except OSError:
        return ()
    candidates: list[Path] = []
    for extension in extensions:
        # The bundle layout has changed across releases. Restrict the scan to
        # this one extension, and only accept files named as the Codex CLI.
        for name in _executable_names():
            try:
                candidates.extend(sorted(extension.rglob(name)))
            except OSError:
                continue
    return tuple(candidates)


def codex_cli_candidates(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    config_paths: tuple[Path, ...] | None = None,
    path_lookup: Callable[[str], str | None] = which,
) -> tuple[tuple[CodexCliSource, Path], ...]:
    """Return ordered CLI candidates: override, config, Desktop, extension, PATH."""
    env = os.environ if environment is None else environment
    user_home = (home or Path.home()).expanduser()
    codex_home = Path(env.get("CODEX_HOME", str(user_home / ".codex"))).expanduser()
    configs = config_paths or (
        codex_home / "config.toml",
        codex_home / "config.json",
        codex_home / "settings.json",
    )
    candidates: list[tuple[CodexCliSource, Path]] = []
    configured = env.get("CODEX_CLI_PATH", "").strip()
    if configured:
        candidates.append((CodexCliSource.ENVIRONMENT, Path(configured)))
    candidates.extend((CodexCliSource.CONFIG, path) for path in _read_config_cli_hints(configs))
    candidates.extend(
        (CodexCliSource.DESKTOP, path) for path in _desktop_candidates(user_home, env)
    )
    candidates.extend(
        (CodexCliSource.VSCODE_EXTENSION, path)
        for path in _vscode_extension_candidates(user_home, env)
    )
    executable = path_lookup("codex")
    if executable:
        candidates.append((CodexCliSource.PATH, Path(executable)))
    return _unique_candidates(candidates)


def validate_codex_cli(executable: Path) -> CodexCliValidation:
    """Run the only allowed CLI probe and return a compact, Japanese-safe cause."""
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
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


def _cli_failure_detail(source: CodexCliSource, cause: str) -> str:
    label = _SOURCE_LABELS[source]
    if cause == "not_a_file":
        return f"{label}で指定されたCodex CLIの実行ファイルが見つかりません"
    if cause == "version_timeout":
        return f"{label}のCodex CLIのバージョン確認が時間切れになりました"
    if cause == "version_start_failed":
        return f"{label}で見つけたCodex CLIを起動できません"
    if cause == "version_empty":
        return f"{label}のCodex CLIからバージョン情報を取得できません"
    return f"{label}のCodex CLIは --version 実行時にエラーになりました"


def discover_codex_cli(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    config_paths: tuple[Path, ...] | None = None,
    path_lookup: Callable[[str], str | None] = which,
    validator: Callable[[Path], CodexCliValidation] = validate_codex_cli,
) -> CodexCliDiscovery:
    """Discover Codex across supported installations and validate each candidate."""
    candidates = codex_cli_candidates(
        environment=environment,
        home=home,
        config_paths=config_paths,
        path_lookup=path_lookup,
    )
    failures: list[tuple[CodexCliSource, str]] = []
    for source, executable in candidates:
        if not executable.is_file():
            # Desktop candidates are conventional locations, not installation
            # evidence. An absent conventional file is therefore just skipped.
            if source in {CodexCliSource.ENVIRONMENT, CodexCliSource.CONFIG, CodexCliSource.PATH}:
                failures.append((source, "not_a_file"))
            continue
        result = validator(executable)
        if result.available:
            return CodexCliDiscovery(
                True,
                result.version,
                source,
                "available",
                f"{_SOURCE_LABELS[source]}からCodex CLIを確認しました",
                (("source", source.value),),
            )
        failures.append((source, result.error or "version_failed"))

    env = os.environ if environment is None else environment
    configured = env.get("CODEX_CLI_PATH", "").strip()
    if failures:
        source, cause = failures[0]
        return CodexCliDiscovery(
            False,
            source=source,
            cause=cause,
            detail=_cli_failure_detail(source, cause),
            detail_data=(("source", source.value), ("cause", cause)),
        )
    detail = (
        "CODEX_CLI_PATH が指定されていますが、Codex CLIが見つかりません"
        if configured
        else "Codex CLIが見つかりません"
    )
    return CodexCliDiscovery(
        False,
        cause="not_found",
        detail=detail,
        detail_data=(("cause", "not_found"),),
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def normalize_backend_status(payload: dict[str, Any]) -> CodexBackendStatus:
    """Normalize the read-only Backend response without trusting its shape."""
    codex = payload.get("codex")
    if not isinstance(codex, dict):
        raise ValueError("Backend診断APIのCodex状態が不正です")
    backend_value = payload.get("backend")
    backend_ok = backend_value in {"ok", True}
    return CodexBackendStatus(
        reachable=backend_ok,
        live_event_count=_non_negative_int(codex.get("live_event_count")),
        last_live_event_at=_parse_datetime(codex.get("last_live_event_at")),
        last_hook_event_at=_parse_datetime(codex.get("last_hook_event_at")),
        last_jsonl_event_at=_parse_datetime(codex.get("last_jsonl_event_at")),
        backend_started_at=_parse_datetime(codex.get("backend_started_at")),
        restore_state=str(codex.get("restore_state", "idle")).lower(),
        restored_sessions=_non_negative_int(codex.get("restored_sessions")),
        last_restored_at=_parse_datetime(codex.get("last_restored_at")),
        active_codex_sessions=_non_negative_int(
            codex.get("active_codex_sessions", codex.get("active_sessions", 0))
        ),
        current_input_mode=str(codex.get("current_input_mode", "IDLE")),
        monitored_sessions=_non_negative_int(codex.get("monitored_sessions")),
        tail_event_count=_non_negative_int(codex.get("tail_event_count")),
        deduplicated_events=_non_negative_int(codex.get("deduplicated_events")),
        jsonl_monitor=str(codex.get("jsonl_monitor", "disabled")),
        jsonl_monitor_health=str(codex.get("jsonl_monitor_health", "idle")),
        jsonl_parse_errors=_non_negative_int(codex.get("jsonl_parse_errors")),
        jsonl_file_access_failures=_non_negative_int(
            codex.get("jsonl_file_access_failures")
        ),
    )


def _handler_is_ai_office(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    command = handler.get("command")
    windows_command = handler.get("commandWindows")
    return any(
        isinstance(value, str) and "claude-office-hook" in value.lower()
        for value in (command, windows_command)
    )


def _event_has_ai_office_handler(groups: object) -> bool:
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if isinstance(handlers, list) and any(_handler_is_ai_office(item) for item in handlers):
            return True
    return False


def inspect_global_hooks(
    *,
    codex_home: Path,
    viewer_root: Path,
    hooks_text: str | None = None,
    config_text: str | None = None,
    launcher_exists: bool | None = None,
) -> GlobalHooksInspection:
    """Validate the exact AI Office Viewer hook chain without mutating it."""
    try:
        raw_hooks = (
            hooks_text
            if hooks_text is not None
            else (codex_home / "hooks.json").read_text(encoding="utf-8")
        )
    except OSError:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=0,
            missing_events=REQUIRED_CODEX_HOOK_EVENTS,
            detail="Codex global hooksが設定されていません",
        )
    try:
        document = json.loads(raw_hooks)
    except json.JSONDecodeError:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=0,
            missing_events=REQUIRED_CODEX_HOOK_EVENTS,
            detail="Codex global hooksのJSONが壊れています",
        )
    if not isinstance(document, dict) or not isinstance(document.get("hooks"), dict):
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=0,
            missing_events=REQUIRED_CODEX_HOOK_EVENTS,
            detail="Codex global hooksの形式が不正です",
        )

    hooks = document["hooks"]
    configured = tuple(
        event
        for event in REQUIRED_CODEX_HOOK_EVENTS
        if _event_has_ai_office_handler(hooks.get(event))
    )
    missing = tuple(event for event in REQUIRED_CODEX_HOOK_EVENTS if event not in configured)
    if missing:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=len(configured),
            missing_events=missing,
            detail=f"AI Office Viewer用hooksが不足しています（{len(configured)}/8）",
        )

    launcher_path = codex_home / "claude-office-hook.ps1"
    has_launcher = launcher_path.is_file() if launcher_exists is None else launcher_exists
    if not has_launcher:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=8,
            detail="Codex hook launcherが見つかりません",
            launcher_exists=False,
        )

    try:
        raw_config = (
            config_text
            if config_text is not None
            else (codex_home / "claude-office-config.json").read_text(encoding="utf-8")
        )
        config = json.loads(raw_config)
    except OSError:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=8,
            detail="Codex adapter設定が見つかりません",
            launcher_exists=True,
        )
    except json.JSONDecodeError:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=8,
            detail="Codex adapter設定のJSONが壊れています",
            launcher_exists=True,
        )
    if not isinstance(config, dict) or not isinstance(config.get("root"), str):
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=8,
            detail="Codex adapter設定のrootが不正です",
            launcher_exists=True,
            config_exists=True,
        )

    configured_root = Path(config["root"]).expanduser()
    try:
        root_matches = configured_root.resolve() == viewer_root.resolve()
    except OSError:
        root_matches = configured_root == viewer_root
    expected_adapter = viewer_root / "codex-adapter" / "hook.py"
    config_adapter = config.get("adapter")
    configured_adapter = (
        Path(config_adapter).expanduser() if isinstance(config_adapter, str) else None
    )
    try:
        adapter_matches = (
            configured_adapter is not None
            and configured_adapter.resolve() == expected_adapter.resolve()
        )
    except OSError:
        adapter_matches = configured_adapter == expected_adapter
    current_adapter_exists = expected_adapter.is_file()
    if not root_matches or not adapter_matches or not current_adapter_exists:
        return GlobalHooksInspection(
            state=DiagnosticState.ERROR,
            configured_events=8,
            detail="Codex adapterの参照先が現在のViewerと一致しません。修復してください",
            launcher_exists=True,
            config_exists=True,
            root_matches=root_matches,
            adapter_path_matches=adapter_matches and current_adapter_exists,
            configured_root=str(configured_root),
        )
    return GlobalHooksInspection(
        state=DiagnosticState.OK,
        configured_events=8,
        detail="8件のCodex global hooksが設定されています",
        launcher_exists=True,
        config_exists=True,
        root_matches=True,
        adapter_path_matches=True,
        configured_root=str(configured_root),
    )


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())


def _age_text(seconds: float | None) -> str:
    if seconds is None:
        return "未受信"
    if seconds < 5:
        return "たった今"
    if seconds < 60:
        return f"{int(seconds)}秒前"
    if seconds < 3600:
        return f"{int(seconds // 60)}分前"
    return f"{int(seconds // 3600)}時間前"


def build_diagnostic_report(
    *,
    cli_available: bool,
    cli_version: str | None,
    cli_discovery: CodexCliDiscovery | None = None,
    hooks_inspection: GlobalHooksInspection,
    adapter_available: bool,
    backend_status: CodexBackendStatus,
    now: datetime | None = None,
    stale_seconds: int = LIVE_EVENT_STALE_SECONDS,
) -> CodexDiagnosticReport:
    """Classify probes into independent checks and a Japanese recommendation."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    resolved_cli_available = cli_discovery.available if cli_discovery is not None else cli_available
    resolved_cli_version = cli_discovery.version if cli_discovery is not None else cli_version
    cli = DiagnosticCheck(
        DiagnosticState.OK if resolved_cli_available else DiagnosticState.WARNING,
        "利用可能" if resolved_cli_available else "要確認",
        (resolved_cli_version or "")
        if resolved_cli_available
        else (cli_discovery.detail if cli_discovery is not None else "Codex CLIを確認できません"),
        cli_discovery.detail_data if cli_discovery is not None else (),
    )
    hooks = DiagnosticCheck(
        hooks_inspection.state,
        "設定済み" if hooks_inspection.state == DiagnosticState.OK else "要修復",
        hooks_inspection.detail,
    )
    adapter = DiagnosticCheck(
        DiagnosticState.OK if adapter_available else DiagnosticState.ERROR,
        "正常" if adapter_available else "見つかりません",
        "" if adapter_available else "Codex Adapter本体を確認してください",
    )
    backend = DiagnosticCheck(
        DiagnosticState.OK if backend_status.reachable else DiagnosticState.ERROR,
        "接続可能" if backend_status.reachable else "接続できません",
        ""
        if backend_status.reachable
        else "AI Office Viewer Backendが停止している可能性があります",
    )

    monitor_active = backend_status.jsonl_monitor in {"monitoring", "healthy"}
    if not backend_status.reachable:
        jsonl_monitor = DiagnosticCheck(
            DiagnosticState.ERROR, "確認できません", "Backendへ接続できません"
        )
    elif monitor_active and backend_status.jsonl_monitor_health == "healthy":
        jsonl_monitor = DiagnosticCheck(
            DiagnosticState.OK,
            "監視中",
            f"{backend_status.monitored_sessions} sessions / "
            f"parse errors: {backend_status.jsonl_parse_errors}",
        )
    elif backend_status.monitored_sessions:
        jsonl_monitor = DiagnosticCheck(
            DiagnosticState.WARNING,
            "要確認",
            "監視対象はありますが、JSONL monitorが稼働していません",
        )
    else:
        jsonl_monitor = DiagnosticCheck(DiagnosticState.WAITING, "待機中")

    restoring = backend_status.restore_state in {"checking", "pending", "running"}
    restore_failed = backend_status.restore_state in {"failed", "error"}
    if restore_failed:
        restore = DiagnosticCheck(
            DiagnosticState.ERROR, "失敗", "Codexセッションの復元に失敗しました"
        )
    elif restoring:
        restore = DiagnosticCheck(
            DiagnosticState.WAITING, "確認中", "Codexセッションを確認しています"
        )
    elif backend_status.restored_sessions:
        restore = DiagnosticCheck(
            DiagnosticState.OK,
            f"{backend_status.restored_sessions}件復元",
            "Session RestoreとLive Eventsは別に判定されます",
        )
    else:
        restore = DiagnosticCheck(DiagnosticState.WAITING, "復元対象なし")

    last_age = _age_seconds(backend_status.last_live_event_at, current)
    startup_age = _age_seconds(backend_status.backend_started_at, current)
    restore_age = _age_seconds(backend_status.last_restored_at, current)
    # A restore count is historical and must not be treated as proof that a
    # session is still active. Only explicit Backend activity can make idle
    # live events suspicious.
    active_sessions = backend_status.active_codex_sessions
    tail_age = _age_seconds(backend_status.last_jsonl_event_at, current)
    recent_tail = tail_age is not None and tail_age <= stale_seconds
    recent_live = last_age is not None and last_age <= stale_seconds
    # A restored session does not itself generate live hook events.  Give the
    # user one monitoring window after startup or restore before flagging it.
    monitor_ages = [age for age in (startup_age, restore_age) if age is not None]
    monitoring_age = min(monitor_ages) if monitor_ages else None
    # A previously received event establishes that live monitoring has started,
    # even when older Backend versions do not provide their startup timestamp.
    expects_live = active_sessions > 0 and (
        (monitoring_age is not None and monitoring_age >= stale_seconds)
        or (last_age is not None and last_age >= stale_seconds)
    )
    if not backend_status.reachable:
        live = DiagnosticCheck(DiagnosticState.ERROR, "確認できません", "Backendへ接続できません")
    elif backend_status.current_input_mode == "HYBRID":
        live = DiagnosticCheck(
            DiagnosticState.OK,
            "Hybrid",
            f"Hooks + JSONL / 最終JSONL: {_age_text(tail_age)}",
        )
    elif backend_status.current_input_mode == "TAIL_FALLBACK" or recent_tail:
        live = DiagnosticCheck(
            DiagnosticState.OK,
            "JSONL fallback",
            f"JSONL監視中 / 最終更新: {_age_text(tail_age)}",
        )
    elif recent_live:
        live = DiagnosticCheck(
            DiagnosticState.OK,
            "受信中",
            f"最終受信: {_age_text(last_age)} / Live events: {backend_status.live_event_count}",
        )
    elif restoring:
        live = DiagnosticCheck(
            DiagnosticState.WAITING,
            "復元確認中",
            "Codexセッションの復元完了後にリアルタイムイベントを確認します",
        )
    elif expects_live:
        live = DiagnosticCheck(
            DiagnosticState.WARNING,
            "復元のみ",
            "Codexセッションは確認できましたが、新しいリアルタイムイベントを受信していません",
        )
    elif active_sessions > 0:
        detail = (
            "起動または復元直後のため、リアルタイムイベントを待っています"
            if monitoring_age is not None
            else "監視開始時刻を取得できないため、Codexの操作を待っています"
        )
        live = DiagnosticCheck(DiagnosticState.WAITING, "待機中", detail)
    else:
        live = DiagnosticCheck(
            DiagnosticState.WAITING,
            "待機中",
            f"最終受信: {_age_text(last_age)} / Codexの操作を待っています",
        )

    # CLI is optional for hook-driven integrations, and an idle live-event
    # stream is normal. Neither alone makes the entire integration an error.
    hard_error = next(
        (check for check in (hooks, adapter, backend) if check.state == DiagnosticState.ERROR),
        None,
    )
    if hard_error is not None:
        overall = DiagnosticCheck(
            DiagnosticState.ERROR, "エラー", "Codex連携の設定または接続に問題があります"
        )
        if hooks.state == DiagnosticState.ERROR:
            recommendation = "Global Hooksを修復してください"
        elif adapter.state == DiagnosticState.ERROR:
            recommendation = "Codex Adapterを修復またはAI Office Viewerを再インストールしてください"
        else:
            recommendation = "Backendを起動または再起動してから再診断してください"
    elif restore_failed:
        overall = DiagnosticCheck(DiagnosticState.WARNING, "要確認", restore.detail)
        recommendation = "Codexセッションの再読込をもう一度実行してください。Codex自体の動作には影響ありません。"
    elif live.state == DiagnosticState.WARNING:
        overall = DiagnosticCheck(DiagnosticState.WARNING, "要確認", live.detail)
        recommendation = (
            "Codex連携設定は正常ですが、現在のVS Code Codexからリアルタイムイベントを"
            "受信していません。Global Hooks設定後からVS Codeを再起動していない場合は、"
            "VS Codeを一度終了して再起動してください。"
        )
    elif cli.state == DiagnosticState.WARNING:
        overall = DiagnosticCheck(DiagnosticState.WARNING, "要確認", cli.detail, cli.detail_data)
        if cli_discovery is not None and cli_discovery.source == CodexCliSource.ENVIRONMENT:
            recommendation = "CODEX_CLI_PATH の設定を確認してください"
        elif cli_discovery is not None and cli_discovery.source is not None:
            recommendation = (
                f"{_SOURCE_LABELS[cli_discovery.source]}のCodex CLIを修復または再インストールして、"
                "再診断してください"
            )
        else:
            recommendation = "Codex CLIをインストールするか、CODEX_CLI_PATH を設定してから再診断してください"
    elif live.state == DiagnosticState.OK:
        overall = DiagnosticCheck(
            DiagnosticState.OK, "正常", "Codex連携はリアルタイムイベントを受信しています"
        )
        recommendation = ""
    else:
        overall = DiagnosticCheck(DiagnosticState.WAITING, "待機", "Codexの操作を待っています")
        recommendation = "Codexで操作を行うと、リアルタイムイベントの受信を確認できます。"

    return CodexDiagnosticReport(
        cli=cli,
        hooks=hooks,
        adapter=adapter,
        backend=backend,
        restore=restore,
        live_events=live,
        jsonl_monitor=jsonl_monitor,
        overall=overall,
        recommendation=recommendation,
        backend_status=backend_status,
        hooks_inspection=hooks_inspection,
        cli_version=resolved_cli_version,
        cli_discovery=cli_discovery,
    )
