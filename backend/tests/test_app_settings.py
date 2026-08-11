"""Regression tests for the shared app settings file."""

import json
from pathlib import Path

import pytest

from app.services.app_settings import (
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    validate_settings,
)


def _test_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "config" / f".{name}"


def test_missing_settings_falls_back_to_defaults() -> None:
    values, warning = load_settings(_test_path("missing-settings.json"))
    assert values == DEFAULT_SETTINGS
    assert warning


def test_invalid_json_falls_back_to_defaults() -> None:
    path = _test_path("invalid-settings.json")
    path.write_text("{not-json", encoding="utf-8")
    try:
        values, warning = load_settings(path)
        assert values == DEFAULT_SETTINGS
        assert warning
    finally:
        path.unlink(missing_ok=True)


def test_save_settings_merges_and_writes_atomically() -> None:
    path = _test_path("save-settings.json")
    path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")
    try:
        values = save_settings({"backend_port": 8100, "company_name": "テスト社"}, path)
        assert values["backend_port"] == 8100
        assert values["company_name"] == "テスト社"
        assert json.loads(path.read_text(encoding="utf-8"))["frontend_port"] == 3000
    finally:
        path.unlink(missing_ok=True)


def test_codex_restore_settings_default_and_round_trip() -> None:
    assert DEFAULT_SETTINGS["restore_codex_sessions"] is True
    assert DEFAULT_SETTINGS["restore_window_minutes"] == 30

    path = _test_path("restore-settings.json")
    path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")
    try:
        values = save_settings(
            {"restore_codex_sessions": False, "restore_window_minutes": 90},
            path,
        )
        assert values["restore_codex_sessions"] is False
        assert values["restore_window_minutes"] == 90
        loaded, warning = load_settings(path)
        assert warning is None
        assert loaded == values
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("backend_port", [80, 65536, True])
def test_validate_rejects_invalid_ports(backend_port: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "backend_port": backend_port})


@pytest.mark.parametrize("enabled", [0, 1, "true", None])
def test_validate_rejects_non_boolean_restore_enabled(enabled: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "restore_codex_sessions": enabled})


@pytest.mark.parametrize("minutes", [0, 1441, True, 30.5, "30"])
def test_validate_rejects_invalid_restore_window(minutes: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "restore_window_minutes": minutes})
