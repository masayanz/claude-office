"""Codex integration diagnostics shared by the Manager UI and tray host.

The module deliberately keeps the classification logic independent from Qt and
subprocess calls.  Filesystem and process probes can supply their results to
``build_diagnostic_report`` and the result is safe to render or log: it never
contains hook payloads, prompts, credentials, or full session paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """A compact, Japanese-renderable check result."""

    state: DiagnosticState
    summary: str
    detail: str = ""


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
    overall: DiagnosticCheck
    recommendation: str
    backend_status: CodexBackendStatus
    hooks_inspection: GlobalHooksInspection
    cli_version: str | None = None


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
        backend_started_at=_parse_datetime(codex.get("backend_started_at")),
        restore_state=str(codex.get("restore_state", "idle")).lower(),
        restored_sessions=_non_negative_int(codex.get("restored_sessions")),
        last_restored_at=_parse_datetime(codex.get("last_restored_at")),
        active_codex_sessions=_non_negative_int(
            codex.get("active_codex_sessions", codex.get("active_sessions", 0))
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
    hooks_inspection: GlobalHooksInspection,
    adapter_available: bool,
    backend_status: CodexBackendStatus,
    now: datetime | None = None,
    stale_seconds: int = LIVE_EVENT_STALE_SECONDS,
) -> CodexDiagnosticReport:
    """Classify probes into independent checks and a Japanese recommendation."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cli = DiagnosticCheck(
        DiagnosticState.OK if cli_available else DiagnosticState.ERROR,
        "利用可能" if cli_available else "利用できません",
        cli_version or "",
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
    active_sessions = max(backend_status.active_codex_sessions, backend_status.restored_sessions)
    recent_live = last_age is not None and last_age <= stale_seconds
    expects_live = active_sessions > 0 and (startup_age is None or startup_age >= stale_seconds)
    if not backend_status.reachable:
        live = DiagnosticCheck(DiagnosticState.ERROR, "確認できません", "Backendへ接続できません")
    elif recent_live:
        live = DiagnosticCheck(
            DiagnosticState.OK,
            "受信中",
            f"最終受信: {_age_text(last_age)} / Live events: {backend_status.live_event_count}",
        )
    elif expects_live:
        live = DiagnosticCheck(
            DiagnosticState.WARNING,
            "復元のみ",
            "Codexセッションは確認できましたが、新しいリアルタイムイベントを受信していません",
        )
    else:
        live = DiagnosticCheck(
            DiagnosticState.WAITING,
            "待機中",
            f"最終受信: {_age_text(last_age)} / Codexの操作を待っています",
        )

    hard_error = next(
        (check for check in (cli, hooks, adapter, backend) if check.state == DiagnosticState.ERROR),
        None,
    )
    if hard_error is not None:
        overall = DiagnosticCheck(
            DiagnosticState.ERROR, "エラー", "Codex連携の設定または接続に問題があります"
        )
        recommendation = (
            "Global Hooksを修復してください"
            if hooks.state == DiagnosticState.ERROR
            else "Backendを起動または再起動してから再診断してください"
        )
    elif live.state == DiagnosticState.WARNING:
        overall = DiagnosticCheck(DiagnosticState.WARNING, "要確認", live.detail)
        recommendation = (
            "Codex連携設定は正常ですが、現在のVS Code Codexからリアルタイムイベントを"
            "受信していません。Global Hooks設定後からVS Codeを再起動していない場合は、"
            "VS Codeを一度終了して再起動してください。"
        )
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
        overall=overall,
        recommendation=recommendation,
        backend_status=backend_status,
        hooks_inspection=hooks_inspection,
        cli_version=cli_version,
    )
