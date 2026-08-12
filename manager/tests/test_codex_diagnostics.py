"""Unit tests for the Manager's payload-free Codex diagnostic model."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from manager.codex_diagnostics import (
    REQUIRED_CODEX_HOOK_EVENTS,
    CodexBackendStatus,
    DiagnosticState,
    GlobalHooksInspection,
    build_diagnostic_report,
    inspect_global_hooks,
    normalize_backend_status,
)


def _hooks_document(events: tuple[str, ...] = REQUIRED_CODEX_HOOK_EVENTS) -> str:
    return json.dumps(
        {
            "hooks": {
                event: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "commandWindows": (
                                    "powershell.exe -File "
                                    "C:/User/.codex/claude-office-hook.ps1"
                                ),
                            }
                        ]
                    }
                ]
                for event in events
            }
        }
    )


def _config(root: Path) -> str:
    return json.dumps({"root": str(root), "adapter": str(root / "codex-adapter" / "hook.py")})


def _inspection(tmp_path: Path) -> GlobalHooksInspection:
    root = tmp_path / "viewer"
    (root / "codex-adapter").mkdir(parents=True)
    (root / "codex-adapter" / "hook.py").write_text("", encoding="utf-8")
    return inspect_global_hooks(
        codex_home=tmp_path / "codex-home",
        viewer_root=root,
        hooks_text=_hooks_document(),
        config_text=_config(root),
        launcher_exists=True,
    )


def test_hooks_without_file_are_not_configured(tmp_path: Path) -> None:
    result = inspect_global_hooks(codex_home=tmp_path, viewer_root=tmp_path / "viewer")

    assert result.state == DiagnosticState.ERROR
    assert result.missing_events == REQUIRED_CODEX_HOOK_EVENTS
    assert "設定されていません" in result.detail


def test_invalid_hooks_json_is_reported(tmp_path: Path) -> None:
    result = inspect_global_hooks(
        codex_home=tmp_path,
        viewer_root=tmp_path / "viewer",
        hooks_text="{not-json",
    )

    assert result.state == DiagnosticState.ERROR
    assert "JSON" in result.detail


def test_eight_hooks_and_matching_launcher_config_are_healthy(tmp_path: Path) -> None:
    result = _inspection(tmp_path)

    assert result.state == DiagnosticState.OK
    assert result.configured_events == 8
    assert result.root_matches is True
    assert result.adapter_path_matches is True


def test_missing_one_hook_is_an_error_even_when_other_handlers_exist(tmp_path: Path) -> None:
    events = tuple(event for event in REQUIRED_CODEX_HOOK_EVENTS if event != "Stop")
    result = inspect_global_hooks(
        codex_home=tmp_path / "home",
        viewer_root=tmp_path / "viewer",
        hooks_text=_hooks_document(events),
        launcher_exists=True,
    )

    assert result.state == DiagnosticState.ERROR
    assert result.configured_events == 7
    assert result.missing_events == ("Stop",)


def test_moved_viewer_root_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "viewer"
    (root / "codex-adapter").mkdir(parents=True)
    (root / "codex-adapter" / "hook.py").write_text("", encoding="utf-8")
    old_root = tmp_path / "old-viewer"
    result = inspect_global_hooks(
        codex_home=tmp_path / "home",
        viewer_root=root,
        hooks_text=_hooks_document(),
        config_text=_config(old_root),
        launcher_exists=True,
    )

    assert result.state == DiagnosticState.ERROR
    assert result.root_matches is False
    assert "修復" in result.detail


def test_backend_payload_tracks_live_separately_from_restore() -> None:
    result = normalize_backend_status(
        {
            "backend": "ok",
            "codex": {
                "live_event_count": 3,
                "last_live_event_at": "2026-08-12T00:00:03Z",
                "backend_started_at": "2026-08-12T00:00:00Z",
                "restored_sessions": 1,
                "restore_state": "completed",
                "last_restored_at": "2026-08-12T00:00:02Z",
                "active_codex_sessions": 1,
            },
        }
    )

    assert result.reachable is True
    assert result.live_event_count == 3
    assert result.restored_sessions == 1
    assert result.active_codex_sessions == 1


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        (CodexBackendStatus(reachable=True), DiagnosticState.WAITING),
        (
            CodexBackendStatus(
                reachable=True,
                restored_sessions=1,
                backend_started_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
            DiagnosticState.WARNING,
        ),
        (
            CodexBackendStatus(
                reachable=True,
                restored_sessions=1,
                last_live_event_at=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
            ),
            DiagnosticState.OK,
        ),
    ],
)
def test_live_classification_separates_waiting_restore_only_and_live(
    tmp_path: Path, backend: CodexBackendStatus, expected: DiagnosticState
) -> None:
    report = build_diagnostic_report(
        cli_available=True,
        cli_version="codex 1.0",
        hooks_inspection=_inspection(tmp_path),
        adapter_available=True,
        backend_status=backend,
        now=datetime(2026, 8, 12, 0, 1, 10, tzinfo=UTC),
    )

    assert report.live_events.state == expected
    assert report.overall.state == expected


def test_live_event_older_than_threshold_with_active_session_is_warning(tmp_path: Path) -> None:
    report = build_diagnostic_report(
        cli_available=True,
        cli_version="codex 1.0",
        hooks_inspection=_inspection(tmp_path),
        adapter_available=True,
        backend_status=CodexBackendStatus(
            reachable=True,
            active_codex_sessions=1,
            last_live_event_at=datetime(2026, 8, 12, tzinfo=UTC),
        ),
        now=datetime(2026, 8, 12, tzinfo=UTC) + timedelta(seconds=61),
    )

    assert report.live_events.state == DiagnosticState.WARNING
    assert "VS Code" in report.recommendation


def test_missing_adapter_is_an_overall_error(tmp_path: Path) -> None:
    report = build_diagnostic_report(
        cli_available=True,
        cli_version="codex 1.0",
        hooks_inspection=_inspection(tmp_path),
        adapter_available=False,
        backend_status=CodexBackendStatus(reachable=True),
    )

    assert report.adapter.state == DiagnosticState.ERROR
    assert report.overall.state == DiagnosticState.ERROR
