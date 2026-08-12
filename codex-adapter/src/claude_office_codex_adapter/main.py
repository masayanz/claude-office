"""Fail-open command-line entry point for a Codex lifecycle hook."""

import json
import sys
from collections.abc import Sequence
from contextlib import suppress

from claude_office_codex_adapter.diagnostics import run_check
from claude_office_codex_adapter.event_journal import append_event
from claude_office_codex_adapter.event_mapper import map_event
from claude_office_codex_adapter.sender import send_event


def _write_check_result() -> None:
    """Print a single safe JSON document for an explicit diagnostic request."""
    try:
        json.dump(run_check(), sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    except Exception:
        # A check must not leak filesystem paths, event data, or exceptions.
        with suppress(Exception):
            sys.stdout.write('{"ok":false,"error":"diagnostic_failed"}\n')


def main(argv: Sequence[str] | None = None) -> int:
    """Read one stdin event and best-effort forward it; always succeed."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--check"]:
        _write_check_result()
        return 0
    try:
        payload = json.load(sys.stdin)
        event = map_event(payload)
        if event is not None:
            # Persist the already-sanitized metadata before delivery. If the
            # Viewer is stopped, startup restoration can still catch up later.
            append_event(event)
            send_event(event)
    except Exception:
        # Hook integration is deliberately fail-open. No payload or exception text is logged.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
