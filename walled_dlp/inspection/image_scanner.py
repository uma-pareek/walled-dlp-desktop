"""Image OCR and metadata text extractor using Pillow + Tesseract."""

import io
import logging
from pathlib import Path
import shutil
from typing import List

logger = logging.getLogger(__name__)


def _configure_tesseract(pytesseract) -> None:
    """Find a standard Windows Tesseract install even if the parent shell has stale PATH."""
    if shutil.which("tesseract"):
        return
    candidates = (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            logger.info("Using Tesseract OCR at %s", candidate)
            return


def _preprocess_for_ocr(image):
    """Normalize image mode, scale small screenshots up, and boost legibility."""
    from PIL import Image, ImageOps

    # Convert palette / alpha images onto a clean white background
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        rgb = image.convert("RGB")

    # Upscale small images so Tesseract recognizes underscores & uppercase markers accurately
    width, height = rgb.size
    if width < 900 or height < 300:
        scale = max(2, int(1000 / max(width, 1)))
        scale = min(scale, 4)
        rgb = rgb.resize((width * scale, height * scale), Image.Resampling.LANCZOS)

    gray = ImageOps.grayscale(rgb)
    gray = ImageOps.autocontrast(gray)
    return gray


def extract_image_text(image_bytes: bytes) -> str:
    """Extract visible text (via Tesseract OCR) and embedded text metadata from image bytes."""
    if not image_bytes:
        return ""

    from PIL import Image
    import pytesseract

    _configure_tesseract(pytesseract)

    parts: List[str] = []

    with Image.open(io.BytesIO(image_bytes)) as img:
        # 1. Inspect embedded text metadata (e.g., PNG tEXt / iTXt / comments)
        for key, value in (img.info or {}).items():
            if isinstance(value, str) and value.strip():
                parts.append(value)

        # 2. Run OCR on preprocessed grayscale image
        processed = _preprocess_for_ocr(img)
        try:
            text_block = pytesseract.image_to_string(processed, config="--psm 6")
            if text_block.strip():
                parts.append(text_block)
        except Exception as exc:
            logger.warning("Tesseract PSM 6 failed: %s", exc)

        # 3. Fallback sparse-text OCR pass if primary pass returned nothing
        if not "".join(parts).strip():
            try:
                text_sparse = pytesseract.image_to_string(processed, config="--psm 11")
                if text_sparse.strip():
                    parts.append(text_sparse)
            except Exception as exc:
                logger.warning("Tesseract PSM 11 failed: %s", exc)

    return "\n".join(parts)
