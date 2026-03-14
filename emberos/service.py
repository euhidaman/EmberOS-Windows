"""Windows Service wrapper for EmberOS-Windows using pywin32."""

import json
import logging
import logging.handlers
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Resolve root before anything else
ROOT_DIR = Path(__file__).resolve().parent.parent

# Setup logging early
def _setup_logging():
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "emberos.log"

    handler = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    # Also log to stdout when not running as service
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(console)

_setup_logging()
logger = logging.getLogger("emberos.service")


class AgentAPIHandler(BaseHTTPRequestHandler):
    """Minimal HTTP API handler for the agent service."""

    agent = None  # Set by the service before starting

    def log_message(self, format, *args):
        logger.debug("API: %s", format % args)

    def do_POST(self):
        if self.path == "/query":
            self._handle_query()
        elif self.path == "/restart":
            self._handle_restart()
        elif self.path == "/status":
            self._handle_status()
        elif self.path == "/control":
            self._handle_control()
        else:
            self._send_json(404, {"error": "Not found"})

    def do_GET(self):
        if self.path == "/status":
            self._handle_status()
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not found"})

    def _handle_query(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            user_input = data.get("input", "")
            attached_files = data.get("attached_files", [])

            if not user_input and not attached_files:
                self._send_json(400, {"error": "Missing 'input' field"})
                return

            if self.agent:
                if attached_files:
                    from use_cases.file_analysis import analyze_attached_files
                    file_content = analyze_attached_files(
                        attached_files,
                        user_input or "Analyze the attached files",
                        llm_client=None,  # raw content; agent LLM handles the analysis
                    )
                    if len(file_content) > 4000:
                        file_content = file_content[:4000] + "\n[... truncated]"
                    combined = (
                        f"{user_input}\n\n[Attached file(s)]\n{file_content}"
                        if user_input else
                        f"[Attached file(s)]\n{file_content}"
                    )
                    response = self.agent.run_once(combined)
                else:
                    response = self.agent.run_once(user_input)
                self._send_json(200, {"response": response})
            else:
                self._send_json(503, {"error": "Agent not initialized"})
        except Exception as e:
            logger.exception("Query handler error")
            self._send_json(500, {"error": str(e)})

    def _handle_restart(self):
        try:
            if self.agent:
                self.agent.bitnet.restart_server()
                self._send_json(200, {"status": "restarted"})
            else:
                self._send_json(503, {"error": "Agent not initialized"})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_status(self):
        try:
            status = {
                "service": "running",
                "runtime_server": self.agent.bitnet.get_server_status() if self.agent else "unknown",
                "gpu_mode": self.agent.config.gpu_mode if self.agent else "unknown",
                "model": "local",
                "memory_entries": self.agent.conv_store.get_session_count(self.agent.session_id) if self.agent else 0,
                "server_port": self.agent.bitnet.server_port if self.agent else 0,
                "has_snapshots": self.agent.snapshot_mgr.has_snapshots() if self.agent else False,
            }
            self._send_json(200, status)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_control(self):
        """Handle control actions: interrupt, rollback."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            action = data.get("action", "")

            if not self.agent:
                self._send_json(503, {"error": "Agent not initialized"})
                return

            if action == "interrupt":
                self.agent.interrupt_flag = True
                self._send_json(200, {"status": "interrupt signal sent"})
            elif action == "rollback":
                result = self.agent.snapshot_mgr.rollback_last()
                self._send_json(200, {"status": "rolled back", "detail": result})
            else:
                self._send_json(400, {"error": f"Unknown action: {action}"})
        except Exception as e:
            logger.exception("Control handler error")
            self._send_json(500, {"error": str(e)})

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _find_free_port(host: str, start_port: int) -> int:
    import socket
    for i in range(5):
        port = start_port + i
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sock.connect_ex((host, port)) != 0:
                return port
        finally:
            sock.close()
    return start_port


def _evict_port(host: str, port: int) -> None:
    """Kill any process already bound to host:port so we always own our port."""
    import socket, time
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        occupied = sock.connect_ex((host, port)) == 0
    finally:
        sock.close()

    if not occupied:
        return

    killed = False
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    logger.info("Evicting stale service PID %d on port %d", conn.pid, port)
                    proc.terminate()
                    proc.wait(timeout=3)
                    killed = True
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    try:
                        proc.kill()
                        killed = True
                    except Exception:
                        pass
                except psutil.AccessDenied:
                    # Fall through to taskkill below
                    pass
    except ImportError:
        pass

    if not killed:
        # Fallback: netstat + taskkill (always available on Windows)
        import subprocess
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pid = line.split()[-1]
                    if pid.isdigit():
                        subprocess.run(
                            ["taskkill", "/PID", pid, "/F"],
                            capture_output=True, timeout=5,
                        )
                        logger.info("Killed stale service PID %s on port %d via taskkill", pid, port)
                    break
        except Exception as e:
            logger.warning("Could not evict process on port %d: %s", port, e)

    time.sleep(0.5)  # give the OS a moment to release the port


def run_standalone(api_port: int = 0):
    """Run the agent + API server directly (not as a Windows Service)."""
    from emberos.agent import EmberAgent
    from emberos.config import load_config

    config = load_config()
    if api_port:
        config.agent_api_port = api_port

    agent = EmberAgent(config)
    agent.start()

    _evict_port(config.server_host, config.agent_api_port)
    actual_port = _find_free_port(config.server_host, config.agent_api_port)
    AgentAPIHandler.agent = agent

    httpd = HTTPServer((config.server_host, actual_port), AgentAPIHandler)
    logger.info("Agent API server listening on %s:%d", config.server_host, actual_port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        agent.stop()


# ── Windows Service implementation ──────────────────────────────

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager

    class EmberOSService(win32serviceutil.ServiceFramework):
        _svc_name_ = "EmberOSAgent"
        _svc_display_name_ = "EmberOS AI Agent"
        _svc_description_ = "EmberOS-Windows AI-powered agentic operating system layer"

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self._agent = None
            self._httpd = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            logger.info("Service stop requested")
            if self._httpd:
                self._httpd.shutdown()
            if self._agent:
                self._agent.conv_store.close()
                self._agent.stop()
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            logger.info("Service starting...")

            try:
                from emberos.agent import EmberAgent
                from emberos.config import load_config

                config = load_config()
                self._agent = EmberAgent(config)
                self._agent.start()

                _evict_port(config.server_host, config.agent_api_port)
                actual_port = _find_free_port(config.server_host, config.agent_api_port)
                AgentAPIHandler.agent = self._agent
                self._httpd = HTTPServer((config.server_host, actual_port), AgentAPIHandler)

                logger.info("Agent API on %s:%d", config.server_host, actual_port)

                # Run HTTP server in a thread
                server_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
                server_thread.start()

                # Wait for stop signal
                win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
            except Exception:
                logger.exception("Service fatal error")
            finally:
                if self._httpd:
                    self._httpd.shutdown()
                if self._agent:
                    self._agent.stop()
                logger.info("Service stopped")

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


if __name__ == "__main__":
    if _HAS_WIN32 and len(sys.argv) > 1 and sys.argv[1] in ("install", "remove", "start", "stop", "restart", "update"):
        win32serviceutil.HandleCommandLine(EmberOSService)
    else:
        # Run standalone
        run_standalone()
