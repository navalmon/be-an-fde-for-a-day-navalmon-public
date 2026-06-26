"""Image preparation helpers for Task 2 extraction model calls."""

import base64
import struct
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image
from PIL import ImageOps
from PIL import ImageStat
from PIL import UnidentifiedImageError

ImageDetail = Literal["auto", "low", "high"]
ImageFormat = Literal["png", "jpeg"]
_ORIENTATION_TAG = 274


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Model-ready image payload plus metadata used in prompts."""

    content_base64: str
    media_type: str
    detail: ImageDetail
    width: int | None
    height: int | None
    original_width: int | None
    original_height: int | None
    resized: bool
    auto_oriented: bool
    contrast_enhanced: bool


def prepare_png_for_model(
    image_bytes: bytes,
    *,
    detail: ImageDetail,
    max_dimension: int,
    low_contrast_threshold: float,
    image_format: ImageFormat = "png",
    jpeg_quality: int = 90,
) -> PreparedImage:
    """Resize oversized PNG documents for vision models."""
    original_base64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            original_width, original_height = image.size
            original_orientation = image.getexif().get(_ORIENTATION_TAG)
            transposed = ImageOps.exif_transpose(image)
            auto_oriented = original_orientation not in {None, 1} and transposed.size != image.size
            image = transposed
            resized = max(original_width, original_height) > max_dimension
            if resized:
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            contrast_enhanced = _is_low_contrast(image, low_contrast_threshold)
            if contrast_enhanced:
                prepared = ImageOps.autocontrast(image.convert("L"))
            else:
                prepared = image.convert("RGB") if image.mode not in {"RGB", "L"} else image.copy()
            output = BytesIO()
            media_type = _save_prepared_image(
                prepared,
                output,
                image_format=image_format,
                jpeg_quality=jpeg_quality,
            )
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            width, height = prepared.size
    except (Image.DecompressionBombError, SyntaxError, struct.error, UnidentifiedImageError, OSError, ValueError):
        return PreparedImage(
            content_base64=original_base64,
            media_type="image/png",
            detail=detail,
            width=None,
            height=None,
            original_width=None,
            original_height=None,
            resized=False,
            auto_oriented=False,
            contrast_enhanced=False,
        )

    return PreparedImage(
        content_base64=encoded,
        media_type=media_type,
        detail=detail,
        width=width,
        height=height,
        original_width=original_width,
        original_height=original_height,
        resized=resized,
        auto_oriented=auto_oriented,
        contrast_enhanced=contrast_enhanced,
    )


def _save_prepared_image(
    image: Image.Image,
    output: BytesIO,
    *,
    image_format: ImageFormat,
    jpeg_quality: int,
) -> str:
    if image_format == "jpeg":
        quality = max(1, min(jpeg_quality, 95))
        image.convert("RGB").save(output, format="JPEG", quality=quality, optimize=True)
        return "image/jpeg"
    image.save(output, format="PNG", optimize=True)
    return "image/png"


def _is_low_contrast(image: Image.Image, threshold: float) -> bool:
    if threshold <= 0:
        return False
    grayscale = image.convert("L")
    stat = ImageStat.Stat(grayscale)
    return bool(stat.stddev and stat.stddev[0] < threshold)
