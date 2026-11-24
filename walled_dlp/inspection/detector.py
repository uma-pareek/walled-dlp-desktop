"""Unified DLP content inspector for prompts, text files, PDFs, and images."""

import logging
import os
from typing import Tuple

from ..config import DLPConfig
from ..models import ContentKind, InspectionResult, InspectionTarget
from .image_scanner import extract_image_text
from .pdf_scanner import extract_pdf_text
from .text_scanner import decode_text_bytes, find_marker

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
IMAGE_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".rb", ".php", ".sh", ".sql", ".html", ".htm", ".css",
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
}


def detect_file_category(filename: str, content_type: str, data: bytes) -> str:
    """Classify uploaded bytes as 'pdf', 'image', 'text', or 'unknown'."""
    ext = os.path.splitext((filename or "").lower())[1]
    ctype = (content_type or "").split(";")[0].strip().lower()

    # 1. Magic-byte detection (most reliable, immune to spoofed extensions)
    if data.startswith(PDF_MAGIC):
        return "pdf"
    for sig, _ in IMAGE_SIGNATURES:
        if data.startswith(sig):
            return "image"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"

    # 2. MIME type / extension detection
    if ctype == "application/pdf" or ext == ".pdf":
        return "pdf"
    if ctype.startswith("image/") or ext in IMAGE_EXTENSIONS:
        return "image"
    if (
        ctype.startswith("text/")
        or ctype in ("application/json", "application/xml", "application/javascript")
        or ext in TEXT_EXTENSIONS
    ):
        return "text"

    # 3. Heuristic: treat as plain text if it contains no NUL bytes and decodes cleanly
    sample = data[:4096]
    if sample and b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            pass

    return "unknown"


class ContentInspector:
    """Inspects outgoing prompts and uploaded files for the configured DLP marker."""

    def __init__(self, config: DLPConfig):
        self.config = config

    def inspect(self, target: InspectionTarget) -> InspectionResult:
        """Inspect an InspectionTarget and return an InspectionResult."""
        if target.kind == ContentKind.PROMPT:
            text = target.text_content or ""
            return self._scan_text(text, detector="text_prompt")

        raw_bytes = target.file_bytes or b""
        if not raw_bytes:
            return InspectionResult(is_sensitive=False, detector_used="empty_file")

        if len(raw_bytes) > self.config.max_inspect_bytes:
            logger.warning(
                "File %s exceeds max_inspect_bytes (%d bytes); inspecting prefix.",
                target.identifier,
                self.config.max_inspect_bytes,
            )
            raw_bytes = raw_bytes[: self.config.max_inspect_bytes]

        category = detect_file_category(
            target.identifier, target.content_type, raw_bytes
        )

        if category == "pdf":
            try:
                text = extract_pdf_text(raw_bytes, ocr_callback=extract_image_text)
                return self._scan_text(text, detector="pdf_extractor")
            except Exception as exc:
                logger.exception("PDF inspection error for %s", target.identifier)
                return InspectionResult(
                    is_sensitive=False, detector_used="pdf_extractor", error=str(exc)
                )

        if category == "image":
            try:
                text = extract_image_text(raw_bytes)
                return self._scan_text(text, detector="image_ocr")
            except Exception as exc:
                logger.exception("Image OCR error for %s", target.identifier)
                return InspectionResult(
                    is_sensitive=False, detector_used="image_ocr", error=str(exc)
                )

        if category == "text":
            text = decode_text_bytes(raw_bytes)
            return self._scan_text(text, detector="text_file")

        # Fallback: check raw UTF-8/Latin-1 bytes for marker even in unknown formats
        fallback_text = decode_text_bytes(raw_bytes)
        return self._scan_text(fallback_text, detector="binary_fallback")

    def _scan_text(self, text: str, detector: str) -> InspectionResult:
        excerpt = find_marker(
            text,
            marker=self.config.sensitive_marker,
            case_sensitive=self.config.case_sensitive,
        )
        if excerpt is not None:
            return InspectionResult(
                is_sensitive=True,
                matched_marker=self.config.sensitive_marker,
                detector_used=detector,
                extracted_excerpt=excerpt,
            )
        return InspectionResult(is_sensitive=False, detector_used=detector)
