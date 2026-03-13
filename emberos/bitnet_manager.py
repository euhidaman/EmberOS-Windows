"""BitNet server lifecycle manager for EmberOS-Windows."""

import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from emberos.config import Config, ROOT_DIR

logger = logging.getLogger("emberos.bitnet_manager")


def _find_free_port(host: str, start_port: int, max_attempts: int = 5, exclude_ports: tuple = ()) -> int:
    """Find a free port starting from start_port, skipping excluded ports."""
    checked = 0
    port = start_port
    while checked < max_attempts + len(exclude_ports):
        if port in exclude_ports:
            port += 1
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            result = sock.connect_ex((host, port))
            if result != 0:
                return port
        finally:
            sock.close()
        port += 1
        checked += 1
    raise RuntimeError(f"No free port found starting from {start_port}")


class BitNetManager:
    """Manages the BitNet llama-server process."""

    def __init__(self, config: Config):
        self.config = config
        self._proc: Optional[subprocess.Popen] = None
        self._server_port: int = config.server_port

    @property
    def server_binary(self) -> Path:
        # Primary: pre-copied binary
        primary = ROOT_DIR / "bitnet" / "llama-server.exe"
        if primary.exists():
            return primary
        # Fallback: build output (Windows multi-config puts binaries in Release/)
        for subpath in [
            "bitnet/src/build/bin/Release/llama-server.exe",
            "bitnet/src/build/bin/llama-server.exe",
        ]:
            p = ROOT_DIR / subpath
            if p.exists():
                return p
        return primary  # Return primary path even if missing (error handled at start)

    @property
    def server_port(self) -> int:
        return self._server_port

    def start_server(self) -> subprocess.Popen:
        """Start the BitNet inference server as a subprocess."""
        # If a server is already answering on the configured port, reuse it
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            _already_up = _sock.connect_ex((self.config.server_host, self.config.server_port)) == 0
        finally:
            _sock.close()
        if _already_up:
            import requests as _req
            try:
                r = _req.get(
                    f"http://{self.config.server_host}:{self.config.server_port}/health",
                    timeout=3,
                )
                if r.status_code == 200:
                    logger.info(
                        "BitNet server already running on port %d — reusing",
                        self.config.server_port,
                    )
                    self._server_port = self.config.server_port
                    return None  # type: ignore[return-value]
            except Exception:
                pass  # Not a live BitNet; fall through to normal startup

        binary = self.server_binary
        if not binary.exists():
            raise FileNotFoundError(f"BitNet server binary not found at {binary}")

        model_path = self.config.abs_model_path
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found at {model_path}")

        # Find a free port, never use the agent API port
        try:
            self._server_port = _find_free_port(
                self.config.server_host,
                self.config.server_port,
                exclude_ports=(self.config.agent_api_port,),
            )
        except RuntimeError:
            logger.error("No free port available for BitNet server")
            raise

        log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "bitnet_server.log"

        cmd = [
            str(binary),
            "--model", str(model_path),
            "--host", self.config.server_host,
            "--port", str(self._server_port),
            "--ctx-size", str(self.config.context_size),
            "--threads", str(self.config.threads),
            "--n-gpu-layers", str(self.config.gpu_layers),
        ]

        logger.info("Starting BitNet server: %s", " ".join(cmd))
        logger.info("GPU mode: %s, port: %d", self.config.gpu_mode, self._server_port)

        with open(log_file, "a", encoding="utf-8") as lf:
            self._proc = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        logger.info("BitNet server started with PID %d", self._proc.pid)
        return self._proc

    def wait_for_server(self, timeout: int = 60) -> bool:
        """Poll the server health endpoint until ready or timeout."""
        import requests

        url = f"http://{self.config.server_host}:{self._server_port}/health"
        deadline = time.time() + timeout
        delay = 2.0

        while time.time() < deadline:
            # Check if process died
            if self._proc and self._proc.poll() is not None:
                logger.error("BitNet server process died with code %d", self._proc.returncode)
                return False

            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    logger.info("BitNet server is ready on port %d", self._server_port)
                    return True
            except requests.ConnectionError:
                pass
            except Exception as e:
                logger.warning("Health check error: %s", e)

            time.sleep(delay)
            delay = min(delay * 1.5, 10.0)

        logger.error("BitNet server did not become ready within %ds", timeout)
        return False

    def stop_server(self) -> None:
        """Gracefully stop the BitNet server."""
        if self._proc is None:
            return

        logger.info("Stopping BitNet server (PID %d)...", self._proc.pid)
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Server did not stop gracefully, killing...")
                self._proc.kill()
                self._proc.wait(timeout=5)
        except Exception as e:
            logger.error("Error stopping server: %s", e)
        finally:
            self._proc = None

    def get_server_status(self) -> str:
        """Return current server status: running, stopped, or error."""
        if self._proc is None:
            return "stopped"
        ret = self._proc.poll()
        if ret is None:
            return "running"
        elif ret == 0:
            return "stopped"
        else:
            return "error"

    def restart_server(self) -> bool:
        """Restart the server."""
        self.stop_server()
        time.sleep(1)
        self.start_server()
        return self.wait_for_server()
