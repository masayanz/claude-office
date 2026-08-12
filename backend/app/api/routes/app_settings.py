"""API for shared AI Office Viewer settings and the owner image."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse

from app.services.app_settings import OWNER_IMAGE_DIR, load_settings, save_settings
from app.services.owner_image import (
    MAX_OWNER_IMAGE_BYTES,
    MAX_OWNER_IMAGE_DIMENSION,
    MIN_OWNER_IMAGE_DIMENSION,
    OWNER_IMAGE_CONTENT_TYPE,
    OWNER_IMAGE_TARGET_SIZE,
    OwnerImageValidationError,
    normalize_owner_image,
)

router = APIRouter(prefix="/settings", tags=["settings"])

OWNER_IMAGE_CONSTRAINTS = {
    "formats": ["PNG", "JPEG", "WebP"],
    "max_bytes": MAX_OWNER_IMAGE_BYTES,
    "min_dimension": MIN_OWNER_IMAGE_DIMENSION,
    "max_dimension": MAX_OWNER_IMAGE_DIMENSION,
    "target_dimension": OWNER_IMAGE_TARGET_SIZE,
}
_UNREADABLE_OWNER_IMAGE_MESSAGE = (
    "現在設定されているオーナー画像を読み込めません。新しい画像を設定してください。"
)


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
    clock_timezone_mode: Literal["local", "iana"] | None = None
    clock_timezone: str | None = Field(default=None, max_length=100)
    main_agent_name_mode: Literal["auto", "custom"] | None = None
    main_agent_custom_name: str | None = Field(default=None, max_length=50)


def _public_settings() -> dict[str, object]:
    settings, warning = load_settings()
    settings, image_warning = _ensure_normalized_owner_image(settings)
    settings["owner_image_url"] = (
        "/api/v1/settings/owner-image"
        if settings.get("owner_image_filename") and image_warning is None
        else None
    )
    if warning:
        settings["warning"] = warning
    if image_warning:
        settings["owner_image_warning"] = image_warning
    settings["owner_image_constraints"] = OWNER_IMAGE_CONSTRAINTS
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
    updated["owner_image_constraints"] = OWNER_IMAGE_CONSTRAINTS
    return updated


async def _read_owner_image(file: UploadFile) -> bytes:
    """Read at most one byte beyond the image limit to bound memory use."""
    data = await file.read(MAX_OWNER_IMAGE_BYTES + 1)
    if len(data) > MAX_OWNER_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="画像サイズが大きすぎます。5MB以下のPNG・JPEG・WebP画像を選択してください。",
        )
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


def _write_owner_image(data: bytes) -> tuple[str, Path]:
    """Atomically persist a normalized Viewer asset with a cache-busting name."""
    filename = f"owner-avatar-{uuid4().hex}.webp"
    OWNER_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_path = OWNER_IMAGE_DIR / filename
    with NamedTemporaryFile(dir=OWNER_IMAGE_DIR, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    temporary_path.replace(image_path)
    return filename, image_path


def _ensure_normalized_owner_image(
    current: dict[str, object],
) -> tuple[dict[str, object], str | None]:
    """Migrate a legacy raw upload once, or hide an unreadable old upload.

    New uploads already have the ``owner-avatar-*.webp`` form.  Earlier
    versions saved client bytes directly, so normalize those files before
    advertising them to the Viewer.
    """
    filename = current.get("owner_image_filename")
    if not isinstance(filename, str) or filename.startswith("owner-avatar-"):
        return current, None
    image_path = _owner_image_path(filename)
    if image_path is None or not image_path.is_file():
        return current, _UNREADABLE_OWNER_IMAGE_MESSAGE
    try:
        normalized = normalize_owner_image(image_path.read_bytes(), None)
        migrated_filename, migrated_path = _write_owner_image(normalized)
        try:
            updated = save_settings({"owner_image_filename": migrated_filename})
        except ValueError:
            migrated_path.unlink(missing_ok=True)
            raise
        image_path.unlink(missing_ok=True)
        return updated, None
    except (OSError, OwnerImageValidationError, ValueError):
        return current, _UNREADABLE_OWNER_IMAGE_MESSAGE


@router.post("/owner-image")
async def upload_owner_image(file: Annotated[UploadFile, File(...)]) -> dict[str, object]:
    data = await _read_owner_image(file)
    try:
        normalized = normalize_owner_image(data, file.content_type)
    except OwnerImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename, image_path = _write_owner_image(normalized)
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
    updated["owner_image_constraints"] = OWNER_IMAGE_CONSTRAINTS
    return updated


@router.get("/owner-image")
async def get_owner_image() -> FileResponse:
    settings, _ = load_settings()
    settings, image_warning = _ensure_normalized_owner_image(settings)
    if image_warning:
        raise HTTPException(status_code=404, detail=image_warning)
    filename = settings.get("owner_image_filename")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Owner image is not configured")
    image_path = _owner_image_path(filename)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Owner image is not available")
    return FileResponse(
        image_path,
        media_type=OWNER_IMAGE_CONTENT_TYPE,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
    updated["owner_image_constraints"] = OWNER_IMAGE_CONSTRAINTS
    return updated
