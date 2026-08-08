import io

import pytest

from claude_office_codex_adapter import main as main_module


def test_main_sends_normal_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_module.sys,
        "stdin",
        io.StringIO('{"hook_event_name":"Stop","session_id":"session-1"}'),
    )
    monkeypatch.setattr(main_module, "send_event", lambda event: sent.append(event) or True)

    assert main_module.main() == 0
    assert len(sent) == 1
    assert sent[0]["event_type"] == "stop"


@pytest.mark.parametrize("stdin", ["{broken", ""])
def test_main_suppresses_broken_or_empty_stdin(
    monkeypatch: pytest.MonkeyPatch, stdin: str
) -> None:
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(
        main_module,
        "send_event",
        lambda _event: pytest.fail("sender must not be called"),
    )

    assert main_module.main() == 0


def test_main_suppresses_mapper_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO("{}"))

    def broken_mapper(_payload: object) -> None:
        raise RuntimeError("mapper failed")

    monkeypatch.setattr(main_module, "map_event", broken_mapper)
    assert main_module.main() == 0


def test_main_suppresses_sender_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(main_module, "map_event", lambda _payload: {"event_type": "stop"})

    def broken_sender(_event: dict[str, object]) -> None:
        raise RuntimeError("sender failed")

    monkeypatch.setattr(main_module, "send_event", broken_sender)
    assert main_module.main() == 0
