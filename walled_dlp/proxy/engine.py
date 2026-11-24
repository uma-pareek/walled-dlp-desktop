"""Core framework-agnostic DLP request/response enforcement engine."""

from dataclasses import dataclass
import hashlib
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..adapters import (
    ChatGPTAdapter,
    HttpRequestSnapshot,
    HttpResponseSnapshot,
    PerplexityAdapter,
    SiteAdapter,
    UploadRegistry,
)
from ..config import DLPConfig
from ..inspection import ContentInspector
from ..models import ActionOutcome, DLPEvent, InspectionResult, InspectionTarget, UserDecision
from .style_injector import StyleInjector

logger = logging.getLogger(__name__)

ConfirmCallback = Callable[[InspectionTarget, InspectionResult], UserDecision]
EventCallback = Callable[[DLPEvent], None]


@dataclass
class RequestAction:
    """Decision returned by DLPEngine for an outgoing HTTP request."""

    allow: bool
    short_circuit_response: Optional[Tuple[int, Dict[str, str], bytes]] = None


class DLPEngine:
    """Coordinates site adapters, content inspection, user confirmation, and CSS injection."""

    def __init__(
        self,
        config: DLPConfig,
        confirm_callback: ConfirmCallback,
        event_callback: Optional[EventCallback] = None,
    ):
        self.config = config
        self.confirm_callback = confirm_callback
        self.event_callback = event_callback
        self.enabled: bool = True

        self.upload_registry = UploadRegistry()
        self.inspector = ContentInspector(config)
        self.style_injector = StyleInjector(config)
        self._recent_approvals: Dict[str, Tuple[float, int]] = {}
        self._approval_lock = threading.Lock()
        self.adapters: List[SiteAdapter] = [
            ChatGPTAdapter(self.upload_registry, config.sensitive_marker),
            PerplexityAdapter(self.upload_registry, config.sensitive_marker),
        ]

    def _emit_event(self, event: DLPEvent) -> None:
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception:
                logger.exception("Event callback error")

    def _find_owning_adapter(self, host: str) -> Optional[SiteAdapter]:
        for adapter in self.adapters:
            if adapter.owns_host(host):
                return adapter
        return None

    def _approval_fingerprint(
        self, target: InspectionTarget, result: InspectionResult
    ) -> str:
        """Key short-lived duplicate approvals to the exact sensitive excerpt/file."""
        if target.kind.value == "text_prompt":
            content = result.extracted_excerpt or target.text_content or target.identifier
        else:
            content = target.identifier + "\0"
            if target.file_bytes:
                return hashlib.sha256(
                    f"{target.platform.value}\0{target.kind.value}\0{content}".encode()
                    + hashlib.sha256(target.file_bytes).digest()
                ).hexdigest()
        normalized = " ".join((content or "").split())
        material = f"{target.platform.value}\0{target.kind.value}\0{normalized}"
        return hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()

    def _consume_recent_approval(self, fingerprint: str) -> bool:
        """Allow a small number of short-lived retries after Continue, without another modal."""
        now = time.monotonic()
        with self._approval_lock:
            expired = [key for key, (expiry, _) in self._recent_approvals.items() if expiry <= now]
            for key in expired:
                self._recent_approvals.pop(key, None)
            approval = self._recent_approvals.get(fingerprint)
            if approval is None:
                return False
            expiry, repeats_left = approval
            if repeats_left <= 1:
                self._recent_approvals.pop(fingerprint, None)
            else:
                self._recent_approvals[fingerprint] = (expiry, repeats_left - 1)
            return True

    def _remember_approval(self, fingerprint: str) -> None:
        # A web app may immediately resend the same prompt as a follow-up request.
        # Suppress at most two identical retries, and expire the approval after 15 seconds.
        with self._approval_lock:
            self._recent_approvals[fingerprint] = (time.monotonic() + 15.0, 2)

    def process_request(self, req: HttpRequestSnapshot) -> RequestAction:
        """Inspect an outgoing request BEFORE it is forwarded upstream."""
        # 1. Serve virtual same-origin stylesheet for AI response text color customization
        clean_path = req.path.split("?", 1)[0]
        if (
            req.method.upper() == "GET"
            and clean_path == self.config.style_endpoint_path
            and self._find_owning_adapter(req.host) is not None
        ):
            logger.info("Serving response stylesheet for %s", req.host)
            return RequestAction(
                allow=False,
                short_circuit_response=self.style_injector.build_stylesheet_response(),
            )

        if not self.enabled:
            return RequestAction(allow=True)

        # 2. Extract any prompt or uploaded file targets from matching site adapters
        for adapter in self.adapters:
            targets = adapter.extract_inspection_targets(req)
            if not targets:
                continue

            for target in targets:
                result = self.inspector.inspect(target)
                if not result.is_sensitive:
                    self._emit_event(
                        DLPEvent(
                            platform=target.platform,
                            kind=target.kind,
                            identifier=self._truncate(target.identifier),
                            outcome=ActionOutcome.ALLOWED_CLEAN,
                            detector=result.detector_used,
                        )
                    )
                    continue

                # Sensitive marker detected — block and request native desktop confirmation
                logger.info(
                    "Sensitive marker %s detected in %s on %s (%s)",
                    result.matched_marker,
                    target.kind.value,
                    target.platform.value,
                    self._truncate(target.identifier, 60),
                )
                approval_fingerprint = self._approval_fingerprint(target, result)
                if self._consume_recent_approval(approval_fingerprint):
                    logger.info(
                        "Allowed repeated %s request on %s after a recent Continue decision",
                        target.kind.value,
                        target.platform.value,
                    )
                    self._emit_event(
                        DLPEvent(
                            platform=target.platform,
                            kind=target.kind,
                            identifier=self._truncate(target.identifier),
                            outcome=ActionOutcome.ALLOWED_BY_USER,
                            detector=result.detector_used,
                        )
                    )
                    continue
                decision = self.confirm_callback(target, result)

                if decision == UserDecision.CONTINUE:
                    self._remember_approval(approval_fingerprint)
                    self._emit_event(
                        DLPEvent(
                            platform=target.platform,
                            kind=target.kind,
                            identifier=self._truncate(target.identifier),
                            outcome=ActionOutcome.ALLOWED_BY_USER,
                            detector=result.detector_used,
                        )
                    )
                    continue

                # User selected Cancel — block transmission locally
                self._emit_event(
                    DLPEvent(
                        platform=target.platform,
                        kind=target.kind,
                        identifier=self._truncate(target.identifier),
                        outcome=ActionOutcome.BLOCKED_BY_USER,
                        detector=result.detector_used,
                    )
                )
                reason = (
                    f"Blocked by Walled DLP: user cancelled submission containing "
                    f"{self.config.sensitive_marker}."
                )
                return RequestAction(
                    allow=False,
                    short_circuit_response=adapter.build_blocked_response(reason),
                )

        return RequestAction(allow=True)

    def process_response(
        self, req: HttpRequestSnapshot, resp: HttpResponseSnapshot
    ) -> Optional[Tuple[bytes, Dict[str, str]]]:
        """Observe API responses for upload correlation and inject CSS into HTML pages."""
        # 1. Allow adapters to record presigned upload URLs from initiation responses
        for adapter in self.adapters:
            adapter.observe_response(req, resp)

        if not self.enabled:
            return None

        # 2. Inject AI response color stylesheet into ChatGPT & Perplexity HTML pages
        adapter = self._find_owning_adapter(req.host)
        if adapter is None:
            return None

        ctype = resp.header("content-type").lower()
        if resp.status_code == 200 and "text/html" in ctype and resp.content:
            logger.info("Injecting response stylesheet into %s%s", req.host, req.path.split("?", 1)[0])
            updated_body = self.style_injector.inject_into_html(resp.content)
            # Keep the site's Content Security Policy untouched. Rewriting it can
            # disable its own styles/scripts; the injected stylesheet is same-origin.
            return updated_body, {}

        return None

    @staticmethod
    def _truncate(text: str, max_len: int = 80) -> str:
        clean = " ".join((text or "").split())
        if len(clean) <= max_len:
            return clean
        return clean[: max_len - 3] + "..."
