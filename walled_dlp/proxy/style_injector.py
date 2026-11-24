"""CSS generator and HTML injector for customizing AI-generated response text color."""

import re
from typing import Dict, Tuple

from ..config import DLPConfig

HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)
BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


class StyleInjector:
    """Injects a same-origin stylesheet to style AI-generated responses on ChatGPT and Perplexity."""

    def __init__(self, config: DLPConfig):
        self.config = config

    def build_stylesheet(self) -> str:
        """Generate CSS rules targeting AI-generated response text on ChatGPT & Perplexity."""
        light_color = self.config.effective_light_color
        dark_color = self.config.effective_dark_color

        return f"""/* Walled DLP — AI-Generated Response Text Color Customization */
:root {{
  --walled-dlp-response-color: {light_color};
}}

@media (prefers-color-scheme: dark) {{
  :root {{
    --walled-dlp-response-color: {dark_color};
  }}
}}

html.dark,
body.dark,
[data-theme="dark"],
[data-color-scheme="dark"] {{
  --walled-dlp-response-color: {dark_color};
}}

/* ChatGPT Web (https://chatgpt.com) — Assistant responses only */
[data-message-author-role="assistant"],
[data-message-author-role="assistant"] *,
[data-testid^="conversation-turn-"] [data-message-author-role="assistant"],
[data-testid^="conversation-turn-"] [data-message-author-role="assistant"] *,
[data-conversation-role="assistant"],
[data-conversation-role="assistant"] *,
[data-markdown-text-style="assistant-message"],
[data-markdown-text-style="assistant-message"] *,
[data-assistant-markdown],
[data-assistant-markdown] *,
[data-assistant-markdown-root],
[data-assistant-markdown-root] *,
[data-message-author-role="assistant"] .markdown,
[data-message-author-role="assistant"] .markdown p,
[data-message-author-role="assistant"] .markdown li,
[data-message-author-role="assistant"] .markdown span,
[data-message-author-role="assistant"] .markdown strong,
[data-message-author-role="assistant"] .markdown em,
[data-message-author-role="assistant"] .markdown h1,
[data-message-author-role="assistant"] .markdown h2,
[data-message-author-role="assistant"] .markdown h3,
[data-message-author-role="assistant"] .markdown h4,
[data-message-author-role="assistant"] .markdown blockquote,
[data-message-author-role="assistant"] .markdown td,
[data-message-author-role="assistant"] .markdown th,
article[data-turn="assistant"] .markdown,
article[data-turn="assistant"] *,
article[data-turn="assistant"] .markdown p,
article[data-turn="assistant"] .markdown li {{
  color: var(--walled-dlp-response-color) !important;
}}

/* Perplexity Web (https://www.perplexity.ai) — AI answer prose containers */
.prose,
.prose *,
.prose p,
.prose li,
.prose span,
.prose strong,
.prose em,
.prose h1,
.prose h2,
.prose h3,
.prose h4,
.prose blockquote,
.prose td,
.prose th,
[data-testid="answer"],
[data-testid="answer"] *,
[data-testid="answer"] p,
[data-testid="answer"] li,
[data-testid="answer"] span,
.markdown-content,
.markdown-content p,
.markdown-content li {{
  color: var(--walled-dlp-response-color) !important;
}}

/* Preserve syntax-highlighted code block legibility while tinting inline code */
[data-message-author-role="assistant"] code:not(pre code),
.prose code:not(pre code) {{
  color: var(--walled-dlp-response-color) !important;
}}
"""

    def build_stylesheet_response(self) -> Tuple[int, Dict[str, str], bytes]:
        """Return HTTP 200 response payload for the virtual same-origin stylesheet."""
        css_bytes = self.build_stylesheet().encode("utf-8")
        headers = {
            "Content-Type": "text/css; charset=utf-8",
            "Cache-Control": "no-store, max-age=0",
            "X-Walled-DLP": "style-injected",
        }
        return 200, headers, css_bytes

    def inject_into_html(self, html_bytes: bytes) -> bytes:
        """Insert the <link rel='stylesheet'> tag into an HTML document if not already present."""
        if not html_bytes:
            return html_bytes
        try:
            html_text = html_bytes.decode("utf-8")
        except UnicodeDecodeError:
            html_text = html_bytes.decode("latin-1", errors="ignore")

        link_href = self.config.style_endpoint_path
        if link_href in html_text:
            return html_bytes

        link_tag = (
            f'<link rel="stylesheet" id="walled-dlp-response-style" '
            f'href="{link_href}" />'
        )

        if HEAD_CLOSE_RE.search(html_text):
            updated = HEAD_CLOSE_RE.sub(f"{link_tag}\n</head>", html_text, count=1)
        elif BODY_CLOSE_RE.search(html_text):
            updated = BODY_CLOSE_RE.sub(f"{link_tag}\n</body>", html_text, count=1)
        else:
            updated = html_text + "\n" + link_tag

        return updated.encode("utf-8")

    @staticmethod
    def relax_csp_for_self_style(csp_header: str) -> str:
        """Allow same-origin styles while preserving the site's existing style sources."""
        if not csp_header:
            return csp_header
        directives = [d.strip() for d in csp_header.split(";") if d.strip()]
        by_name = {
            directive.split(None, 1)[0].lower(): index
            for index, directive in enumerate(directives)
        }

        def add_self(index: int) -> None:
            parts = directives[index].split()
            if "'self'" not in parts[1:]:
                directives[index] = f"{parts[0]} 'self' {' '.join(parts[1:])}".rstrip()

        style_src_index = by_name.get("style-src")
        style_elem_index = by_name.get("style-src-elem")
        default_src_index = by_name.get("default-src")

        # If neither style-src nor default-src exists, styles are unrestricted;
        # adding a new style-src would unnecessarily block inline site styles.
        if style_src_index is None and default_src_index is None:
            if style_elem_index is not None:
                add_self(style_elem_index)
            return "; ".join(directives)

        if style_src_index is not None:
            add_self(style_src_index)
        else:
            # Preserve default-src's existing inline/nonce allowances when making
            # a dedicated style-src directive for the injected same-origin file.
            default_parts = directives[default_src_index].split()
            sources = default_parts[1:]
            if "'self'" not in sources:
                sources.insert(0, "'self'")
            directives.append(f"style-src {' '.join(sources)}")

        # style-src-elem overrides style-src for <link> and <style> elements.
        if style_elem_index is not None:
            add_self(style_elem_index)

        return "; ".join(directives)
