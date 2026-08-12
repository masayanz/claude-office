"""Validation and display-safe normalization for owner avatar uploads."""

from __future__ import annotations

import warnings
from io import BytesIO
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_OWNER_IMAGE_BYTES: Final = 5 * 1024 * 1024
MIN_OWNER_IMAGE_DIMENSION: Final = 64
MAX_OWNER_IMAGE_DIMENSION: Final = 4096
OWNER_IMAGE_TARGET_SIZE: Final = 512
OWNER_IMAGE_CONTENT_TYPE: Final = "image/webp"
# Pillow's documented ``Resampling.LANCZOS`` value.  Keep this plain int so
# strict pyright does not depend on Pillow's partially typed enum stub.
_LANCZOS_RESAMPLE: Final = 1

_SUPPORTED_FORMATS: Final = frozenset({"PNG", "JPEG", "WEBP"})
_CONTENT_TYPE_BY_FORMAT: Final = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
_CONTENT_TYPE_ALIASES: Final = {"image/jpg": "image/jpeg"}


class OwnerImageValidationError(ValueError):
    """Raised when an uploaded image cannot safely be used by the Viewer."""


def normalize_owner_image(data: bytes, content_type: str | None) -> bytes:
    """Decode, validate, orient, crop, and encode an upload as a 512px WebP."""
    if len(data) > MAX_OWNER_IMAGE_BYTES:
        raise OwnerImageValidationError(
            "画像サイズが大きすぎます。5MB以下のPNG・JPEG・WebP画像を選択してください。"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                actual_format = probe.format
                if actual_format not in _SUPPORTED_FORMATS:
                    raise OwnerImageValidationError(
                        "この画像形式は使用できません。PNG、JPEG、WebPを選択してください。"
                    )
                expected_content_type = _CONTENT_TYPE_BY_FORMAT[actual_format]
                declared_content_type = _CONTENT_TYPE_ALIASES.get(
                    (content_type or "").lower(), (content_type or "").lower()
                )
                if declared_content_type and declared_content_type != expected_content_type:
                    raise OwnerImageValidationError(
                        "画像の形式がファイル内容と一致しません。PNG、JPEG、WebPを選択してください。"
                    )
                width, height = probe.size
                if width < MIN_OWNER_IMAGE_DIMENSION or height < MIN_OWNER_IMAGE_DIMENSION:
                    raise OwnerImageValidationError(
                        "画像が小さすぎます。64×64px以上の画像を使用してください。"
                    )
                if width > MAX_OWNER_IMAGE_DIMENSION or height > MAX_OWNER_IMAGE_DIMENSION:
                    raise OwnerImageValidationError(
                        "画像の解像度が大きすぎます。4096×4096px以下の画像を使用してください。"
                    )
                probe.verify()

            # ``verify`` invalidates the image object, so fully decode it again
            # before producing the single display asset the Viewer consumes.
            with Image.open(BytesIO(data)) as source:
                source.load()
                image = ImageOps.exif_transpose(source)
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                width, height = image.size
                edge = min(width, height)
                left = (width - edge) // 2
                top = (height - edge) // 2
                image = image.crop((left, top, left + edge, top + edge))
                target_size = (OWNER_IMAGE_TARGET_SIZE, OWNER_IMAGE_TARGET_SIZE)
                # Pillow's stub leaves an internal ``box`` union partially unknown.
                image = image.resize(target_size, _LANCZOS_RESAMPLE)  # type: ignore[reportUnknownMemberType]
                normalized = BytesIO()
                # ``method=0`` keeps uploads responsive even for the allowed
                # 4096px input while still producing a browser-decodable WebP.
                image.save(normalized, format="WEBP", quality=92, method=0)
                return normalized.getvalue()
    except OwnerImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise OwnerImageValidationError(
            "画像ファイルを読み込めませんでした。別の画像を選択してください。"
        ) from exc
