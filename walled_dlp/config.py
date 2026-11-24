"""Configuration for Walled DLP desktop application."""

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class DLPConfig:
    """Runtime settings for the local proxy, content inspector, and response styling."""

    # Sensitive marker to detect in prompts and uploaded files
    sensitive_marker: str = "WALLED_SENSITIVE"
    case_sensitive: bool = True

    # Local intercepting proxy bind settings
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8080

    # AI-generated response text color customization (light / dark theme)
    response_text_color_light: str = "#0F766E"  # Deep teal (high contrast on light bg)
    response_text_color_dark: str = "#2DD4BF"   # Bright teal (high contrast on dark bg)
    custom_response_color: str = ""             # Optional single override color (hex)

    # Maximum file size (in bytes) inspected in memory (default 25 MB)
    max_inspect_bytes: int = 25 * 1024 * 1024

    # Hosts whose HTTPS traffic is decrypted by the local proxy.
    # All other domains pass through untouched so normal browsing is unaffected.
    intercept_host_patterns: Tuple[str, ...] = field(
        default_factory=lambda: (
            # mitmproxy matches allow_hosts against a host:port value.
            r"^chatgpt\.com:443$",
            r"^.+\.chatgpt\.com:443$",
            r"^.+\.oaiusercontent\.com:443$",
            r"^.+\.blob\.core\.windows\.net:443$",
            r"^(www\.)?perplexity\.ai:443$",
            r"^.+\.perplexity\.ai:443$",
            r"^ppl-ai-file-upload\.s3\.amazonaws\.com:443$",
            r"^ppl-ai-file-upload\.s3\..+\.amazonaws\.com:443$",
        )
    )

    # Virtual same-origin stylesheet path served directly by the proxy
    style_endpoint_path: str = "/__walled_dlp__/response-style.css"

    @property
    def effective_light_color(self) -> str:
        return self.custom_response_color.strip() or self.response_text_color_light

    @property
    def effective_dark_color(self) -> str:
        return self.custom_response_color.strip() or self.response_text_color_dark
