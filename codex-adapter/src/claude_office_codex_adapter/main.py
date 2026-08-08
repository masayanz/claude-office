"""Fail-open command-line entry point for a Codex lifecycle hook."""

import json
import sys

from claude_office_codex_adapter.event_mapper import map_event
from claude_office_codex_adapter.sender import send_event


def main() -> int:
    """Read one stdin event and best-effort forward it; always succeed."""
    try:
        payload = json.load(sys.stdin)
        event = map_event(payload)
        if event is not None:
            send_event(event)
    except Exception:
        # Hook integration is deliberately fail-open. No payload or exception text is logged.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
