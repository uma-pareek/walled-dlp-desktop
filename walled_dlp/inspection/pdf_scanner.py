"""PDF text (and embedded image OCR fallback) extractor."""

import io
import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


def extract_pdf_text(
    pdf_bytes: bytes,
    ocr_callback: Optional[Callable[[bytes], str]] = None,
) -> str:
    """Extract text from PDF bytes using pypdf, with pdfplumber and embedded-image OCR fallbacks."""
    if not pdf_bytes:
        return ""

    extracted_parts: List[str] = []

    # Primary extractor: pypdf
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                extracted_parts.append(page_text)

            # If a page has embedded raster images (e.g., scanned PDF), run OCR on them
            if ocr_callback and getattr(page, "images", None):
                for image_file in page.images:
                    try:
                        ocr_text = ocr_callback(image_file.data)
                        if ocr_text.strip():
                            extracted_parts.append(ocr_text)
                    except Exception as exc:
                        logger.debug("Embedded PDF image OCR skipped: %s", exc)
    except Exception as exc:
        logger.warning("pypdf extraction failed, attempting pdfplumber fallback: %s", exc)

    # Secondary fallback: pdfplumber (handles some table/stream encodings pypdf misses)
    if not extracted_parts:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        extracted_parts.append(page_text)
        except Exception as exc:
            logger.warning("pdfplumber extraction failed: %s", exc)

    return "\n".join(extracted_parts)
