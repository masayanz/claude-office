from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from manager.process_manager import (
    HEALTH_ERROR_AFTER,
    STATE_DEGRADED,
    STATE_ERROR,
    STATE_RUNNING,
    HealthSnapshot,
    ServerLifecycleManager,
)


def _manager() -> ServerLifecycleManager:
    manager = object.__new__(ServerLifecycleManager)
    manager.processes = {"backend": SimpleNamespace(pid=1234, poll=lambda: None)}
    manager._records = {}
    manager._states = {"backend": STATE_RUNNING, "frontend": "stopped"}
    manager._state_reasons = {"backend": "test", "frontend": "test"}
    manager._health_snapshots = {}
    manager._log_streams = {}
    return manager


def test_liveness_failures_degrade_then_error_without_restart(monkeypatch) -> None:
    manager = _manager()
    calls = {"count": 0}

    def probe(_service: str, _process_alive: bool) -> HealthSnapshot:
        calls["count"] += 1
        return HealthSnapshot(
            "backend",
            process_alive=True,
            port_listening=True,
            liveness_ok=False,
            consecutive_liveness_failures=calls["count"],
            detail="timeout",
        )

    monkeypatch.setattr(manager, "_probe_service", probe)
    monkeypatch.setattr(manager, "_log_process_event", lambda *_args: None)
    monkeypatch.setattr(manager, "_record_verification", lambda _record: "match")

    states = [manager.status("backend").state for _ in range(HEALTH_ERROR_AFTER)]

    assert states[:3] == [STATE_RUNNING, STATE_RUNNING, STATE_DEGRADED]
    assert states[-1] == STATE_ERROR
    assert manager.processes["backend"].pid == 1234


def test_readiness_failure_is_degraded_and_recovers(monkeypatch) -> None:
    manager = _manager()
    probes = iter(
        [
            HealthSnapshot(
                "backend", True, True, True, False, True, 0, 1, detail="DB unavailable"
            ),
            HealthSnapshot("backend", True, True, True, True, True),
        ]
    )
    monkeypatch.setattr(manager, "_probe_service", lambda *_args: next(probes))
    monkeypatch.setattr(manager, "_log_process_event", lambda *_args: None)

    assert manager.status("backend").state == STATE_DEGRADED
    recovered = manager.status("backend")
    assert recovered.state == STATE_RUNNING
    assert recovered.process_alive is True
    assert recovered.readiness_ok is True


def test_lifecycle_commands_manage_real_server_processes(
    monkeypatch, tmp_path: Path, request
) -> None:
    """Exercise the lifecycle core with two real long-lived child processes."""
    import manager.process_manager as process_manager

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(process_manager, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(process_manager, "LOG_DIR", runtime / "logs")
    monkeypatch.setattr(process_manager, "PID_PATH", runtime / "processes.json")
    manager = ServerLifecycleManager()
    request.addfinalizer(manager.stop_all)
    stop_code = (
        "import signal, time; "
        "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), lambda *_: exit(0)); "
        "signal.signal(signal.SIGTERM, lambda *_: exit(0)); "
        "time.sleep(60)"
    )
    monkeypatch.setattr(
        manager,
        "_command",
        lambda _service: ([sys.executable, "-c", stop_code], Path.cwd()),
    )
    monkeypatch.setattr(manager, "_healthy", lambda service: service in manager.processes)
    monkeypatch.setattr(manager, "_readiness", lambda _service: (True, True, "ready"))
    monkeypatch.setattr(manager, "_port_in_use", lambda _service: False)
    monkeypatch.setattr(manager, "_backend_identity", lambda: None)

    first = manager.start_all()
    assert set(first) == {"backend", "frontend"}
    snapshot = manager.snapshot()
    pids = {service: status.pid for service, status in snapshot.items()}
    assert all(status.process_alive for status in snapshot.values()), snapshot
    assert pids["backend"] and pids["frontend"]

    stopped = manager.stop_all()
    assert all(not status.running for status in stopped.values())
    assert manager.processes == {}
    assert "backend" not in json.loads(
        (runtime / "processes.json").read_text(encoding="utf-8")
    )

    # A second start is independent and cannot reuse the old process handles.
    manager.start_all()
    second_snapshot = manager.snapshot()
    assert all(status.process_alive for status in second_snapshot.values())
    assert any(second_snapshot[service].pid != pids[service] for service in pids)
