"""Regression tests for the shared app settings file."""

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.routes import app_settings as app_settings_routes
from app.services.app_settings import (
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    validate_settings,
)


def _image_bytes(
    image_format: str,
    width: int = 64,
    height: int = 64,
    *,
    mode: str = "RGB",
    exif: Image.Exif | None = None,
) -> bytes:
    image = Image.new(
        mode,
        (width, height),
        (20, 40, 60, 120) if mode == "RGBA" else (20, 40, 60),
    )
    encoded = BytesIO()
    options = {"exif": exif} if exif is not None else {}
    image.save(encoded, format=image_format, **options)
    return encoded.getvalue()


def _test_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "config" / f".{name}"


def test_missing_settings_falls_back_to_defaults() -> None:
    values, warning = load_settings(_test_path("missing-settings.json"))
    assert values == DEFAULT_SETTINGS
    assert warning


def test_invalid_json_falls_back_to_defaults() -> None:
    path = _test_path("invalid-settings.json")
    path.write_text("{not-json", encoding="utf-8")
    try:
        values, warning = load_settings(path)
        assert values == DEFAULT_SETTINGS
        assert warning
    finally:
        path.unlink(missing_ok=True)


def test_save_settings_merges_and_writes_atomically() -> None:
    path = _test_path("save-settings.json")
    path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")
    try:
        values = save_settings({"backend_port": 8100, "company_name": "テスト社"}, path)
        assert values["backend_port"] == 8100
        assert values["company_name"] == "テスト社"
        assert json.loads(path.read_text(encoding="utf-8"))["frontend_port"] == 3000
    finally:
        path.unlink(missing_ok=True)


def test_codex_restore_settings_default_and_round_trip() -> None:
    assert DEFAULT_SETTINGS["restore_codex_sessions"] is True
    assert DEFAULT_SETTINGS["restore_window_minutes"] == 30

    path = _test_path("restore-settings.json")
    path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")
    try:
        values = save_settings(
            {"restore_codex_sessions": False, "restore_window_minutes": 90},
            path,
        )
        assert values["restore_codex_sessions"] is False
        assert values["restore_window_minutes"] == 90
        loaded, warning = load_settings(path)
        assert warning is None
        assert loaded == values
    finally:
        path.unlink(missing_ok=True)


def test_owner_and_board_settings_round_trip() -> None:
    path = _test_path("owner-board-settings.json")
    path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")
    try:
        values = save_settings(
            {
                "owner_name": "久保田兄貴",
                "owner_title": "Owner / Creator",
                "owner_message": "今日もAIチームと開発中",
                "board_mode": "daily_goals",
                "daily_goals": ["Codex連携の安定化", "Yomicaの不具合修正"],
                "weekly_goals": ["AI Office Viewer完成"],
                "board_memo": "今日は18時まで開発",
                "custom_board_title": "今月の重点",
                "custom_board_message": "AIツールの完成度を上げる",
                "board_auto_rotate": True,
                "board_rotate_seconds": 10,
            },
            path,
        )
        loaded, warning = load_settings(path)
        assert warning is None
        assert loaded == values
        assert loaded["daily_goals"] == ["Codex連携の安定化", "Yomicaの不具合修正"]
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("owner_name", ""),
        ("owner_name", "x" * 51),
        ("owner_title", "x" * 51),
        ("owner_message", "x" * 201),
        ("daily_goals", [""]),
        ("daily_goals", ["x" * 101]),
        ("daily_goals", ["goal"] * 51),
        ("board_memo", "x" * 501),
        ("custom_board_title", "x" * 51),
        ("custom_board_message", "x" * 501),
        ("board_mode", "auto"),
        ("board_auto_rotate", "true"),
        ("board_rotate_seconds", 4),
    ],
)
def test_validate_rejects_invalid_owner_and_board_settings(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, key: value})


