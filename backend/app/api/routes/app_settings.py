"""API for shared AI Office Viewer settings and the owner image."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse

from app.services.app_settings import OWNER_IMAGE_DIR, load_settings, save_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsUpdate(BaseModel):
    """Fields that can be changed from the Web settings screen."""

    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    backend_host: str | None = None
    backend_port: int | None = Field(default=None, ge=1024, le=65535)
    frontend_host: str | None = None
    frontend_port: int | None = Field(default=None, ge=1024, le=65535)
    open_browser_on_start: bool | None = None
    browser_mode: str | None = None
    company_name: str | None = Field(default=None, max_length=120)
    owner_name: str | None = Field(default=None, min_length=1, max_length=50)
    owner_title: str | None = Field(default=None, max_length=50)
    owner_message: str | None = Field(default=None, max_length=200)
    board_mode: Literal["todo", "daily_goals", "weekly_goals", "memo", "custom"] | None = None
    daily_goals: list[str] | None = Field(default=None, max_length=50)
    weekly_goals: list[str] | None = Field(default=None, max_length=50)
    board_memo: str | None = Field(default=None, max_length=500)
    custom_board_title: str | None = Field(default=None, max_length=50)
    custom_board_message: str | None = Field(default=None, max_length=500)
    board_auto_rotate: bool | None = None
    board_rotate_seconds: int | None = Field(default=None, ge=5, le=3600)
    stop_servers_on_manager_exit: bool | None = None
    restore_codex_sessions: bool | None = None
    restore_window_minutes: int | None = Field(default=None, ge=1, le=1440)


def _public_settings() -> dict[str, object]:
    settings, warning = load_settings()
    settings["owner_image_url"] = (
        "/api/v1/settings/owner-image" if settings.get("owner_image_filename") else None
    )
    if warning:
        settings["warning"] = warning
    return settings


@router.get("")
async def get_app_settings() -> dict[str, object]:
    return _public_settings()


@router.put("")
async def update_app_settings(body: AppSettingsUpdate) -> dict[str, object]:
    try:
        updated = save_settings(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated["owner_image_url"] = (
        "/api/v1/settings/owner-image" if updated.get("owner_image_filename") else None
    )
    return updated


_MAX_OWNER_IMAGE_BYTES = 5 * 1024 * 1024


def _validate_image_content(data: bytes, content_type: str | None) -> str:
    """Return the safe extension after checking declared and binary image formats."""
    signatures = {
        "image/png": (".png", data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"),
        "image/jpeg": (".jpg", data.startswith(b"\xff\xd8\xff")),
        "image/webp": (
            ".webp",
            len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        ),
    }
    image = signatures.get(content_type or "")
    if image is None:
        raise HTTPException(status_code=400, detail="PNG, JPEG, or WebP is required")
    extension, is_valid = image
    if not is_valid:
        raise HTTPException(status_code=400, detail="Image content does not match its type")
    return extension


async def _read_owner_image(file: UploadFile) -> bytes:
    """Read at most one byte beyond the image limit to bound memory use."""
    data = await file.read(_MAX_OWNER_IMAGE_BYTES + 1)
    if len(data) > _MAX_OWNER_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller")
    return data


def _owner_image_path(filename: str) -> Path | None:
    """Resolve a settings filename only when it stays in the managed image directory."""
    if Path(filename).name != filename:
        return None
    image_path = (OWNER_IMAGE_DIR / filename).resolve()
    try:
        image_path.relative_to(OWNER_IMAGE_DIR.resolve())
    except ValueError:
        return None
    return image_path


@router.post("/owner-image")
async def upload_owner_image(file: Annotated[UploadFile, File(...)]) -> dict[str, object]:
    data = await _read_owner_image(file)
    extension = _validate_image_content(data, file.content_type)
    filename = f"owner-{uuid4().hex}{extension}"
    OWNER_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_path = OWNER_IMAGE_DIR / filename
    image_path.write_bytes(data)
    previous, _ = load_settings()
    try:
        updated = save_settings({"owner_image_filename": filename})
    except ValueError as exc:
        image_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    old_filename = previous.get("owner_image_filename")
    if isinstance(old_filename, str):
        old_image_path = _owner_image_path(old_filename)
        if old_image_path is not None and old_image_path != image_path:
            old_image_path.unlink(missing_ok=True)
    updated["owner_image_url"] = "/api/v1/settings/owner-image"
    return updated


@router.get("/owner-image")
async def get_owner_image() -> FileResponse:
    settings, _ = load_settings()
    filename = settings.get("owner_image_filename")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Owner image is not configured")
    image_path = _owner_image_path(filename)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Owner image is not available")
    return FileResponse(image_path)


@router.delete("/owner-image")
async def delete_owner_image() -> dict[str, object]:
    """Remove the custom image and make clients fall back to the default owner sprite."""
    current, _ = load_settings()
    filename = current.get("owner_image_filename")
    try:
        updated = save_settings({"owner_image_filename": None})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(filename, str):
        image_path = _owner_image_path(filename)
        if image_path is not None:
            image_path.unlink(missing_ok=True)
    updated["owner_image_url"] = None
    return updated
