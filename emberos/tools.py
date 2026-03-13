"""Tool registry and built-in tools for EmberOS-Windows."""

import concurrent.futures
import json
import logging
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from emberos.config import ROOT_DIR

logger = logging.getLogger("emberos.tools")

# Tool logging to file
_tools_log_file = ROOT_DIR / "logs" / "tools.log"


def _log_tool_call(name: str, params: dict, result: dict) -> None:
    """Append tool call to the tools log file."""
    try:
        _tools_log_file.parent.mkdir(parents=True, exist_ok=True)
        import time
        entry = {
            "timestamp": time.time(),
            "tool": name,
            "params": params,
            "success": result.get("success", False),
        }
        with open(_tools_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


@dataclass
class ToolResult:
    success: bool
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "result": self.result, "error": self.error}


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    func: Callable


class ToolRegistry:
    """Central registry of callable tools."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._register_builtins()

    def register(self, name: str, description: str, parameters: dict, func: Callable) -> None:
        """Register a tool."""
        self._tools[name] = ToolDef(name=name, description=description,
                                     parameters=parameters, func=func)

    def get_tool(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """Return tool schemas for LLM consumption."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def execute_tool(self, name: str, params: dict) -> ToolResult:
        """Execute a registered tool by name."""
        tool = self._tools.get(name)
        if not tool:
            result = ToolResult(success=False, error=f"Unknown tool: {name}")
            _log_tool_call(name, params, result.to_dict())
            return result

        try:
            output = tool.func(**params)
            result = ToolResult(success=True, result=output)
        except Exception as e:
            logger.exception("Tool '%s' failed", name)
            result = ToolResult(success=False, error=str(e))

        _log_tool_call(name, params, result.to_dict())
        return result

    def execute_parallel(self, calls: list[dict]) -> list[ToolResult]:
        """Execute multiple tool calls in parallel."""
        max_workers = min(8, os.cpu_count() or 4)
        results = [None] * len(calls)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for i, call in enumerate(calls):
                name = call.get("tool", "")
                params = call.get("params", {})
                future = executor.submit(self.execute_tool, name, params)
                future_map[future] = i

            for future in concurrent.futures.as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = ToolResult(success=False, error=str(e))

        return results

    def _register_builtins(self) -> None:
        """Register all built-in tools."""

        self.register(
            name="run_shell",
            description="Run a shell command and return stdout/stderr",
            parameters={"cmd": {"type": "string", "description": "Shell command to execute"}},
            func=_tool_run_shell,
        )
        self.register(
            name="read_file",
            description="Read a file's content",
            parameters={"path": {"type": "string", "description": "File path to read"}},
            func=_tool_read_file,
        )
        self.register(
            name="write_file",
            description="Write content to a file",
            parameters={
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            func=_tool_write_file,
        )
        self.register(
            name="list_dir",
            description="List directory contents",
            parameters={"path": {"type": "string", "description": "Directory path"}},
            func=_tool_list_dir,
        )
        self.register(
            name="get_clipboard",
            description="Read the current clipboard text",
            parameters={},
            func=_tool_get_clipboard,
        )
        self.register(
            name="set_clipboard",
            description="Write text to the clipboard",
            parameters={"text": {"type": "string", "description": "Text to copy"}},
            func=_tool_set_clipboard,
        )
        self.register(
            name="open_file",
            description="Open a file with its default application",
            parameters={"path": {"type": "string", "description": "File path to open"}},
            func=_tool_open_file,
        )
        self.register(
            name="search_web",
            description="Open a URL in the default browser",
            parameters={"url": {"type": "string", "description": "URL to open"}},
            func=_tool_search_web,
        )
        self.register(
            name="get_active_window",
            description="Get the title of the currently active window",
            parameters={},
            func=_tool_get_active_window,
        )
        self.register(
            name="close_window",
            description="Close a window by its title",
            parameters={"title": {"type": "string", "description": "Window title to close"}},
            func=_tool_close_window,
        )
        self.register(
            name="get_system_info",
            description="Return hardware profile information",
            parameters={},
            func=_tool_get_system_info,
        )
        self.register(
            name="kill_process",
            description="Kill a process by name or PID",
            parameters={
                "target": {"type": "string", "description": "Process name or PID"},
            },
            func=_tool_kill_process,
        )

        # ── File operation tools ──────────────────────────────────
        self.register(
            name="find_files",
            description="Find files by name or type with optional date filter",
            parameters={
                "query": {"type": "string", "description": "File name substring to match"},
                "search_root": {"type": "string", "description": "Directory to search (default: home)"},
                "file_type": {"type": "string", "description": "Type filter: pdf, image, document, code, video, audio, archive, spreadsheet"},
                "modified_within_days": {"type": "integer", "description": "Only files modified within N days"},
            },
            func=_tool_find_files,
        )
        self.register(
            name="organize_folder",
            description="Organize a folder by file type into subfolders (PDFs, Images, Documents, etc.)",
            parameters={
                "folder": {"type": "string", "description": "Folder path to organize"},
                "preview_only": {"type": "boolean", "description": "If true, show preview without moving files"},
            },
            func=_tool_organize_folder,
        )
        self.register(
            name="move_file",
            description="Move a file or directory to a new location",
            parameters={
                "src": {"type": "string", "description": "Source path"},
                "dst": {"type": "string", "description": "Destination path"},
            },
            func=_tool_move_file,
        )
        self.register(
            name="copy_file",
            description="Copy a file or directory to a new location",
            parameters={
                "src": {"type": "string", "description": "Source path"},
                "dst": {"type": "string", "description": "Destination path"},
            },
            func=_tool_copy_file,
        )
        self.register(
            name="rename_file",
            description="Rename a file or directory",
            parameters={
                "path": {"type": "string", "description": "Path to rename"},
                "new_name": {"type": "string", "description": "New name (not full path)"},
            },
            func=_tool_rename_file,
        )
        self.register(
            name="delete_file",
            description="Delete a file or directory (snapshot backup created first)",
            parameters={
                "path": {"type": "string", "description": "Path to delete"},
            },
            func=_tool_delete_file,
        )
        self.register(
            name="get_file_info",
            description="Get file details: size, type, permissions, modification time",
            parameters={
                "path": {"type": "string", "description": "File or folder path"},
            },
            func=_tool_get_file_info,
        )
        self.register(
            name="create_directory",
            description="Create a new directory including parents",
            parameters={
                "path": {"type": "string", "description": "Directory path to create"},
            },
            func=_tool_create_directory,
        )

        # ── System query tools ────────────────────────────────────
        self.register(
            name="disk_usage",
            description="Get disk usage for all drives (size, used, free)",
            parameters={},
            func=_tool_disk_usage,
        )
        self.register(
            name="ram_status",
            description="Get current RAM usage and available memory",
            parameters={},
            func=_tool_ram_status,
        )
        self.register(
            name="running_processes",
            description="List running processes, optionally filtered by name",
            parameters={
                "filter_name": {"type": "string", "description": "Optional process name filter"},
            },
            func=_tool_running_processes,
        )
        self.register(
            name="system_uptime",
            description="Get how long the system has been running",
            parameters={},
            func=_tool_system_uptime,
        )
        self.register(
            name="cpu_info",
            description="Get CPU name, cores, frequency, and current usage",
            parameters={},
            func=_tool_cpu_info,
        )
        self.register(
            name="cpu_temperature",
            description="Get CPU temperature if available",
            parameters={},
            func=_tool_cpu_temperature,
        )

        # ── App launcher ──────────────────────────────────────────
        self.register(
            name="launch_app",
            description="Launch an application by name (calculator, browser, vscode, terminal, etc.)",
            parameters={
                "app_name": {"type": "string", "description": "Application name or alias"},
            },
            func=_tool_launch_app,
        )


# ── Built-in tool implementations ──────────────────────────────────

def _tool_run_shell(cmd: str) -> str:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=60,
        cwd=str(ROOT_DIR),
    )
    output = result.stdout
    if result.stderr:
        output += "\n[STDERR]\n" + result.stderr
    return output.strip()


