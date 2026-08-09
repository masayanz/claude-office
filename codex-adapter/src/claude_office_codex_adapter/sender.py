"""Best-effort, single-attempt delivery to the local AI Office Viewer API."""

import http.client
import json
from contextlib import suppress

from claude_office_codex_adapter.config import (
    HTTP_TIMEOUT_SECONDS,
    get_event_endpoint,
)


def send_event(event: dict[str, object]) -> bool:
    """Send one event and suppress every transport failure."""
    connection: http.client.HTTPConnection | None = None
    try:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # A direct HTTPConnection avoids environment proxies and never follows redirects,
        # so hook data cannot leave the fixed loopback destination.
        host, port, path = get_event_endpoint()
        connection = http.client.HTTPConnection(host, port, timeout=HTTP_TIMEOUT_SECONDS)
        connection.request(
            "POST",
            path,
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
