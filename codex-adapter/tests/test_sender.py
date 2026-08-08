import http.client

import pytest

from claude_office_codex_adapter import sender


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeConnection:
    response_status = 200
    failure: Exception | None = None
    instances: list["FakeConnection"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False
        self.__class__.instances.append(self)

    def request(
        self, method: str, path: str, *, body: bytes, headers: dict[str, str]
    ) -> None:
        self.requests.append((method, path, body, headers))
        if self.failure is not None:
            raise self.failure

    def getresponse(self) -> FakeResponse:
        if self.failure is not None:
            raise self.failure
        return FakeResponse(self.response_status)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.response_status = 200
    FakeConnection.failure = None
    FakeConnection.instances = []
    monkeypatch.setattr(sender.http.client, "HTTPConnection", FakeConnection)


def test_sender_accepts_200() -> None:
    assert sender.send_event({"event_type": "stop"}) is True

    connection = FakeConnection.instances[0]
    assert (connection.host, connection.port) == ("127.0.0.1", 8000)
    assert connection.timeout <= 0.5
    assert connection.requests[0][0:2] == ("POST", "/api/v1/events")
    assert connection.closed is True


@pytest.mark.parametrize("status", [400, 500])
def test_sender_suppresses_http_errors(status: int) -> None:
    FakeConnection.response_status = status

    assert sender.send_event({"event_type": "stop"}) is False
    assert len(FakeConnection.instances) == 1


@pytest.mark.parametrize(
    "exception",
    [
        ConnectionRefusedError("connection refused"),
        TimeoutError("timed out"),
        http.client.HTTPException("bad response"),
    ],
    ids=["connection-refused", "timeout", "http-error"],
)
def test_sender_suppresses_transport_errors(exception: Exception) -> None:
    FakeConnection.failure = exception

    assert sender.send_event({"event_type": "stop"}) is False
    assert len(FakeConnection.instances) == 1
    assert FakeConnection.instances[0].closed is True
