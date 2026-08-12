"""Read-only, payload-free integration status for Manager and Viewer."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.codex_hybrid import get_codex_hybrid_coordinator
from app.core.codex_integration import get_codex_live_telemetry
from app.core.codex_session_restorer import get_codex_session_restorer

router = APIRouter(prefix="/system/integration-status", tags=["system"])


@router.get("")
async def integration_status() -> dict[str, object]:
    restore = get_codex_session_restorer().status()
    telemetry = get_codex_live_telemetry().snapshot()
    hybrid = get_codex_hybrid_coordinator().status(
        restored_sessions=int(restore.get("restored_sessions") or 0)
    )
    for key in ("last_hook_event_at", "last_jsonl_event_at"):
        value = hybrid.get(key)
        if isinstance(value, datetime):
            hybrid[key] = value.astimezone(UTC).isoformat()
    return {
        "backend": "ok",
        "codex": {
            **telemetry,
            "last_restored_at": restore.get("last_run"),
            "restored_sessions": int(restore.get("restored_sessions") or 0),
            "restore_state": str(restore.get("state") or "idle"),
            **hybrid,
        },
    }
