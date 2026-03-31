"""
Resize and compress uploaded images before storage (Pillow).
PDFs and non-image files are not processed here.
"""
from __future__ import annotations

import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None


def _get_int(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def compress_image_bytes(
    data: bytes,
    *,
    canvas: bool = False,
) -> ContentFile:
    """
    Decode image bytes, resize to max width, save as optimized JPEG.
    On failure, returns original bytes wrapped in ContentFile (best-effort).
    """
    if not data:
        return ContentFile(b"")

    if Image is None:
        return ContentFile(data)

    max_w = _get_int("CANVAS_IMAGE_MAX_WIDTH" if canvas else "IMAGE_UPLOAD_MAX_WIDTH", 1200)
    quality = _get_int("CANVAS_IMAGE_JPEG_QUALITY" if canvas else "IMAGE_UPLOAD_JPEG_QUALITY", 60 if canvas else 78)

    try:
        im = Image.open(BytesIO(data))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "P"):
            background = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            if im.mode == "RGBA":
                background.paste(im, mask=im.split()[-1])
            else:
                background.paste(im)
            im = background
        elif im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        if w > max_w:
            new_h = max(1, int(h * (max_w / w)))
            im = im.resize((max_w, new_h), Image.Resampling.LANCZOS)

        out = BytesIO()
        im.save(
            out,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=2,
        )
        out.seek(0)
        return ContentFile(out.read())
    except Exception:
        logger.warning("Image compression failed; storing original bytes", exc_info=True)
        return ContentFile(data)


def compressed_canvas_filename_base(question_or_situation: str | int, attempt_id: int) -> str:
    return f"q{question_or_situation}_{attempt_id}"
