"""System tray icon and mini-GUI for EmberOS-Windows."""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("emberos.tray")
ROOT_DIR = Path(__file__).resolve().parent.parent
API_BASE = "http://127.0.0.1:8766"


def _load_api_base() -> str:
    """Load the agent API base URL from config."""
    try:
        cfg_path = ROOT_DIR / "config" / "default.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            port = data.get("agent_api_port", 8766)
            host = data.get("server_host", "127.0.0.1")
            return f"http://{host}:{port}"
    except Exception:
        pass
    return API_BASE


def _query_agent(text: str) -> str:
    """Send a query to the agent service via HTTP."""
    import requests
    base = _load_api_base()
    try:
        resp = requests.post(
            f"{base}/query",
            json={"input": text},
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "(no response)")
        return f"[Error {resp.status_code}]: {resp.text}"
    except requests.ConnectionError:
        return "[Error] Cannot connect to EmberOS service. Is it running?"
    except Exception as e:
        return f"[Error] {e}"


def _get_status() -> dict:
    """Get service status."""
    import requests
    base = _load_api_base()
    try:
        resp = requests.get(f"{base}/status", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"service": "unreachable"}


def _restart_agent() -> str:
    """Ask the service to restart."""
    import requests
    base = _load_api_base()
    try:
        resp = requests.post(f"{base}/restart", timeout=30)
        if resp.status_code == 200:
            return "Agent restarted successfully"
        return f"Restart failed: {resp.text}"
    except Exception as e:
        return f"Restart failed: {e}"


def _open_chat_dialog():
    """Launch the full EmberOS GUI window as a subprocess."""
    import subprocess
    import sys
    venv_py = ROOT_DIR / "env" / "venv" / "Scripts" / "python.exe"
    py = str(venv_py) if venv_py.exists() else sys.executable
    try:
        subprocess.Popen(
            [py, "-m", "emberos.gui"],
            cwd=str(ROOT_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as e:
        logger.error("Failed to launch GUI: %s", e)


def _show_status_dialog():
    """Show a Tkinter status dialog."""
    import tkinter as tk
    from tkinter import messagebox

    status = _get_status()
    lines = [f"{k}: {v}" for k, v in status.items()]
    msg = "\n".join(lines)
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("EmberOS Status", msg)
    root.destroy()


def _open_logs():
    """Open the log file."""
    log_path = ROOT_DIR / "logs" / "emberos.log"
    if log_path.exists():
        os.startfile(str(log_path))
    else:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("EmberOS", "Log file not found yet.")
        root.destroy()


def _create_icon_image():
    """Create or load the tray icon image."""
    from PIL import Image
    ico_path = ROOT_DIR / "assets" / "icon.ico"
    if ico_path.exists():
        try:
            return Image.open(str(ico_path))
        except Exception:
            pass
    # Generate a simple icon programmatically
    img = Image.new("RGB", (64, 64), color=(200, 80, 30))
    # Draw a simple "E" shape using pixels
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([12, 12, 52, 52], fill=(255, 120, 40))
    draw.rectangle([20, 20, 44, 24], fill=(255, 255, 255))
    draw.rectangle([20, 30, 40, 34], fill=(255, 255, 255))
    draw.rectangle([20, 40, 44, 44], fill=(255, 255, 255))
    draw.rectangle([20, 20, 24, 44], fill=(255, 255, 255))
    return img


def run_tray():
    """Run the system tray icon."""
    import pystray
    from pystray import MenuItem, Icon

    icon_image = _create_icon_image()

    menu = pystray.Menu(
        MenuItem("Open Chat", lambda: threading.Thread(target=_open_chat_dialog, daemon=True).start()),
        MenuItem("Show Status", lambda: threading.Thread(target=_show_status_dialog, daemon=True).start()),
        MenuItem("Open Logs", lambda: _open_logs()),
        MenuItem("Restart Agent", lambda: _restart_agent()),
        pystray.Menu.SEPARATOR,
        MenuItem("Exit", lambda icon, item: icon.stop()),
    )

    icon = Icon("EmberOS", icon_image, "EmberOS Agent", menu)
    logger.info("System tray icon starting")
    icon.run()


if __name__ == "__main__":
    run_tray()
