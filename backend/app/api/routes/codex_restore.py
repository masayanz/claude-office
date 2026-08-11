"""Status and manual trigger endpoints for Codex startup restoration."""

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import get_settings
from app.core.codex_session_restorer import get_codex_session_restorer
from app.core.event_processor import EventProcessor, get_event_processor

router = APIRouter(prefix="/codex/restore", tags=["codex"])
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _validate_browser_origin(request: Request) -> None:
    """Block cross-site browser triggers while allowing Manager's no-Origin call."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    normalized = origin.rstrip("/")
    allowed = {
        configured.rstrip("/")
        for configured in get_settings().BACKEND_CORS_ORIGINS
        if urlparse(configured).hostname in _LOCALHOST_HOSTS
    }
    if normalized not in allowed:
        raise HTTPException(status_code=403, detail="Origin not allowed")


@router.get("/status")
async def get_restore_status() -> dict[str, str | int | None]:
    return get_codex_session_restorer().status()


@router.post("")
async def restore_codex_sessions(
    request: Request,
    event_processor: Annotated[EventProcessor, Depends(get_event_processor)],
) -> dict[str, str | int | None]:
    _validate_browser_origin(request)
    return get_codex_session_restorer().start(event_processor)
