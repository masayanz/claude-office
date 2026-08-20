import json
from pathlib import Path

from claude_office_codex_adapter import config


def _test_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / ".adapter-test-settings.json"


def test_adapter_follows_shared_backend_port(monkeypatch) -> None:
    path = _test_path()
    path.write_text(
        json.dumps({"backend_host": "127.0.0.1", "backend_port": 8123}), encoding="utf-8"
    )
    try:
        monkeypatch.setattr(config, "_settings_path", lambda: path)
        assert config.get_event_endpoint() == ("127.0.0.1", 8123, "/api/v1/events")
    finally:
        path.unlink(missing_ok=True)


def test_adapter_fails_open_on_corrupt_shared_config(monkeypatch) -> None:
    path = _test_path()
    path.write_text("broken", encoding="utf-8")
    try:
        monkeypatch.setattr(config, "_settings_path", lambda: path)
        assert config.get_event_endpoint() == (
            config.EVENTS_HOST,
            config.EVENTS_PORT,
            config.EVENTS_PATH,
        )
    finally:
        path.unlink(missing_ok=True)


def test_frozen_adapter_finds_settings_from_executable(tmp_path, monkeypatch) -> None:
    root = tmp_path / "AI Office Viewer_日本語"
    adapter = root / "runtime" / "codex-adapter" / "AI-Office-Viewer-Codex-Adapter.exe"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b"")
    settings = root / "config" / "app-settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("CLAUDE_OFFICE_ROOT", raising=False)
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(adapter))

    assert config._settings_path() == settings
