"""Perplexity Web (https://www.perplexity.ai) DLP site adapter."""

import json
import logging
import os
from typing import Dict, List, Tuple
from urllib.parse import unquote, urlparse

from ..models import ContentKind, InspectionTarget, Platform
from .base import (
    HttpRequestSnapshot,
    HttpResponseSnapshot,
    SiteAdapter,
    marker_text_from_body,
    marker_text_from_json,
)
from .multipart import parse_multipart_form_data
from .upload_registry import UploadRegistry

logger = logging.getLogger(__name__)


class PerplexityAdapter(SiteAdapter):
    """Intercepts Perplexity web prompts and S3 file uploads."""

    platform = Platform.PERPLEXITY

    PERPLEXITY_HOSTS = ("perplexity.ai", "www.perplexity.ai")
    PROMPT_PATH_MARKERS = (
        "/rest/sse/perplexity_ask",
        "/api/ask",
    )
    UPLOAD_INIT_PATH_MARKERS = (
        "/rest/uploads/batch_create_upload_urls",
        "/rest/uploads/create_upload_url",
    )

    def __init__(
        self, upload_registry: UploadRegistry, sensitive_marker: str = "WALLED_SENSITIVE"
    ):
        self.upload_registry = upload_registry
        self.sensitive_marker = sensitive_marker

    def owns_host(self, host: str) -> bool:
        host_l = (host or "").lower()
        return any(
            host_l == h or host_l.endswith(f".{h}") for h in self.PERPLEXITY_HOSTS
        )

    def _is_perplexity_s3_host(self, host: str) -> bool:
        host_l = (host or "").lower()
        return "ppl-ai-file-upload" in host_l and "amazonaws.com" in host_l

    def _is_prompt_endpoint(self, req: HttpRequestSnapshot) -> bool:
        if req.method.upper() != "POST" or not self.owns_host(req.host):
            return False
        path = req.path.split("?", 1)[0]
        return any(path.startswith(prefix) for prefix in self.PROMPT_PATH_MARKERS)

    def _is_upload_init_endpoint(self, req: HttpRequestSnapshot) -> bool:
        if req.method.upper() != "POST" or not self.owns_host(req.host):
            return False
        path = req.path.split("?", 1)[0]
        return any(path.startswith(prefix) for prefix in self.UPLOAD_INIT_PATH_MARKERS)

    def extract_inspection_targets(
        self, req: HttpRequestSnapshot
    ) -> List[InspectionTarget]:
        targets: List[InspectionTarget] = []

        # 1. Outgoing Perplexity prompt
        if self._is_prompt_endpoint(req):
            prompt_text = self._extract_prompt_text(req)
            if prompt_text:
                targets.append(
                    InspectionTarget(
                        platform=Platform.PERPLEXITY,
                        kind=ContentKind.PROMPT,
                        identifier=prompt_text,
                        text_content=prompt_text,
                        url=req.url,
                    )
                )
                return targets

        # Fallback for changed Perplexity JSON APIs when the marker is present.
        if (
            req.method.upper() == "POST"
            and self.owns_host(req.host)
            and "multipart/form-data" not in req.header("content-type").lower()
        ):
            prompt_text = (
                marker_text_from_json(req.content, self.sensitive_marker)
                or marker_text_from_body(req.content, self.sensitive_marker)
            )
            if not prompt_text and self.sensitive_marker in req.url:
                prompt_text = req.url
            if prompt_text:
                return [
                    InspectionTarget(
                        platform=Platform.PERPLEXITY,
                        kind=ContentKind.PROMPT,
                        identifier=prompt_text,
                        text_content=prompt_text,
                        url=req.url,
                    )
                ]

        # 2. Multipart POST file upload to Perplexity S3 bucket (or registered URL)
        ctype = req.header("content-type")
        if (
            req.method.upper() == "POST"
            and "multipart/form-data" in ctype.lower()
            and (
                self._is_perplexity_s3_host(req.host)
                or self.owns_host(req.host)
                or self._is_registered_perplexity_url(req.url)
            )
        ):
            fields, files = parse_multipart_form_data(ctype, req.content)
            s3_key = fields.get("key", "")
            key_meta = self.upload_registry.lookup_key(s3_key) if s3_key else None
            for uploaded in files:
                filename = (
                    (key_meta.filename if key_meta else "")
                    or uploaded.filename
                    or os.path.basename(s3_key)
                    or "perplexity_upload"
                )
                targets.append(
                    InspectionTarget(
                        platform=Platform.PERPLEXITY,
                        kind=ContentKind.FILE,
                        identifier=filename,
                        file_bytes=uploaded.data,
                        content_type=(
                            (key_meta.content_type if key_meta else "")
                            or uploaded.content_type
                        ),
                        url=req.url,
                    )
                )
            return targets

        # 3. Direct PUT upload to presigned S3 URL
        if req.method.upper() == "PUT" and req.content:
            meta = self.upload_registry.lookup_url(req.url)
            if (meta and meta.platform == Platform.PERPLEXITY) or self._is_perplexity_s3_host(req.host):
                filename = (
                    meta.filename if meta else self._filename_from_url(req.url)
                )
                targets.append(
                    InspectionTarget(
                        platform=Platform.PERPLEXITY,
                        kind=ContentKind.FILE,
                        identifier=filename,
                        file_bytes=req.content,
                        content_type=(meta.content_type if meta else "") or ctype,
                        url=req.url,
                    )
                )
                return targets

        return targets

    def observe_response(
        self, req: HttpRequestSnapshot, resp: HttpResponseSnapshot
    ) -> None:
        """Correlate Perplexity upload URL creation responses with S3 bucket URLs & keys."""
        if not self._is_upload_init_endpoint(req) or resp.status_code >= 400:
            return
        try:
            req_json = json.loads(req.content.decode("utf-8")) if req.content else {}
            resp_json = json.loads(resp.content.decode("utf-8")) if resp.content else {}
        except Exception:
            return

        # Batch upload endpoint: { "files": { "<id>": { "filename": ..., "content_type": ... } } }
        req_files = req_json.get("files")
        resp_results = resp_json.get("results")
        if isinstance(req_files, dict) and isinstance(resp_results, dict):
            for file_id, meta in req_files.items():
                if not isinstance(meta, dict):
                    continue
                filename = str(meta.get("filename") or meta.get("file_name") or "perplexity_upload")
                ctype = str(meta.get("content_type") or "")
                res_entry = resp_results.get(file_id)
                if not isinstance(res_entry, dict):
                    continue
                bucket_url = res_entry.get("s3_bucket_url") or res_entry.get("upload_url")
                if bucket_url:
                    self.upload_registry.register_url(
                        url=str(bucket_url),
                        platform=Platform.PERPLEXITY,
                        filename=filename,
                        content_type=ctype,
                    )
                fields = res_entry.get("fields")
                if isinstance(fields, dict) and fields.get("key"):
                    self.upload_registry.register_key(
                        key=str(fields["key"]),
                        platform=Platform.PERPLEXITY,
                        filename=filename,
                        content_type=ctype,
                    )
            return

        # Single upload endpoint fallback
        filename = str(req_json.get("filename") or req_json.get("file_name") or "perplexity_upload")
        ctype = str(req_json.get("content_type") or "")
        bucket_url = resp_json.get("s3_bucket_url") or resp_json.get("upload_url")
        if bucket_url:
            self.upload_registry.register_url(
                url=str(bucket_url),
                platform=Platform.PERPLEXITY,
                filename=filename,
                content_type=ctype,
            )
        fields = resp_json.get("fields")
        if isinstance(fields, dict) and fields.get("key"):
            self.upload_registry.register_key(
                key=str(fields["key"]),
                platform=Platform.PERPLEXITY,
                filename=filename,
                content_type=ctype,
            )

    def build_blocked_response(self, reason: str) -> Tuple[int, Dict[str, str], bytes]:
        payload = json.dumps({"error": reason}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "https://www.perplexity.ai",
            "Access-Control-Allow-Credentials": "true",
            "Cache-Control": "no-store",
        }
        return 403, headers, payload

    def _is_registered_perplexity_url(self, url: str) -> bool:
        meta = self.upload_registry.lookup_url(url)
        return bool(meta and meta.platform == Platform.PERPLEXITY)

    @staticmethod
    def _extract_prompt_text(req: HttpRequestSnapshot) -> str:
        if not req.content:
            return ""
        try:
            payload = json.loads(req.content.decode("utf-8"))
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""

        candidates = [
            payload.get("query_str"),
            payload.get("query"),
            (payload.get("params") or {}).get("query_str")
            if isinstance(payload.get("params"), dict)
            else None,
            (payload.get("params") or {}).get("query")
            if isinstance(payload.get("params"), dict)
            else None,
        ]
        for cand in candidates:
            if isinstance(cand, str) and cand.strip():
                return cand.strip()
        return ""

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = unquote(urlparse(url).path)
        base = os.path.basename(path.rstrip("/"))
        return base or "perplexity_upload"
