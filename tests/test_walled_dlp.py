"""Comprehensive unit and end-to-end tests covering all 9 Walled DLP acceptance criteria."""

import json
from pathlib import Path
import unittest

from walled_dlp.adapters import HttpRequestSnapshot, HttpResponseSnapshot
from walled_dlp.config import DLPConfig
from walled_dlp.models import ActionOutcome, ContentKind, Platform, UserDecision
from walled_dlp.proxy.engine import DLPEngine
from walled_dlp.ui.dialog import format_dialog_content

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def build_multipart_body(
    fields: dict, filename: str, content_type: str, file_bytes: bytes, boundary: str = "----WalledBoundary7MA4YWxkTrZu0gW"
):
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    header = f"multipart/form-data; boundary={boundary}"
    return header, body


class TestWalledDLPEndToEnd(unittest.TestCase):
    """Verifies prompt/file inspection, Continue/Cancel enforcement, and response color styling."""

    def setUp(self):
        self.config = DLPConfig()
        self.prompted_dialogs = []
        self.events = []
        self.next_decision = UserDecision.CANCEL

        def fake_confirm(target, result):
            self.prompted_dialogs.append((target, result, format_dialog_content(target, result)))
            return self.next_decision

        self.engine = DLPEngine(
            config=self.config,
            confirm_callback=fake_confirm,
            event_callback=self.events.append,
        )

    # ------------------------------------------------------------------
    # 1. Clean prompt without marker -> submitted without interruption
    # ------------------------------------------------------------------
    def test_clean_prompt_chatgpt_and_perplexity_no_dialog(self):
        cg_req = HttpRequestSnapshot(
            method="POST",
            url="https://chatgpt.com/backend-api/f/conversation",
            host="chatgpt.com",
            path="/backend-api/f/conversation",
            headers={"content-type": "application/json"},
            content=json.dumps(
                {"messages": [{"content": {"parts": ["Explain quicksort in Python."]}}]}
            ).encode("utf-8"),
        )
        action = self.engine.process_request(cg_req)
        self.assertTrue(action.allow)
        self.assertEqual(len(self.prompted_dialogs), 0)
        self.assertEqual(self.events[-1].outcome, ActionOutcome.ALLOWED_CLEAN)

        px_req = HttpRequestSnapshot(
            method="POST",
            url="https://www.perplexity.ai/rest/sse/perplexity_ask",
            host="www.perplexity.ai",
            path="/rest/sse/perplexity_ask",
            headers={"content-type": "application/json"},
            content=json.dumps({"query_str": "What is the capital of Japan?"}).encode("utf-8"),
        )
        action2 = self.engine.process_request(px_req)
        self.assertTrue(action2.allow)
        self.assertEqual(len(self.prompted_dialogs), 0)

    # ------------------------------------------------------------------
    # 2. Sensitive prompt -> dialog shown; Cancel blocks, Continue allows
    # ------------------------------------------------------------------
    def test_sensitive_prompt_chatgpt_cancel_then_continue(self):
        payload = json.dumps(
            {
                "messages": [
                    {
                        "content": {
                            "parts": ["Please summarize WALLED_SENSITIVE budget draft."]
                        }
                    }
                ]
            }
        ).encode("utf-8")
        req = HttpRequestSnapshot(
            method="POST",
            url="https://chatgpt.com/backend-api/f/conversation",
            host="chatgpt.com",
            path="/backend-api/f/conversation",
            headers={"content-type": "application/json"},
            content=payload,
        )

        # User clicks Cancel -> blocked
        self.next_decision = UserDecision.CANCEL
        blocked_action = self.engine.process_request(req)
        self.assertFalse(blocked_action.allow)
        self.assertIsNotNone(blocked_action.short_circuit_response)
        self.assertEqual(blocked_action.short_circuit_response[0], 403)
        self.assertEqual(len(self.prompted_dialogs), 1)
        target, result, dlg = self.prompted_dialogs[0]
        self.assertEqual(target.kind, ContentKind.PROMPT)
        self.assertEqual(target.platform, Platform.CHATGPT)
        self.assertEqual(dlg.source_label, "Text Prompt")
        self.assertIn("WALLED_SENSITIVE", dlg.identifier_value)

        # User retries and clicks Continue -> allowed unmodified
        self.next_decision = UserDecision.CONTINUE
        allowed_action = self.engine.process_request(req)
        self.assertTrue(allowed_action.allow)
        self.assertIsNone(allowed_action.short_circuit_response)
        self.assertEqual(self.events[-1].outcome, ActionOutcome.ALLOWED_BY_USER)

    def test_sensitive_prompt_perplexity_cancel_then_continue(self):
        req = HttpRequestSnapshot(
            method="POST",
            url="https://www.perplexity.ai/rest/sse/perplexity_ask",
            host="www.perplexity.ai",
            path="/rest/sse/perplexity_ask",
            headers={"content-type": "application/json"},
            content=json.dumps(
                {"query_str": "Search details about WALLED_SENSITIVE internal project"}
            ).encode("utf-8"),
        )

        self.next_decision = UserDecision.CANCEL
        self.assertFalse(self.engine.process_request(req).allow)
        self.assertEqual(self.prompted_dialogs[-1][0].platform, Platform.PERPLEXITY)

        self.next_decision = UserDecision.CONTINUE
        self.assertTrue(self.engine.process_request(req).allow)

    # ------------------------------------------------------------------
    # 3, 4, 5. File uploads (TXT, PDF, PNG OCR) on ChatGPT & Perplexity
    # ------------------------------------------------------------------
    def _register_chatgpt_upload(self, filename: str, mime: str, upload_url: str):
        init_req = HttpRequestSnapshot(
            method="POST",
            url="https://chatgpt.com/backend-api/files",
            host="chatgpt.com",
            path="/backend-api/files",
            headers={"content-type": "application/json"},
            content=json.dumps({"file_name": filename, "mime_type": mime}).encode("utf-8"),
        )
        init_resp = HttpResponseSnapshot(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps({"file_id": "file-123", "upload_url": upload_url}).encode("utf-8"),
        )
        self.engine.process_response(init_req, init_resp)

    def test_chatgpt_text_pdf_image_file_uploads(self):
        cases = [
            ("clean_notes.txt", "text/plain", False),
            ("sensitive_notes.txt", "text/plain", True),
            ("clean_report.pdf", "application/pdf", False),
            ("sensitive_report.pdf", "application/pdf", True),
            ("clean_screenshot.png", "image/png", False),
            ("sensitive_screenshot.png", "image/png", True),
        ]
        for fname, mime, expected_sensitive in cases:
            with self.subTest(filename=fname):
                self.prompted_dialogs.clear()
                upload_url = f"https://files.oaiusercontent.com/uploads/{fname}?sig=test"
                self._register_chatgpt_upload(fname, mime, upload_url)
                file_bytes = (SAMPLES_DIR / fname).read_bytes()

                put_req = HttpRequestSnapshot(
                    method="PUT",
                    url=upload_url,
                    host="files.oaiusercontent.com",
                    path=f"/uploads/{fname}?sig=test",
                    headers={"content-type": mime},
                    content=file_bytes,
                )

                self.next_decision = UserDecision.CANCEL
                action = self.engine.process_request(put_req)

                if expected_sensitive:
                    self.assertFalse(action.allow, f"Expected {fname} to be blocked on Cancel")
                    self.assertEqual(len(self.prompted_dialogs), 1)
                    target, _res, dlg = self.prompted_dialogs[0]
                    self.assertEqual(target.kind, ContentKind.FILE)
                    self.assertEqual(target.identifier, fname)
                    self.assertEqual(dlg.source_label, "File Upload")
                    self.assertEqual(dlg.identifier_value, fname)

                    # Re-uploading with Continue succeeds
                    self.next_decision = UserDecision.CONTINUE
                    self.assertTrue(self.engine.process_request(put_req).allow)
                else:
                    self.assertTrue(action.allow, f"Expected clean file {fname} to pass")
                    self.assertEqual(len(self.prompted_dialogs), 0)

    def test_perplexity_s3_multipart_uploads_text_pdf_image(self):
        for fname, mime, expected_sensitive in [
            ("sensitive_notes.txt", "text/plain", True),
            ("sensitive_report.pdf", "application/pdf", True),
            ("sensitive_screenshot.png", "image/png", True),
            ("clean_screenshot.png", "image/png", False),
        ]:
            with self.subTest(filename=fname):
                self.prompted_dialogs.clear()
                s3_url = "https://ppl-ai-file-upload.s3.amazonaws.com/"
                s3_key = f"user-uploads/abc/{fname}"

                # Step 1: Perplexity upload init
                init_req = HttpRequestSnapshot(
                    method="POST",
                    url="https://www.perplexity.ai/rest/uploads/batch_create_upload_urls",
                    host="www.perplexity.ai",
                    path="/rest/uploads/batch_create_upload_urls",
                    headers={"content-type": "application/json"},
                    content=json.dumps(
                        {"files": {"u1": {"filename": fname, "content_type": mime}}}
                    ).encode("utf-8"),
                )
                init_resp = HttpResponseSnapshot(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    content=json.dumps(
                        {
                            "results": {
                                "u1": {
                                    "s3_bucket_url": s3_url,
                                    "fields": {"key": s3_key},
                                }
                            }
                        }
                    ).encode("utf-8"),
                )
                self.engine.process_response(init_req, init_resp)

                # Step 2: Multipart POST to S3
                ctype_hdr, body = build_multipart_body(
                    fields={"key": s3_key},
                    filename=fname,
                    content_type=mime,
                    file_bytes=(SAMPLES_DIR / fname).read_bytes(),
                )
                post_req = HttpRequestSnapshot(
                    method="POST",
                    url=s3_url,
                    host="ppl-ai-file-upload.s3.amazonaws.com",
                    path="/",
                    headers={"content-type": ctype_hdr},
                    content=body,
                )

                self.next_decision = UserDecision.CANCEL
                action = self.engine.process_request(post_req)
                if expected_sensitive:
                    self.assertFalse(action.allow)
                    self.assertEqual(len(self.prompted_dialogs), 1)
                    self.assertEqual(self.prompted_dialogs[0][0].platform, Platform.PERPLEXITY)
                    self.assertEqual(self.prompted_dialogs[0][0].identifier, fname)

                    self.next_decision = UserDecision.CONTINUE
                    self.assertTrue(self.engine.process_request(post_req).allow)
                else:
                    self.assertTrue(action.allow)
                    self.assertEqual(len(self.prompted_dialogs), 0)

    # ------------------------------------------------------------------
    # 8 & 9. AI Response Text Color Customization on ChatGPT & Perplexity
    # ------------------------------------------------------------------
    def test_response_color_css_injection_both_platforms(self):
        for host in ("chatgpt.com", "www.perplexity.ai"):
            with self.subTest(host=host):
                # HTML gets the stylesheet link while its CSP remains untouched.
                html_req = HttpRequestSnapshot(
                    method="GET",
                    url=f"https://{host}/",
                    host=host,
                    path="/",
                )
                html_resp = HttpResponseSnapshot(
                    status_code=200,
                    headers={
                        "content-type": "text/html; charset=utf-8",
                        "content-security-policy": "default-src 'self'; style-src https://cdn.example.com",
                    },
                    content=b"<!doctype html><html><head><title>App</title></head><body></body></html>",
                )
                mod = self.engine.process_response(html_req, html_resp)
                self.assertIsNotNone(mod)
                new_html, new_headers = mod
                self.assertIn(b"/__walled_dlp__/response-style.css", new_html)
                self.assertEqual(new_headers, {})

                # Virtual stylesheet endpoint returns valid CSS for both platforms
                css_req = HttpRequestSnapshot(
                    method="GET",
                    url=f"https://{host}/__walled_dlp__/response-style.css",
                    host=host,
                    path="/__walled_dlp__/response-style.css",
                )
                css_action = self.engine.process_request(css_req)
                self.assertFalse(css_action.allow)
                status, headers, css_body = css_action.short_circuit_response
                self.assertEqual(status, 200)
                self.assertIn("text/css", headers["Content-Type"])
                css_text = css_body.decode("utf-8")
                self.assertIn('[data-message-author-role="assistant"]', css_text)
                self.assertIn(".prose", css_text)
                self.assertIn(self.config.effective_light_color, css_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
