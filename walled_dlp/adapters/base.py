"""Base interface and HTTP snapshot models for site adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
from typing import Dict, List, Tuple

from ..models import InspectionTarget, Platform


def marker_text_from_json(body: bytes, marker: str, case_sensitive: bool = True) -> str:
    """Find a DLP marker in nested JSON strings, including changed API shapes."""
    if not body or not marker:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""

    strings: List[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(payload)
    needle = marker if case_sensitive else marker.lower()
    matches = [
        value
        for value in strings
        if needle in (value if case_sensitive else value.lower())
    ]
    return "\n".join(matches)


def marker_text_from_body(body: bytes, marker: str, case_sensitive: bool = True) -> str:
    """Find the marker in non-JSON request bodies, returning decoded text on a match."""
    if not body or not marker:
        return ""
    text = body.decode("utf-8-sig", errors="ignore")
    haystack = text if case_sensitive else text.lower()
    needle = marker if case_sensitive else marker.lower()
    return text if needle in haystack else ""


@dataclass
class HttpRequestSnapshot:
    """Framework-agnostic representation of an outgoing HTTP request."""

    method: str
    url: str
    host: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        target = name.lower()
        for k, v in self.headers.items():
            if k.lower() == target:
                return v
        return default


@dataclass
class HttpResponseSnapshot:
    """Framework-agnostic representation of an incoming HTTP response."""

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        target = name.lower()
        for k, v in self.headers.items():
            if k.lower() == target:
                return v
        return default


class SiteAdapter(ABC):
    """Contract implemented by each supported GenAI platform adapter."""

    platform: Platform

    @abstractmethod
    def owns_host(self, host: str) -> bool:
        """Return True if this adapter handles HTML pages or API calls on `host`."""

    @abstractmethod
    def extract_inspection_targets(
        self, req: HttpRequestSnapshot
    ) -> List[InspectionTarget]:
        """Return any prompt or uploaded file targets present in `req` prior to transmission."""

    @abstractmethod
    def observe_response(
        self, req: HttpRequestSnapshot, resp: HttpResponseSnapshot
    ) -> None:
        """Inspect API responses (e.g., presigned upload URLs) to correlate subsequent uploads."""

    @abstractmethod
    def build_blocked_response(self, reason: str) -> Tuple[int, Dict[str, str], bytes]:
        """Return (status_code, headers, body) sent locally when the user clicks Cancel."""
