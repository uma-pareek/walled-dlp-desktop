"""Cross-platform native desktop confirmation dialog (never an HTML browser popup)."""

from dataclasses import dataclass
import logging
import platform
import shutil
import subprocess
import threading
from typing import Callable, Optional

from ..models import ContentKind, InspectionResult, InspectionTarget, UserDecision

logger = logging.getLogger(__name__)


@dataclass
class DialogContent:
    """Structured text rendered in the native desktop confirmation dialog."""

    title: str
    source_label: str
    identifier_label: str
    identifier_value: str
    marker: str
    body_text: str


def format_dialog_content(
    target: InspectionTarget, result: InspectionResult
) -> DialogContent:
    """Build clear, human-readable text for the confirmation dialog."""
    marker = result.matched_marker or "WALLED_SENSITIVE"
    if target.kind == ContentKind.PROMPT:
        source_label = "Text Prompt"
        identifier_label = "Prompt"
        snippet = " ".join((result.extracted_excerpt or target.identifier or "").split())
        if len(snippet) > 260:
            snippet = snippet[:257] + "..."
        identifier_value = snippet
    else:
        source_label = "File Upload"
        identifier_label = "File Name"
        identifier_value = target.identifier or "Unknown file"

    title = f"Walled DLP Alert — {target.platform.value}"
    body_text = (
        f"Sensitive content ({marker}) was detected before submission to {target.platform.value}.\n\n"
        f"Source Type: {source_label}\n"
        f"{identifier_label}: {identifier_value}\n\n"
        f"Choose 'Continue' to submit anyway, or 'Cancel' to block this submission."
    )
    return DialogContent(
        title=title,
        source_label=source_label,
        identifier_label=identifier_label,
        identifier_value=identifier_value,
        marker=marker,
        body_text=body_text,
    )


class NativeConfirmDialog:
    """Displays an OS-native desktop modal dialog with 'Continue' and 'Cancel' actions."""

    def __init__(
        self,
        tk_dispatcher: Optional[Callable[[DialogContent], UserDecision]] = None,
    ):
        # Optional callback into a running Tkinter mainloop to open a modal on the UI thread
        self._tk_dispatcher = tk_dispatcher
        self._lock = threading.Lock()

    def ask(
        self, target: InspectionTarget, result: InspectionResult
    ) -> UserDecision:
        """Display the native desktop dialog and return UserDecision.CONTINUE or CANCEL."""
        content = format_dialog_content(target, result)
        with self._lock:
            system = platform.system()

            # 1. macOS: Native AppleScript alert sheet (always appears above full-screen browsers)
            if system == "Darwin":
                decision = self._ask_macos_osascript(content)
                if decision is not None:
                    return decision

            # 2. If the desktop Tkinter window is active, dispatch a native modal on its UI thread
            if self._tk_dispatcher is not None:
                try:
                    return self._tk_dispatcher(content)
                except Exception:
                    logger.exception("Tkinter dispatcher dialog failed; trying OS fallback.")

            # 3. Linux: Native GTK/Qt dialog via zenity when installed
            if system == "Linux" and shutil.which("zenity"):
                decision = self._ask_linux_zenity(content)
                if decision is not None:
                    return decision

            # 4. Standalone Tkinter topmost modal dialog (Windows / Linux / macOS fallback)
            decision = self._ask_standalone_tkinter(content)
            if decision is not None:
                return decision

            # Fail closed (Cancel) if no desktop UI backend is available
            logger.error("No native desktop dialog backend available; defaulting to Cancel.")
            return UserDecision.CANCEL

    @staticmethod
    def _escape_applescript(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def _ask_macos_osascript(self, content: DialogContent) -> Optional[UserDecision]:
        escaped_body = self._escape_applescript(content.body_text)
        escaped_title = self._escape_applescript(content.title)
        script = (
            f'display dialog "{escaped_body}" '
            f'with title "{escaped_title}" '
            f'with icon caution '
            f'buttons {{"Cancel", "Continue"}} '
            f'default button "Cancel" '
            f'cancel button "Cancel"'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and "button returned:Continue" in proc.stdout:
                return UserDecision.CONTINUE
            return UserDecision.CANCEL
        except Exception as exc:
            logger.warning("macOS osascript dialog failed: %s", exc)
            return None

    def _ask_linux_zenity(self, content: DialogContent) -> Optional[UserDecision]:
        try:
            proc = subprocess.run(
                [
                    "zenity",
                    "--question",
                    f"--title={content.title}",
                    f"--text={content.body_text}",
                    "--ok-label=Continue",
                    "--cancel-label=Cancel",
                    "--icon-name=dialog-warning",
                    "--width=460",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            return UserDecision.CONTINUE if proc.returncode == 0 else UserDecision.CANCEL
        except Exception as exc:
            logger.warning("Linux zenity dialog failed: %s", exc)
            return None

    def _ask_standalone_tkinter(self, content: DialogContent) -> Optional[UserDecision]:
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            return None

        decision = UserDecision.CANCEL
        try:
            root = tk.Tk()
            root.withdraw()
            decision = show_tkinter_modal(root, content)
            root.destroy()
            return decision
        except Exception as exc:
            logger.warning("Standalone Tkinter dialog failed: %s", exc)
            return None


def show_tkinter_modal(parent, content: DialogContent) -> UserDecision:
    """Render an always-on-top native Tkinter confirmation dialog with Continue and Cancel."""
    import tkinter as tk
    from tkinter import ttk

    result_holder = {"decision": UserDecision.CANCEL}

    dialog = tk.Toplevel(parent)
    dialog.title(content.title)
    dialog.resizable(False, False)
    dialog.attributes("-topmost", True)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill="both", expand=True)

    header = ttk.Label(
        frame,
        text=f"Sensitive Content Detected ({content.source_label})",
        font=("TkDefaultFont", 12, "bold"),
    )
    header.pack(anchor="w", pady=(0, 10))

    body = ttk.Label(
        frame,
        text=content.body_text,
        wraplength=440,
        justify="left",
    )
    body.pack(anchor="w", pady=(0, 16))

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill="x", anchor="e")

    def on_cancel(_event=None):
        result_holder["decision"] = UserDecision.CANCEL
        dialog.destroy()

    def on_continue():
        result_holder["decision"] = UserDecision.CONTINUE
        dialog.destroy()

    cancel_btn = ttk.Button(btn_row, text="Cancel", command=on_cancel)
    cancel_btn.pack(side="right", padx=(8, 0))

    continue_btn = ttk.Button(btn_row, text="Continue", command=on_continue)
    continue_btn.pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", on_cancel)
    dialog.bind("<Escape>", on_cancel)

    # Center on screen and bring to front
    dialog.update_idletasks()
    w, h = dialog.winfo_width(), dialog.winfo_height()
    x = (dialog.winfo_screenwidth() - w) // 2
    y = (dialog.winfo_screenheight() - h) // 3
    dialog.geometry(f"+{x}+{y}")
    dialog.lift()
    dialog.focus_force()
    cancel_btn.focus_set()

    parent.wait_window(dialog)
    return result_holder["decision"]
