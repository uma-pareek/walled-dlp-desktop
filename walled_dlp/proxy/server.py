"""Background thread runner for the embedded mitmproxy HTTPS proxy."""

import asyncio
import logging
import threading
from typing import Optional

from ..config import DLPConfig
from .engine import DLPEngine

logger = logging.getLogger(__name__)


class ProxyServer:
    """Runs an embedded mitmproxy DumpMaster in a background thread inside the desktop app."""

    def __init__(self, config: DLPConfig, engine: DLPEngine):
        self.config = config
        self.engine = engine
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._master = None
        self._started = threading.Event()
        self._startup_error: Optional[BaseException] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 10.0) -> None:
        if self.is_running:
            return
        self._started.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run_loop, name="WalledDLP-Proxy", daemon=True
        )
        self._thread.start()
        self._started.wait(timeout=timeout)
        if self._startup_error is not None:
            raise RuntimeError(f"Failed to start local DLP proxy: {self._startup_error}") from self._startup_error

    def stop(self) -> None:
        if self._master is not None and self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._master.shutdown)
            except Exception:
                logger.debug("Proxy shutdown signal ignored", exc_info=True)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._master = None
        self._thread = None

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._startup_error = exc
            self._started.set()
            logger.exception("Proxy server terminated with error")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster
        from .addon import MitmproxyDLPAddon

        opts = options.Options(
            listen_host=self.config.proxy_host,
            listen_port=self.config.proxy_port,
            # Selective TLS interception: only decrypt ChatGPT, Perplexity, and their upload storage.
            # All other domains pass through untouched.
            allow_hosts=list(self.config.intercept_host_patterns),
            http2=True,
        )
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        master.addons.add(MitmproxyDLPAddon(self.engine))
        self._master = master
        self._started.set()
        logger.info(
            "Walled DLP proxy listening on http://%s:%d",
            self.config.proxy_host,
            self.config.proxy_port,
        )
        await master.run()
