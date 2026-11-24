"""Application lifecycle orchestration for Walled DLP desktop app."""

import argparse
import atexit
import logging
import signal
import sys
import time
from typing import Optional

from .config import DLPConfig
from .models import DLPEvent
from .proxy.engine import DLPEngine
from .proxy.server import ProxyServer
from .proxy.system_proxy import disable_system_proxy, enable_system_proxy
from .ui.dialog import NativeConfirmDialog

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="walled-dlp",
        description="Standalone desktop DLP solution for ChatGPT and Perplexity Web.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Local proxy bind host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Local proxy listen port (default: 8080)"
    )
    parser.add_argument(
        "--marker",
        default="WALLED_SENSITIVE",
        help="Sensitive marker string to detect (default: WALLED_SENSITIVE)",
    )
    parser.add_argument(
        "--color",
        default="",
        help="Custom hex color for AI-generated responses (e.g. '#4338CA')",
    )
    parser.add_argument(
        "--auto-proxy",
        action="store_true",
        help="Automatically configure OS HTTP/HTTPS proxy on startup and restore on exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the desktop dashboard window (native dialogs still appear on detections)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args(argv)


def run_app(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    config = DLPConfig(
        sensitive_marker=args.marker,
        proxy_host=args.host,
        proxy_port=args.port,
        custom_response_color=args.color,
    )

    use_gui = not args.headless
    window = None

    if use_gui:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            logger.warning("Tkinter is unavailable; falling back to headless mode.")
            use_gui = False

    def log_event(event: DLPEvent) -> None:
        logger.info(
            "[%s] %s | %s | %s -> %s",
            event.timestamp,
            event.platform.value,
            event.kind.value,
            event.identifier,
            event.outcome.value,
        )
        if window is not None:
            window.enqueue_event(event)

    dialog = NativeConfirmDialog()
    engine = DLPEngine(
        config=config,
        confirm_callback=dialog.ask,
        event_callback=log_event,
    )
    proxy_server = ProxyServer(config=config, engine=engine)

    proxy_enabled = False

    def cleanup() -> None:
        nonlocal proxy_enabled
        if proxy_enabled:
            disable_system_proxy()
            proxy_enabled = False
        proxy_server.stop()

    atexit.register(cleanup)

    def handle_signal(_signum, _frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    proxy_server.start()

    if use_gui:
        from .ui.desktop_window import DesktopWindow

        window = DesktopWindow(
            config=config,
            engine=engine,
            proxy_server=proxy_server,
            auto_enable_proxy=args.auto_proxy,
        )
        dialog._tk_dispatcher = window.dispatch_modal_dialog
        window.run()
    else:
        if args.auto_proxy:
            ok, msg = enable_system_proxy(config.proxy_host, config.proxy_port)
            proxy_enabled = ok
            logger.info(msg)
        logger.info(
            "Walled DLP running in headless mode on http://%s:%d. Press Ctrl+C to stop.",
            config.proxy_host,
            config.proxy_port,
        )
        try:
            while proxy_server.is_running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            cleanup()

    return 0
