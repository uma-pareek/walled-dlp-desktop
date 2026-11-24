"""Thread-safe TTL registry mapping presigned upload URLs/keys to original file metadata."""

from dataclasses import dataclass
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlparse

from ..models import Platform


@dataclass
class UploadMetadata:
    platform: Platform
    filename: str
    content_type: str = ""
    created_at: float = 0.0


class UploadRegistry:
    """Tracks upload URLs issued by ChatGPT/Perplexity so PUT/POST payloads know their filename."""

    def __init__(self, ttl_seconds: float = 900.0):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._by_url: Dict[str, UploadMetadata] = {}
        self._by_key: Dict[str, UploadMetadata] = {}

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        self._by_url = {k: v for k, v in self._by_url.items() if v.created_at >= cutoff}
        self._by_key = {k: v for k, v in self._by_key.items() if v.created_at >= cutoff}

    def register_url(
        self, url: str, platform: Platform, filename: str, content_type: str = ""
    ) -> None:
        if not url:
            return
        now = time.monotonic()
        meta = UploadMetadata(
            platform=platform,
            filename=filename,
            content_type=content_type,
            created_at=now,
        )
        with self._lock:
            self._evict_expired(now)
            self._by_url[self._normalize_url(url)] = meta

    def register_key(
        self, key: str, platform: Platform, filename: str, content_type: str = ""
    ) -> None:
        if not key:
            return
        now = time.monotonic()
        meta = UploadMetadata(
            platform=platform,
            filename=filename,
            content_type=content_type,
            created_at=now,
        )
        with self._lock:
            self._evict_expired(now)
            self._by_key[key] = meta

    def lookup_url(self, url: str) -> Optional[UploadMetadata]:
        if not url:
            return None
        now = time.monotonic()
        norm = self._normalize_url(url)
        with self._lock:
            self._evict_expired(now)
            return self._by_url.get(norm)

    def lookup_key(self, key: str) -> Optional[UploadMetadata]:
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            return self._by_key.get(key)
