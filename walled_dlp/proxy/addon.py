"""mitmproxy addon bridging live HTTPS traffic to DLPEngine."""

import logging

from mitmproxy import http
from mitmproxy.script import concurrent

from ..adapters import HttpRequestSnapshot, HttpResponseSnapshot
from .engine import DLPEngine

logger = logging.getLogger(__name__)


class MitmproxyDLPAddon:
    """Intercepts HTTP/HTTPS flows in mitmproxy and delegates enforcement to DLPEngine."""

    def __init__(self, engine: DLPEngine):
        self.engine = engine

    @staticmethod
    def _snapshot_request(flow: http.HTTPFlow) -> HttpRequestSnapshot:
        return HttpRequestSnapshot(
            method=flow.request.method,
            url=flow.request.pretty_url,
            host=flow.request.pretty_host,
            path=flow.request.path,
            headers=dict(flow.request.headers.items(multi=False)),
            content=flow.request.get_content(strict=False) or b"",
        )

    @staticmethod
    def _snapshot_response(flow: http.HTTPFlow) -> HttpResponseSnapshot:
        resp = flow.response
        return HttpResponseSnapshot(
            status_code=resp.status_code,
            headers=dict(resp.headers.items(multi=False)),
            content=resp.get_content(strict=False) or b"",
        )

    @concurrent
    def request(self, flow: http.HTTPFlow) -> None:
        """Runs in a worker thread so waiting on native confirmation dialogs never blocks other tabs."""
        try:
            req_snap = self._snapshot_request(flow)
            if req_snap.method.upper() == "POST" and (
                req_snap.host.lower() == "chatgpt.com"
                or req_snap.host.lower().endswith(".chatgpt.com")
                or req_snap.host.lower() == "www.perplexity.ai"
                or req_snap.host.lower().endswith(".perplexity.ai")
            ):
                logger.info(
                    "Observed supported-site POST: host=%s path=%s content_type=%s bytes=%d marker_present=%s",
                    req_snap.host,
                    req_snap.path.split("?", 1)[0],
                    req_snap.header("content-type").split(";", 1)[0],
                    len(req_snap.content),
                    self.engine.config.sensitive_marker.encode("utf-8")
                    in req_snap.content,
                )
            action = self.engine.process_request(req_snap)
            if not action.allow and action.short_circuit_response is not None:
                status_code, headers, body = action.short_circuit_response
                flow.response = http.Response.make(status_code, body, headers)
        except Exception:
            logger.exception("Unhandled error inspecting request %s", flow.request.pretty_url)

    def response(self, flow: http.HTTPFlow) -> None:
        """Correlates upload URLs and injects AI response styling into HTML documents."""
        if flow.response is None:
            return
        try:
            req_snap = self._snapshot_request(flow)
            resp_snap = self._snapshot_response(flow)
            modification = self.engine.process_response(req_snap, resp_snap)
            if modification is not None:
                updated_body, updated_headers = modification
                flow.response.set_content(updated_body)
                for key, value in updated_headers.items():
                    flow.response.headers[key] = value
        except Exception:
            logger.exception("Unhandled error processing response %s", flow.request.pretty_url)
