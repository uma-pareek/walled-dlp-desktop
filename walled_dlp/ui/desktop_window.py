"""Desktop control window for Walled DLP (status, proxy toggle, color picker, live event log)."""

import logging
import queue
import threading
from typing import Optional

from ..config import DLPConfig
from ..models import ActionOutcome, DLPEvent, UserDecision
from ..proxy.engine import DLPEngine
from ..proxy.server import ProxyServer
from ..proxy.system_proxy import (
    disable_system_proxy,
    enable_system_proxy,
    get_mitmproxy_ca_path,
)
from .dialog import DialogContent, show_tkinter_modal

logger = logging.getLogger(__name__)

COLOR_PRESETS = {
    "Deep Teal (Default)": "",
    "Royal Indigo (#4338CA)": "#4338CA",
    "Emerald Green (#047857)": "#047857",
    "Crimson Red (#B91C1C)": "#B91C1C",
    "Amber Gold (#B45309)": "#B45309",
    "Violet (#6D28D9)": "#6D28D9",
}


class DesktopWindow:
    """Lightweight native Tkinter desktop dashboard for managing Walled DLP."""

    def __init__(
        self,
        config: DLPConfig,
        engine: DLPEngine,
        proxy_server: ProxyServer,
        auto_enable_proxy: bool = False,
    ):
        self.config = config
        self.engine = engine
        self.proxy_server = proxy_server
        self.auto_enable_proxy = auto_enable_proxy
        self._system_proxy_enabled = False

        self._event_queue: "queue.Queue[DLPEvent]" = queue.Queue()
        self._dialog_queue: "queue.Queue[tuple]" = queue.Queue()
        self._root = None

    def enqueue_event(self, event: DLPEvent) -> None:
        self._event_queue.put(event)

    def dispatch_modal_dialog(self, content: DialogContent) -> UserDecision:
        """Called from a background proxy thread to display a modal on the Tkinter main thread."""
        done = threading.Event()
        holder = {"decision": UserDecision.CANCEL}
        self._dialog_queue.put((content, done, holder))
        done.wait()
        return holder["decision"]

    def run(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = tk.Tk()
        self._root = root
        root.title("Walled DLP — GenAI Data Loss Prevention")
        root.geometry("760x500")
        root.minsize(680, 440)

        container = ttk.Frame(root, padding=14)
        container.pack(fill="both", expand=True)

        # Header & status row
        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 10))

        ttk.Label(
            header,
            text="Walled DLP Desktop",
            font=("TkDefaultFont", 14, "bold"),
        ).pack(side="left")

        status_var = tk.StringVar(
            value=f"Proxy active on {self.config.proxy_host}:{self.config.proxy_port}"
        )
        ttk.Label(header, textvariable=status_var).pack(side="right")

        # Controls bar
        controls = ttk.LabelFrame(container, text="Protection & Browser Controls", padding=10)
        controls.pack(fill="x", pady=(0, 10))

        protection_var = tk.BooleanVar(value=True)

        def on_toggle_protection():
            self.engine.enabled = bool(protection_var.get())
            state = "Active" if self.engine.enabled else "Paused"
            status_var.set(
                f"Protection {state} — {self.config.proxy_host}:{self.config.proxy_port}"
            )

        ttk.Checkbutton(
            controls,
            text=f"Inspect Prompts & Uploads for '{self.config.sensitive_marker}'",
            variable=protection_var,
            command=on_toggle_protection,
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))

        proxy_btn_var = tk.StringVar(value="Enable System Proxy")

        def on_toggle_sys_proxy():
            if not self._system_proxy_enabled:
                ok, msg = enable_system_proxy(
                    self.config.proxy_host, self.config.proxy_port
                )
                if ok:
                    self._system_proxy_enabled = True
                    proxy_btn_var.set("Disable System Proxy")
                messagebox.showinfo("System Proxy", msg, parent=root)
            else:
                ok, msg = disable_system_proxy()
                if ok:
                    self._system_proxy_enabled = False
                    proxy_btn_var.set("Enable System Proxy")
                messagebox.showinfo("System Proxy", msg, parent=root)

        ttk.Button(
            controls, textvariable=proxy_btn_var, command=on_toggle_sys_proxy
        ).grid(row=0, column=1, sticky="e", padx=4)

        def on_show_cert():
            ca_path = get_mitmproxy_ca_path()
            exists = "Found" if ca_path.exists() else "Not yet generated (start proxy first)"
            messagebox.showinfo(
                "Local Root CA Certificate",
                f"Path: {ca_path}\nStatus: {exists}\n\n"
                "To inspect HTTPS traffic to ChatGPT and Perplexity without browser warnings, "
                "install and trust this local certificate once in your OS/browser trust store "
                "(or visit http://mitm.it while connected to the proxy).",
                parent=root,
            )

        ttk.Button(controls, text="CA Certificate Info", command=on_show_cert).grid(
            row=0, column=2, sticky="e", padx=4
        )

        # Response color row
        color_row = ttk.Frame(controls)
        color_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(color_row, text="AI Response Text Color:").pack(side="left", padx=(0, 8))

        color_choice = tk.StringVar(value="Deep Teal (Default)")
        color_combo = ttk.Combobox(
            color_row,
            textvariable=color_choice,
            values=list(COLOR_PRESETS.keys()),
            state="readonly",
            width=26,
        )
        color_combo.pack(side="left")

        def on_color_change(_event=None):
            selected = COLOR_PRESETS.get(color_choice.get(), "")
            self.config.custom_response_color = selected

        color_combo.bind("<<ComboboxSelected>>", on_color_change)

        ttk.Label(
            color_row,
            text="Targets: chatgpt.com & www.perplexity.ai",
        ).pack(side="right")

        controls.columnconfigure(0, weight=1)

        # Activity log table
        log_frame = ttk.LabelFrame(container, text="Live Inspection Activity", padding=8)
        log_frame.pack(fill="both", expand=True)

        columns = ("time", "platform", "source", "identifier", "outcome")
        tree = ttk.Treeview(log_frame, columns=columns, show="headings", height=12)
        tree.heading("time", text="Time")
        tree.heading("platform", text="Platform")
        tree.heading("source", text="Source")
        tree.heading("identifier", text="Prompt / File Name")
        tree.heading("outcome", text="Outcome")

        tree.column("time", width=75, anchor="center")
        tree.column("platform", width=90, anchor="center")
        tree.column("source", width=95, anchor="center")
        tree.column("identifier", width=310, anchor="w")
        tree.column("outcome", width=130, anchor="center")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        outcome_labels = {
            ActionOutcome.ALLOWED_CLEAN: "Allowed (Clean)",
            ActionOutcome.ALLOWED_BY_USER: "Continued (User)",
            ActionOutcome.BLOCKED_BY_USER: "Blocked (Cancelled)",
        }

        def poll_queues():
            # Drain activity events
            while True:
                try:
                    ev = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                src = "Text Prompt" if ev.kind.value == "text_prompt" else "File Upload"
                tree.insert(
                    "",
                    0,
                    values=(
                        ev.timestamp,
                        ev.platform.value,
                        src,
                        ev.identifier,
                        outcome_labels.get(ev.outcome, ev.outcome.value),
                    ),
                )

            # Drain pending native confirmation dialogs
            while True:
                try:
                    content, done_event, holder = self._dialog_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    holder["decision"] = show_tkinter_modal(root, content)
                finally:
                    done_event.set()

            root.after(100, poll_queues)

        if self.auto_enable_proxy:
            ok, _ = enable_system_proxy(self.config.proxy_host, self.config.proxy_port)
            if ok:
                self._system_proxy_enabled = True
                proxy_btn_var.set("Disable System Proxy")

        def on_close():
            if self._system_proxy_enabled:
                disable_system_proxy()
                self._system_proxy_enabled = False
            self.proxy_server.stop()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.after(100, poll_queues)
        root.mainloop()
