"""ChatGPT Web (https://chatgpt.com) DLP site adapter."""

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Tuple
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

DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[a-zA-Z0-9.+/-]+);base64,(?P<b64>.+)$", re.DOTALL
)


class ChatGPTAdapter(SiteAdapter):
    """Intercepts ChatGPT web prompts and file uploads (Azure Blob / oaiusercontent)."""

    platform = Platform.CHATGPT

    CHATGPT_HOSTS = ("chatgpt.com", "chat.openai.com")
    PROMPT_PATH_PREFIXES = (
        "/backend-api/f/conversation",
        "/backend-api/conversation",
        "/backend-anon/f/conversation",
        "/backend-anon/conversation",
    )
    UPLOAD_INIT_PATHS = (
        "/backend-api/files",
        "/backend-anon/files",
    )

    def __init__(
        self, upload_registry: UploadRegistry, sensitive_marker: str = "WALLED_SENSITIVE"
    ):
        self.upload_registry = upload_registry
        self.sensitive_marker = sensitive_marker

    def owns_host(self, host: str) -> bool:
        host_l = (host or "").lower()
        return any(
            host_l == h or host_l.endswith(f".{h}") for h in self.CHATGPT_HOSTS
        )

    def _is_chatgpt_storage_host(self, host: str) -> bool:
        host_l = (host or "").lower()
        return (
            host_l.endswith(".oaiusercontent.com")
            or host_l.endswith(".blob.core.windows.net")
        )

    def _is_prompt_endpoint(self, req: HttpRequestSnapshot) -> bool:
        if req.method.upper() != "POST" or not self.owns_host(req.host):
            return False
        path = req.path.split("?", 1)[0]
        # Exclude sub-actions like /conversation/gen_title or /conversation/init
        if path in self.PROMPT_PATH_PREFIXES:
            return True
        return False

    def _is_upload_init_endpoint(self, req: HttpRequestSnapshot) -> bool:
        if req.method.upper() != "POST" or not self.owns_host(req.host):
            return False
        path = req.path.split("?", 1)[0].rstrip("/")
        return path in self.UPLOAD_INIT_PATHS

    def extract_inspection_targets(
        self, req: HttpRequestSnapshot
    ) -> List[InspectionTarget]:
        targets: List[InspectionTarget] = []

        # 1. Outgoing ChatGPT text prompt (and any inline base64 attachments)
        if self._is_prompt_endpoint(req):
            targets.extend(self._extract_prompt_targets(req))
            if targets:
                return targets

        # Fallback for ChatGPT API changes: inspect JSON POST bodies on the
        # supported host even when the conversation endpoint or nesting changes.
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
                        platform=Platform.CHATGPT,
                        kind=ContentKind.PROMPT,
                        identifier=prompt_text,
                        text_content=prompt_text,
                        url=req.url,
                    )
                ]

        # 2. Direct PUT upload to presigned Azure Blob / oaiusercontent URL
        if req.method.upper() == "PUT" and req.content:
            meta = self.upload_registry.lookup_url(req.url)
            if meta and meta.platform == Platform.CHATGPT:
                targets.append(
                    InspectionTarget(
                        platform=Platform.CHATGPT,
                        kind=ContentKind.FILE,
                        identifier=meta.filename,
                        file_bytes=req.content,
                        content_type=meta.content_type or req.header("content-type"),
                        url=req.url,
                    )
                )
                return targets

            # Fallback if PUT hits oaiusercontent/blob even without prior init metadata
            if self._is_chatgpt_storage_host(req.host):
                inferred_name = self._filename_from_url(req.url)
                targets.append(
                    InspectionTarget(
                        platform=Platform.CHATGPT,
                        kind=ContentKind.FILE,
                        identifier=inferred_name,
                        file_bytes=req.content,
                        content_type=req.header("content-type"),
                        url=req.url,
                    )
                )
                return targets

        # 3. Multipart file upload directly to chatgpt.com/backend-api/files*
        if (
            req.method.upper() == "POST"
            and self.owns_host(req.host)
            and "multipart/form-data" in req.header("content-type").lower()
        ):
            _, files = parse_multipart_form_data(req.header("content-type"), req.content)
            for uploaded in files:
                targets.append(
                    InspectionTarget(
                        platform=Platform.CHATGPT,
                        kind=ContentKind.FILE,
                        identifier=uploaded.filename or "uploaded_file",
                        file_bytes=uploaded.data,
                        content_type=uploaded.content_type,
                        url=req.url,
                    )
                )

        return targets

    def observe_response(
        self, req: HttpRequestSnapshot, resp: HttpResponseSnapshot
    ) -> None:
        """Correlate POST /backend-api/files request filename with returned presigned upload_url."""
        if not self._is_upload_init_endpoint(req) or resp.status_code >= 400:
            return
        try:
            req_json = json.loads(req.content.decode("utf-8")) if req.content else {}
            resp_json = json.loads(resp.content.decode("utf-8")) if resp.content else {}
        except Exception:
            return

        filename = req_json.get("file_name") or req_json.get("filename") or "uploaded_file"
        upload_url = resp_json.get("upload_url")
        if upload_url:
            self.upload_registry.register_url(
                url=upload_url,
                platform=Platform.CHATGPT,
                filename=str(filename),
                content_type=str(req_json.get("mime_type", "")),
            )
            logger.debug("Registered ChatGPT upload URL for %s", filename)

    def build_blocked_response(self, reason: str) -> Tuple[int, Dict[str, str], bytes]:
        payload = json.dumps({"detail": reason}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "https://chatgpt.com",
            "Access-Control-Allow-Credentials": "true",
            "Cache-Control": "no-store",
        }
        return 403, headers, payload

    def _extract_prompt_targets(
        self, req: HttpRequestSnapshot
    ) -> List[InspectionTarget]:
        targets: List[InspectionTarget] = []
        if not req.content:
            return targets
        try:
            payload = json.loads(req.content.decode("utf-8"))
        except Exception:
            return targets

        text_chunks: List[str] = []
        messages = payload.get("messages") or []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                text_chunks.append(content)
            elif isinstance(content, dict):
                parts = content.get("parts") or []
                for part in parts:
                    self._collect_part(part, text_chunks, targets, req.url)
                if isinstance(content.get("text"), str):
                    text_chunks.append(content["text"])

        combined_prompt = "\n".join(c for c in text_chunks if c).strip()
        if combined_prompt:
            targets.insert(
                0,
                InspectionTarget(
                    platform=Platform.CHATGPT,
                    kind=ContentKind.PROMPT,
                    identifier=combined_prompt,
                    text_content=combined_prompt,
                    url=req.url,
                ),
            )
        return targets

    def _collect_part(
        self,
        part: Any,
        text_chunks: List[str],
        targets: List[InspectionTarget],
        url: str,
    ) -> None:
        if isinstance(part, str):
            match = DATA_URI_RE.match(part.strip())
            if match:
                try:
                    raw = base64.b64decode(match.group("b64"))
                    targets.append(
                        InspectionTarget(
                            platform=Platform.CHATGPT,
                            kind=ContentKind.FILE,
                            identifier="inline_attachment",
                            file_bytes=raw,
                            content_type=match.group("mime"),
                            url=url,
                        )
                    )
                except Exception:
                    text_chunks.append(part)
            else:
                text_chunks.append(part)
        elif isinstance(part, dict):
            if isinstance(part.get("text"), str):
                text_chunks.append(part["text"])
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                self._collect_part(image_url["url"], text_chunks, targets, url)

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = unquote(urlparse(url).path)
        base = os.path.basename(path.rstrip("/"))
        return base or "chatgpt_upload"
