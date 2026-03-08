"""System context monitor for EmberOS-Windows."""

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger("emberos.context")


@dataclass
class ContextSnapshot:
    active_window: str = ""
    clipboard_text: str = ""
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    disk_percent: float = 0.0
    timestamp: float = 0.0


class SystemContextMonitor:
    """Background thread that periodically captures system context."""

    def __init__(self, interval: float = 2.0):
        self._interval = interval
        self._snapshot = ContextSnapshot()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread = None

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ContextMonitor")
        self._thread.start()
        logger.info("System context monitor started")

    def stop(self) -> None:
        """Stop the monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("System context monitor stopped")

    def _run(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                snap = self._capture()
                with self._lock:
                    self._snapshot = snap
            except Exception as e:
                logger.debug("Context capture error: %s", e)
            time.sleep(self._interval)

    def _capture(self) -> ContextSnapshot:
        """Capture current system context."""
        snap = ContextSnapshot(timestamp=time.time())

        # Active window
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            if win:
                snap.active_window = win.title or ""
        except Exception:
            pass

        # Clipboard
        try:
            import pyperclip
            text = pyperclip.paste()
            snap.clipboard_text = text[:500] if text else ""
        except Exception:
            pass

        # System metrics
        try:
            import psutil
            snap.cpu_percent = psutil.cpu_percent(interval=0)
            snap.ram_percent = psutil.virtual_memory().percent
            snap.disk_percent = psutil.disk_usage("C:\\").percent
        except Exception:
            pass

        return snap

    def get_current(self) -> ContextSnapshot:
        """Return the latest context snapshot (thread-safe)."""
        with self._lock:
            return self._snapshot

    def format_context(self) -> str:
        """Format the current context as a string for LLM injection."""
        snap = self.get_current()
        clip = snap.clipboard_text[:200] if snap.clipboard_text else "(empty)"
        return (
            f"Active window: {snap.active_window or '(none)'} | "
            f"Clipboard: {clip} | "
            f"CPU: {snap.cpu_percent:.0f}% | "
            f"RAM: {snap.ram_percent:.0f}%"
        )