@pytest.fixture
def settings_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide the settings router backed by an isolated JSON file and image directory."""
    settings_path = tmp_path / "app-settings.json"
    image_dir = tmp_path / "owner-image"
    settings_path.write_text(json.dumps(DEFAULT_SETTINGS), encoding="utf-8")

    monkeypatch.setattr(app_settings_routes, "OWNER_IMAGE_DIR", image_dir)
    monkeypatch.setattr(
        app_settings_routes,
        "load_settings",
        lambda: load_settings(settings_path),
    )
    monkeypatch.setattr(
        app_settings_routes,
        "save_settings",
        lambda updates: save_settings(updates, settings_path),
    )
    application = FastAPI()
    application.include_router(app_settings_routes.router, prefix="/api/v1")
    return TestClient(application)


def test_settings_api_updates_owner_and_board(settings_api: TestClient) -> None:
    response = settings_api.put(
        "/api/v1/settings",
        json={
            "owner_name": "<script>alert(1)</script>",
            "owner_title": "Creator",
            "owner_message": "plain text only",
            "board_mode": "custom",
            "daily_goals": ["日本語", "English"],
            "custom_board_title": "今月の重点",
            "custom_board_message": "<img src=x onerror=alert(1)>",
        },
    )
    assert response.status_code == 200
    settings = response.json()
    assert settings["owner_name"] == "<script>alert(1)</script>"
    assert settings["custom_board_message"] == "<img src=x onerror=alert(1)>"


@pytest.mark.parametrize(
    ("filename", "content_type", "image_format", "width", "height", "mode"),
    [
        ("日本語のアバター.png", "image/png", "PNG", 64, 64, "RGBA"),
        ("avatar.jpg", "image/jpeg", "JPEG", 256, 256, "RGB"),
        ("avatar.webp", "image/webp", "WEBP", 512, 512, "RGB"),
        ("landscape.jpg", "image/jpeg", "JPEG", 1920, 1080, "RGB"),
        ("portrait.jpg", "image/jpeg", "JPEG", 1080, 1920, "RGB"),
        ("largest.png", "image/png", "PNG", 4096, 4096, "RGB"),
    ],
    ids=["png-64", "jpeg-256", "webp-512", "landscape", "portrait", "png-4096"],
)
def test_owner_image_upload_serves_and_deletes(
    settings_api: TestClient,
    filename: str,
    content_type: str,
    image_format: str,
    width: int,
    height: int,
    mode: str,
) -> None:
    content = _image_bytes(image_format, width, height, mode=mode)
    response = settings_api.post(
        "/api/v1/settings/owner-image",
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 200
    uploaded = response.json()
    assert uploaded["owner_image_url"] == "/api/v1/settings/owner-image"
    assert uploaded["owner_image_filename"].startswith("owner-avatar-")
    assert uploaded["owner_image_filename"].endswith(".webp")
    assert uploaded["owner_image_constraints"]["target_dimension"] == 512

    fetched = settings_api.get("/api/v1/settings/owner-image")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert fetched.headers["cache-control"] == "no-store, max-age=0"
    with Image.open(BytesIO(fetched.content)) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (512, 512)

    deleted = settings_api.delete("/api/v1/settings/owner-image")
    assert deleted.status_code == 200
    assert deleted.json()["owner_image_filename"] is None
    assert deleted.json()["owner_image_url"] is None
    assert settings_api.get("/api/v1/settings/owner-image").status_code == 404


@pytest.mark.parametrize(
    ("filename", "content_type", "kind", "message"),
    [
        ("tiny.png", "image/png", "png-1", "小さすぎます"),
        ("tiny.png", "image/png", "png-63", "小さすぎます"),
        ("huge.png", "image/png", "png-4097", "大きすぎます"),
        ("broken.jpg", "image/jpeg", "broken-jpeg", "読み込めません"),
        ("text.jpg", "image/jpeg", "text", "読み込めません"),
        ("animated.gif", "image/gif", "gif", "使用できません"),
        ("bitmap.bmp", "image/bmp", "bmp", "使用できません"),
        (
            "vector.svg",
            "image/svg+xml",
            "svg",
            "読み込めません",
        ),
    ],
    ids=["png-1", "png-63", "png-4097", "broken-jpeg", "text", "gif", "bmp", "svg"],
)
def test_owner_image_upload_rejects_invalid_images(
    settings_api: TestClient,
    filename: str,
    content_type: str,
    kind: str,
    message: str,
) -> None:
    contents = {
        "png-1": _image_bytes("PNG", 1, 1),
        "png-63": _image_bytes("PNG", 63, 63),
        "png-4097": _image_bytes("PNG", 4097, 4097),
        "broken-jpeg": b"\xff\xd8\xffnot-a-jpeg",
        "text": b"not an image",
        "gif": _image_bytes("GIF"),
        "bmp": _image_bytes("BMP"),
        "svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    }
    response = settings_api.post(
        "/api/v1/settings/owner-image",
        files={"file": (filename, contents[kind], content_type)},
    )
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_owner_image_upload_rejects_too_large_content(settings_api: TestClient) -> None:
    oversized = settings_api.post(
        "/api/v1/settings/owner-image",
        files={
            "file": (
                "avatar.png",
                b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024),
                "image/png",
            )
        },
    )
    assert oversized.status_code == 413
    assert "5MB以下" in oversized.json()["detail"]


def test_owner_image_upload_corrects_exif_rotation_and_cmyk_jpeg(
    settings_api: TestClient,
) -> None:
    exif = Image.Exif()
    exif[274] = 6
    image = Image.new("CMYK", (1080, 1920), (0, 128, 128, 0))
    encoded = BytesIO()
    image.save(encoded, format="JPEG", exif=exif)

    response = settings_api.post(
        "/api/v1/settings/owner-image",
        files={"file": ("smartphone.jpg", encoded.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 200
    fetched = settings_api.get("/api/v1/settings/owner-image")
    with Image.open(BytesIO(fetched.content)) as normalized:
        assert normalized.size == (512, 512)
        assert normalized.mode == "RGB"


def test_settings_migrates_existing_raw_owner_image(settings_api: TestClient) -> None:
    legacy_filename = "owner-existing.png"
    app_settings_routes.OWNER_IMAGE_DIR.mkdir(parents=True)
    (app_settings_routes.OWNER_IMAGE_DIR / legacy_filename).write_bytes(
        _image_bytes("PNG", 1920, 1080)
    )
    app_settings_routes.save_settings({"owner_image_filename": legacy_filename})

    settings = settings_api.get("/api/v1/settings").json()
    assert settings["owner_image_filename"].startswith("owner-avatar-")
    assert settings["owner_image_url"] == "/api/v1/settings/owner-image"
    assert not (app_settings_routes.OWNER_IMAGE_DIR / legacy_filename).exists()

    fetched = settings_api.get("/api/v1/settings/owner-image")
    with Image.open(BytesIO(fetched.content)) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (512, 512)


def test_settings_hides_unreadable_existing_owner_image(settings_api: TestClient) -> None:
    legacy_filename = "owner-broken.jpg"
    app_settings_routes.OWNER_IMAGE_DIR.mkdir(parents=True)
    (app_settings_routes.OWNER_IMAGE_DIR / legacy_filename).write_bytes(b"not an image")
    app_settings_routes.save_settings({"owner_image_filename": legacy_filename})

    settings = settings_api.get("/api/v1/settings").json()
    assert settings["owner_image_url"] is None
    assert "読み込めません" in settings["owner_image_warning"]
    assert settings_api.get("/api/v1/settings/owner-image").status_code == 404


@pytest.mark.parametrize("backend_port", [80, 65536, True])
def test_validate_rejects_invalid_ports(backend_port: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "backend_port": backend_port})


@pytest.mark.parametrize("enabled", [0, 1, "true", None])
def test_validate_rejects_non_boolean_restore_enabled(enabled: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "restore_codex_sessions": enabled})


@pytest.mark.parametrize("minutes", [0, 1441, True, 30.5, "30"])
def test_validate_rejects_invalid_restore_window(minutes: object) -> None:
    with pytest.raises(ValueError):
        validate_settings({**DEFAULT_SETTINGS, "restore_window_minutes": minutes})
