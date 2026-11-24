"""Site-specific adapters for ChatGPT and Perplexity web applications."""

from .base import HttpRequestSnapshot, HttpResponseSnapshot, SiteAdapter
from .chatgpt import ChatGPTAdapter
from .perplexity import PerplexityAdapter
from .upload_registry import UploadRegistry

__all__ = [
    "ChatGPTAdapter",
    "HttpRequestSnapshot",
    "HttpResponseSnapshot",
    "PerplexityAdapter",
    "SiteAdapter",
    "UploadRegistry",
]
