"""Tests for Manager-owned shared settings handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manager import settings


def _test_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "config" / f".manager-{name}.json"


def test_restore_settings_have_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _test_path("defaults")
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    try:
        values, warning = settings.load_settings()
        assert warning is None
        assert values["restore_codex_sessions"] is True
        assert values["restore_window_minutes"] == 30
    finally:
        path.unlink(missing_ok=True)


def test_identity_and_clock_settings_match_backend_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _test_path("identity-clock")
    path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    try:
        values = settings.save_settings(
            {
                "clock_timezone_mode": "iana",
                "clock_timezone": "Asia/Tokyo",
                "main_agent_name_mode": "custom",
                "main_agent_custom_name": "My Main AI",
            }
        )
        assert values["clock_timezone"] == "Asia/Tokyo"
        assert values["main_agent_custom_name"] == "My Main AI"
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("clock_timezone_mode", "unknown"),
        ("clock_timezone", "Asia//Tokyo"),
        ("main_agent_name_mode", "unknown"),
        ("main_agent_custom_name", "x" * 51),
    ],
)
def test_identity_and_clock_settings_reject_invalid_values(key: str, value: object) -> None:
    with pytest.raises(ValueError):
        settings._validate({**settings.DEFAULTS, key: value})


def test_save_restore_settings_uses_shared_json(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _test_path("save")
    path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    try:
        values = settings.save_settings(
            {"restore_codex_sessions": False, "restore_window_minutes": 45}
        )
        assert values["restore_codex_sessions"] is False
        assert values["restore_window_minutes"] == 45
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["restore_codex_sessions"] is False
        assert saved["restore_window_minutes"] == 45
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("value", [0, 1441, True, "30"])
def test_restore_window_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="復元対象時間"):
        settings._validate({**settings.DEFAULTS, "restore_window_minutes": value})


def test_restore_enabled_must_be_boolean() -> None:
    with pytest.raises(ValueError, match="restore_codex_sessions"):
        settings._validate({**settings.DEFAULTS, "restore_codex_sessions": 1})


def test_owner_and_board_settings_are_preserved_in_shared_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _test_path("owner-board")
    path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    try:
        values = settings.save_settings(
            {
                "company_name": "AI開発室",
                "owner_name": "久保田兄貴",
                "owner_title": "Owner / Creator",
                "owner_message": "今日もAIチームと開発中",
                "board_mode": "daily_goals",
                "daily_goals": ["Codex連携の安定化"],
                "weekly_goals": ["AI Office Viewer完成"],
                "board_memo": "18時まで開発",
                "custom_board_title": "今月の重点",
                "custom_board_message": "完成度を上げる",
                "board_auto_rotate": True,
                "board_rotate_seconds": 10,
            }
        )
        assert values["owner_name"] == "久保田兄貴"
        assert values["daily_goals"] == ["Codex連携の安定化"]
        assert json.loads(path.read_text(encoding="utf-8"))["board_mode"] == "daily_goals"
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("owner_name", "x" * 51),
        ("owner_name", "   "),
        ("owner_title", "x" * 51),
        ("owner_message", "x" * 201),
        ("daily_goals", ["x" * 101]),
        ("daily_goals", ["目標"] * 51),
        ("board_memo", "x" * 501),
        ("board_auto_rotate", 1),
        ("board_rotate_seconds", 4),
        ("board_mode", "auto"),
    ],
)
def test_owner_and_board_settings_reject_invalid_values(key: str, value: object) -> None:
    with pytest.raises(ValueError, match=key):
        settings._validate({**settings.DEFAULTS, key: value})
