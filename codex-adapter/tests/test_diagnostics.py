import io

import pytest

from claude_office_codex_adapter import diagnostics
from claude_office_codex_adapter import main as main_module


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


class _Connection:
    status = 200
    instances: list["_Connection"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method: str, path: str) -> None:
        assert (method, path) == ("GET", "/health")

    def getresponse(self) -> _Response:
        return _Response(self.status)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    _Connection.status = 200
    _Connection.instances = []
    monkeypatch.setattr(diagnostics.http.client, "HTTPConnection", _Connection)
    monkeypatch.setattr(diagnostics, "_settings_loaded", lambda: True)


def test_check_reports_safe_adapter_and_backend_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        diagnostics,
        "get_event_endpoint",
        lambda: ("127.0.0.1", 8123, "/api/v1/events"),
    )

    result = diagnostics.run_check()

    assert result == {
        "ok": True,
        "settings_loaded": True,
        "endpoint": {
            "host": "127.0.0.1",
            "port": 8123,
            "path": "/api/v1/events",
            "loopback": True,
        },
        "python": {
            "version": ".".join(str(part) for part in diagnostics.sys.version_info[:3]),
            "supported": True,
        },
        "modules": {"json": True, "http_client": True},
        "backend": {"reachable": True, "status_code": 200},
    }
    assert _Connection.instances[0].closed is True


def test_check_does_not_probe_a_non_loopback_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        diagnostics,
        "get_event_endpoint",
        lambda: ("viewer.example", 8123, "/api/v1/events"),
    )

    result = diagnostics.run_check()

    assert result["ok"] is False
    assert result["endpoint"]["loopback"] is False
    assert result["backend"] == {"reachable": False, "status_code": None}
    assert _Connection.instances == []


def test_check_mode_does_not_map_journal_or_send_events(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(main_module.sys, "stdout", output)
    monkeypatch.setattr(main_module, "run_check", lambda: {"ok": True})
    monkeypatch.setattr(
        main_module,
        "map_event",
        lambda _payload: pytest.fail("check must not map an event"),
    )
    monkeypatch.setattr(
        main_module,
        "append_event",
        lambda _event: pytest.fail("check must not write a journal"),
    )
    monkeypatch.setattr(
        main_module,
        "send_event",
        lambda _event: pytest.fail("check must not send an event"),
    )

    assert main_module.main(["--check"]) == 0
    assert output.getvalue() == '{"ok":true}\n'


def test_check_mode_returns_safe_json_when_diagnostic_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(main_module.sys, "stdout", output)

    def _broken_check() -> dict[str, object]:
        raise RuntimeError("secret path must not be exposed")

    monkeypatch.setattr(main_module, "run_check", _broken_check)

    assert main_module.main(["--check"]) == 0
    assert output.getvalue() == '{"ok":false,"error":"diagnostic_failed"}\n'
