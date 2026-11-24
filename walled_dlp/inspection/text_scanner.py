"""Plain-text decoder and marker scanner."""

from typing import Optional


def decode_text_bytes(data: bytes) -> str:
    """Decode raw text bytes cleanly across UTF-8, UTF-16, and Latin-1 encodings."""
    if not data:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def find_marker(
    text: str, marker: str, case_sensitive: bool = True
) -> Optional[str]:
    """Return a short surrounding excerpt if the marker is present, else None."""
    if not text or not marker:
        return None
    haystack = text if case_sensitive else text.lower()
    needle = marker if case_sensitive else marker.lower()
    idx = haystack.find(needle)
    if idx == -1:
        return None
    start = max(0, idx - 30)
    end = min(len(text), idx + len(marker) + 30)
    excerpt = " ".join(text[start:end].split())
    return excerpt
