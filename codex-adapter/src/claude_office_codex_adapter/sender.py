"""Best-effort, single-attempt delivery to the local Claude Office API."""

import http.client
import json
from contextlib import suppress

from claude_office_codex_adapter.config import (
    EVENTS_HOST,
    EVENTS_PATH,
    EVENTS_PORT,
    HTTP_TIMEOUT_SECONDS,
)


def send_event(event: dict[str, object]) -> bool:
    """Send one event and suppress every transport failure."""
    connection: http.client.HTTPConnection | None = None
    try:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # A direct HTTPConnection avoids environment proxies and never follows redirects,
        # so hook data cannot leave the fixed loopback destination.
        connection = http.client.HTTPConnection(
            EVENTS_HOST,
            EVENTS_PORT,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        connection.request(
            "POST",
            EVENTS_PATH,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return 200 <= response.status < 300
    except (http.client.HTTPException, TimeoutError, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()
