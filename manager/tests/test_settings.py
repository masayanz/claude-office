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
