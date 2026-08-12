"""Read-only, payload-free integration status for Manager and Viewer."""

from fastapi import APIRouter

from app.core.codex_integration import get_codex_live_telemetry
from app.core.codex_session_restorer import get_codex_session_restorer

router = APIRouter(prefix="/system/integration-status", tags=["system"])


@router.get("")
async def integration_status() -> dict[str, object]:
    restore = get_codex_session_restorer().status()
    telemetry = get_codex_live_telemetry().snapshot()
    return {
        "backend": "ok",
        "codex": {
            **telemetry,
            "last_restored_at": restore.get("last_run"),
            "restored_sessions": int(restore.get("restored_sessions") or 0),
            "restore_state": str(restore.get("state") or "idle"),
        },
    }