def _tool_read_file(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return p.read_text(encoding="utf-8", errors="replace")


def _tool_write_file(path: str, content: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {p}"


def _tool_list_dir(path: str) -> list:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return sorted(
        (e.name + "/" if e.is_dir() else e.name) for e in p.iterdir()
    )


def _tool_get_clipboard() -> str:
    import pyperclip
    return pyperclip.paste() or ""


def _tool_set_clipboard(text: str) -> str:
    import pyperclip
    pyperclip.copy(text)
    return "Clipboard updated"


def _tool_open_file(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    os.startfile(str(p))
    return f"Opened {p}"


def _tool_search_web(url: str) -> str:
    webbrowser.open(url)
    return f"Opened {url} in browser"


def _tool_get_active_window() -> str:
    import pygetwindow as gw
    win = gw.getActiveWindow()
    return win.title if win else "(no active window)"


def _tool_close_window(title: str) -> str:
    import pygetwindow as gw
    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return f"No window found with title: {title}"
    windows[0].close()
    return f"Closed window: {title}"


def _tool_get_system_info() -> dict:
    from emberos.gpu_detect import detect_hardware
    profile = detect_hardware()
    return {
        "cpu_arch": profile.cpu_arch,
        "cpu_cores": profile.cpu_cores,
        "cpu_threads": profile.cpu_threads,
        "ram_gb": profile.ram_gb,
        "gpu_available": profile.gpu_available,
        "gpu_name": profile.gpu_name,
        "gpu_vram_mb": profile.gpu_vram_mb,
        "gpu_mode": profile.gpu_mode,
        "cuda_version": profile.cuda_version,
    }


def _tool_kill_process(target: str) -> str:
    import psutil
    killed = []
    # Try as PID first
    try:
        pid = int(target)
        proc = psutil.Process(pid)
        proc.kill()
        return f"Killed process PID {pid}"
    except (ValueError, psutil.NoSuchProcess):
        pass

    # Try by name
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"] and target.lower() in proc.info["name"].lower():
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        return f"Killed {len(killed)} process(es) matching '{target}': PIDs {killed}"
    return f"No process found matching '{target}'"


# ── File operation tool implementations ────────────────────────────

def _tool_find_files(query: str = "", search_root: str = None,
                     file_type: str = None, modified_within_days: int = None) -> list:
    from use_cases.file_ops import find_files
    return find_files(query or "", search_root, file_type, modified_within_days)


def _tool_organize_folder(folder: str, preview_only: bool = True) -> dict:
    from use_cases.file_ops import organize_folder_by_type
    return organize_folder_by_type(folder, preview_only=bool(preview_only))


def _tool_move_file(src: str, dst: str) -> str:
    from use_cases.file_ops import move_file
    return move_file(src, dst)


def _tool_copy_file(src: str, dst: str) -> str:
    from use_cases.file_ops import copy_file
    return copy_file(src, dst)


def _tool_rename_file(path: str, new_name: str) -> str:
    from use_cases.file_ops import rename_file
    return rename_file(path, new_name)


def _tool_delete_file(path: str) -> str:
    from use_cases.file_ops import delete_file
    return delete_file(path)


def _tool_get_file_info(path: str) -> dict:
    from use_cases.file_ops import get_file_info
    return get_file_info(path)


def _tool_create_directory(path: str) -> str:
    from use_cases.file_ops import create_directory
    return create_directory(path)


# ── System query tool implementations ──────────────────────────────

def _tool_disk_usage() -> str:
    from use_cases.system_queries import get_disk_usage
    return get_disk_usage()


def _tool_ram_status() -> str:
    from use_cases.system_queries import get_ram_status
    return get_ram_status()


def _tool_running_processes(filter_name: str = None) -> str:
    from use_cases.system_queries import get_running_processes
    return get_running_processes(filter_name)


def _tool_system_uptime() -> str:
    from use_cases.system_queries import get_system_uptime
    return get_system_uptime()


def _tool_cpu_info() -> str:
    from use_cases.system_queries import get_cpu_info
    return get_cpu_info()


def _tool_cpu_temperature() -> str:
    from use_cases.system_queries import check_cpu_temperature
    return check_cpu_temperature()


# ── App launcher tool implementation ───────────────────────────────

def _tool_launch_app(app_name: str) -> str:
    from use_cases.app_launcher import launch_app
    return launch_app(app_name)
