"""Domain models shared across inspection, site adapters, proxy, and UI."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ContentKind(str, Enum):
    PROMPT = "text_prompt"
    FILE = "file_upload"


class Platform(str, Enum):
    CHATGPT = "ChatGPT"
    PERPLEXITY = "Perplexity"


class UserDecision(str, Enum):
    CONTINUE = "continue"
    CANCEL = "cancel"


class ActionOutcome(str, Enum):
    ALLOWED_CLEAN = "allowed_clean"
    ALLOWED_BY_USER = "allowed_by_user"
    BLOCKED_BY_USER = "blocked_by_user"


@dataclass
class InspectionTarget:
    """Represents a prompt or uploaded file intercepted prior to network transmission."""

    platform: Platform
    kind: ContentKind
    identifier: str  # Prompt preview text or uploaded file name
    text_content: Optional[str] = None
    file_bytes: Optional[bytes] = None
    content_type: str = ""
    url: str = ""


@dataclass
class InspectionResult:
    """Outcome of inspecting an outgoing prompt or uploaded file."""

    is_sensitive: bool
    matched_marker: Optional[str] = None
    detector_used: str = ""
    extracted_excerpt: str = ""
    error: Optional[str] = None


@dataclass
class DLPEvent:
    """Audit record emitted to the desktop UI log for each inspected action."""

    platform: Platform
    kind: ContentKind
    identifier: str
    outcome: ActionOutcome
    detector: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    )
