"""API for shared Claude Office settings and the owner image."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

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
    owner_name: str | None = Field(default=None, max_length=120)


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


@router.post("/owner-image")
async def upload_owner_image(file: Annotated[UploadFile, File(...)]) -> dict[str, object]:
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=400, detail="PNG, JPEG, or WebP is required")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller")
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures.get(file.content_type, False):
        raise HTTPException(status_code=400, detail="Image content does not match its type")

    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[file.content_type]
    filename = f"owner-{secrets.token_urlsafe(12)}{extension}"
    OWNER_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (OWNER_IMAGE_DIR / filename).write_bytes(data)
    try:
        updated = save_settings({"owner_image_filename": filename})
    except ValueError as exc:
        (OWNER_IMAGE_DIR / filename).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated["owner_image_url"] = "/api/v1/settings/owner-image"
    return updated


@router.get("/owner-image")
async def get_owner_image() -> FileResponse:
    settings, _ = load_settings()
    filename = settings.get("owner_image_filename")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Owner image is not configured")
    image_path = (OWNER_IMAGE_DIR / Path(filename).name).resolve()
    if image_path.parent != OWNER_IMAGE_DIR.resolve() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Owner image is not available")
    return FileResponse(image_path)
