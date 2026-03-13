"""Core agent loop for EmberOS-Windows."""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from emberos.bitnet_manager import BitNetManager
from emberos.config import Config, load_config
from emberos.context import SystemContextMonitor
from emberos.llm_client import LLMClient
from emberos.memory import ConversationStore, VectorStore, ContextWindowManager
from emberos.snapshot import SnapshotManager
from emberos.tools import ToolRegistry, ToolResult

logger = logging.getLogger("emberos.agent")

_RECALL_KEYWORDS = ("earlier", "before", "last time", "remember when", "what did we", "previously")

_NOTE_SAVE_KEYWORDS = ("remember that", "save this", "note:", "create a note", "make a note")
_NOTE_QUERY_KEYWORDS = ("what did i note", "what was the", "search my notes", "what did we note")

_CALENDAR_KEYWORDS = ("add to calendar", "remind me at", "schedule a meeting")
_WEB_SEARCH_KEYWORDS = ("search the web", "look up", "google", "find online", "search online")
_BROWSER_KEYWORDS = ("go to ", "open http", "open www", "open the website")
_VISION_STUB_KEYWORDS = ("what is in this image", "describe this image", "analyze this image",
                         "what does the image show", "explain the chart")

_DESTRUCTIVE_SHELL = ("del ", "rm ", "rmdir ", "format ", "remove-item",
                      "rd ", "erase ")

# App launch detection: matched against prefixes "open/launch/start + alias"
_APP_LAUNCH_PREFIXES = ("open ", "launch ", "start ")

# System query keywords
_DISK_QUERY_KEYWORDS = (
    "disk space", "disk usage", "free space", "storage space", "how much disk",
    "how much storage", "mounted drives", "drive space", "drives and their free",
)
_RAM_QUERY_KEYWORDS = (
    "ram usage", "memory usage", "how much ram", "how much memory",
    "available ram", "available memory",
)
_PROCESS_QUERY_KEYWORDS = (
    "running processes", "show processes", "list processes", "what processes",
    "active processes", "show running",
)
_UPTIME_QUERY_KEYWORDS = (
    "system uptime", "how long running", "machine uptime", "system up",
    "how long the system", "how long has",
)
_CPU_QUERY_KEYWORDS = (
    "cpu usage", "cpu temperature", "cpu temp", "cpu info", "cpu load",
    "processor usage", "processor info", "check cpu",
)

# File find/organize keywords
_FILE_FIND_KEYWORDS = (
    "find all pdf", "find pdf", "find all image", "find all python",
    "find python files", "find all files", "search for files", "find files",
    "look for files", "find files named", "search files named",
)
_FILE_ORGANIZE_KEYWORDS = (
    "organize my downloads", "organize the downloads", "organize downloads",
    "organize my documents", "organize my desktop", "organize my pictures",
    "organize folder", "sort files by type", "group files by type",
    "organize files by type",
)

_FILE_SUMMARIZE_KEYWORDS = (
    "what's in", "whats in", "what is in", "what's inside",
    "summarize", "summarise", "summary of",
    "read the file", "read this file", "read that file",
    "describe the file", "what does the file",
    "contents of", "content of",
    "analyze the file", "analyse the file",
    "tell me about the file", "tell me what's in",
)

_FILE_DELETE_KEYWORDS = (
    "delete the file", "delete file", "delete this file",
    "remove the file", "remove file", "remove this file",
    "delete the document", "remove the document",
    "get rid of", "erase the file", "erase file",
)

# ── New capability keyword groups ───────────────────────────────────────────

_SCREENSHOT_KEYWORDS = (
    "take a screenshot", "take screenshot", "capture screenshot",
    "screenshot my", "screenshot the screen", "screen capture",
)
_VOLUME_UP_KEYWORDS = ("volume up", "turn up volume", "increase volume", "louder")
_VOLUME_DOWN_KEYWORDS = ("volume down", "turn down volume", "decrease volume", "quieter", "lower the volume")
_MUTE_KEYWORDS = ("mute", "unmute", "toggle mute", "silence the audio")
_DARK_MODE_KEYWORDS = ("dark mode", "light mode", "enable dark mode", "disable dark mode",
                        "turn on dark mode", "turn off dark mode", "switch to dark", "switch to light",
                        "toggle dark mode", "toggle theme")
_BATTERY_KEYWORDS = ("battery", "battery level", "how much battery", "battery status",
                      "battery percentage", "battery charge")
_LOCK_KEYWORDS = ("lock the screen", "lock screen", "lock computer", "lock my pc",
                   "lock the computer", "lock workstation")
_SLEEP_KEYWORDS = ("sleep the computer", "put to sleep", "put computer to sleep",
                    "sleep mode", "hibernate")
_SHUTDOWN_KEYWORDS = ("shut down", "shutdown the computer", "power off", "turn off the pc",
                       "turn off the computer", "turn off computer")
_RESTART_KEYWORDS = ("restart", "reboot", "restart the computer", "restart my pc",
                      "reboot the computer")
_BRIGHTNESS_KEYWORDS = ("brightness", "screen brightness", "set brightness",
                          "increase brightness", "decrease brightness", "dim the screen")
_WINDOW_LIST_KEYWORDS = ("open windows", "list windows", "show windows", "what windows",
                          "which windows", "what's open", "whats open")
_WINDOW_MIN_KEYWORDS = ("minimize all", "show desktop", "hide all windows",
                         "minimize all windows", "win d", "win+d")
_WINDOW_FOCUS_KEYWORDS = ("focus on", "switch to window", "bring up", "focus window")

_TASK_ADD_KEYWORDS = ("add a task", "add task", "create a task", "create task",
                        "new task", "add to my tasks", "add to tasks", "todo:", "to-do:",
                        "remind me to", "i need to", "task:")
_TASK_LIST_KEYWORDS = ("show my tasks", "list my tasks", "show tasks", "list tasks",
                         "what are my tasks", "pending tasks", "my to-do", "my todo",
                         "task list", "show todo")
_TASK_COMPLETE_KEYWORDS = ("complete task", "mark task", "done task", "finish task",
                             "mark as done", "complete #", "done #", "task done")
_TASK_REMOVE_KEYWORDS = ("remove task", "delete task", "delete to-do", "remove todo")
_TASK_CLEAR_KEYWORDS = ("clear completed tasks", "clear done tasks", "remove completed",
                          "clean up tasks", "clear finished tasks")

_COMPRESS_KEYWORDS = ("compress", "zip the file", "zip the folder", "create a zip",
                        "create zip", "archive the file", "zip files")
_EXTRACT_KEYWORDS = ("extract", "unzip", "extract the archive", "extract from zip",
                      "unzip the file", "unpack")
_FIND_LARGE_KEYWORDS = ("large files", "biggest files", "find large files",
                          "files taking up space", "huge files", "find files larger")
_FIND_OLD_KEYWORDS = ("old files", "oldest files", "find old files",
                        "files not modified", "stale files")
_FIND_DUPES_KEYWORDS = ("duplicate files", "find duplicates", "find duplicate files",
                          "same files", "identical files", "dedup")
_GREP_KEYWORDS = ("search inside", "grep", "find text in", "search text in",
                    "look inside", "find in file", "search for text in")
_DIFF_KEYWORDS = ("diff", "compare file", "compare the files", "what changed between",
                    "difference between files", "show diff")
_EXTRACT_PATTERNS_KEYWORDS = ("extract emails", "find emails", "extract urls", "find urls",
                                "find phone numbers", "extract phone", "extract dates from",
                                "extract ips", "extract patterns")

# ── Known file extensions for query parsing ──────────────────────────────────
_KNOWN_EXTENSIONS = (
    "pdf", "docx", "xlsx", "pptx", "txt", "md", "csv",
    "py", "js", "ts", "json", "log", "html", "xml", "yaml", "yml",
    "cpp", "c", "h", "rs", "go", "java", "rb", "bat", "ps1", "zip",
)


_CONFIRM_YES = {"yes", "y", "proceed", "ok", "sure", "do it", "go ahead", "yep", "yeah"}
_CONFIRM_NO = {"no", "n", "cancel", "stop", "nope", "don't", "abort"}

_IDENTITY_KEYWORDS = (
    "what are you", "who are you", "what can you do", "tell me about yourself",
    "introduce yourself", "what is emberos", "what is ember os", "how do you work",
    "what are your capabilities", "what do you do", "describe yourself",
    "your name", "are you an ai", "are you a bot",
)
_GREETING_KEYWORDS = (
    "hello", "hi!", "hi there", "hey!", "hey there",
    "good morning", "good evening", "good afternoon", "howdy",
)


def _extract_explicit_tags(text: str):
    """Extract user-specified tags from text like 'tag it as work, meeting'.
    Returns (clean_text, tags_list) or (original_text, []) if none found.
    """
    import re
    patterns = [
        r'\s+(?:and\s+)?tag\s+it\s+as\s+([^.!?\n]+)',
        r'\s+(?:and\s+)?store\s+it\s+under\s+(?:the\s+tag[s]?\s+)?([^.!?\n]+)',
        r'\s+(?:and\s+)?label\s+it\s+(?:as\s+)?([^.!?\n]+)',
        r'\s+with\s+tag[s]?\s+([^.!?\n]+)',
        r'\s+tag[s]?:\s+([^.!?\n]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            tag_str = m.group(1).strip().rstrip(".")
            tags = [
                t.strip().strip("\"'#").lower()
                for t in re.split(r'[,\s]+(?:and\s+)?|[,]+', tag_str)
                if t.strip()
            ]
            tags = [t for t in tags if t]
            clean = text[:m.start()].strip()
            return clean, tags
    return text, []


def _looks_like_tool_call(text: str) -> bool:
    """Return True if text appears to be a (possibly malformed) tool call JSON."""
    t = text.strip()
    return (t.startswith("{") or t.startswith("[{")) and '"tool"' in t


def _parse_file_query(text: str):
    """Parse a natural-language file reference into (filename, search_root).

    Examples
    --------
    "what's in energy.pdf"                         -> ("energy.pdf", None)
    "summarize quantum.pdf in the Quant folder in E drive" -> ("quantum.pdf", "E:\\Quant")
    "what's in notes.txt in downloads"             -> ("notes.txt", "<home>/Downloads")
    "read C:\\Users\\TinyLab\\report.docx"         -> ("C:\\Users\\TinyLab\\report.docx", None)
    """
    import os
    from pathlib import Path as _Path

    # 1. Absolute Windows path (e.g. C:\something\file.ext)
    abs_match = re.search(r'[A-Za-z]:\\[\w\\/ \-\.]+', text)
    if abs_match:
        raw = abs_match.group().rstrip()
        p = _Path(raw)
        if p.suffix:
            return raw, None            # full file path
        # It's a directory — see if a filename appears before it
        fname_m = re.search(_ext_pattern(), text[:abs_match.start()])
        if fname_m:
            return fname_m.group().strip(), raw
        return None, raw

    # 2. Extract filename (word.ext)
    ext_pat = _ext_pattern()
    fname_m = re.search(ext_pat, text, re.IGNORECASE)
    if not fname_m:
        return None, None
    filename = fname_m.group().strip()

    # Everything after the filename is the location description
    after = text[fname_m.end():]

    # 3. Drive letter:  "E drive", "E:", "drive E", "on the E drive"
    drive = None
    dm = re.search(
        r'\b([C-Zc-z])\s+drive\b|drive\s+([C-Zc-z])\b|\b([C-Zc-z]):\B',
        text, re.IGNORECASE,
    )
    if dm:
        drive = (dm.group(1) or dm.group(2) or dm.group(3)).upper()

    # 4. Folder name after "in/from/on/at [the/my] <name> [folder/directory]"
    folder = None
    fm = re.search(
        r'(?:in|from|on|at)\s+(?:the\s+)?(?:my\s+)?([\w][\w\-\. ]{1,40}?)'
        r'\s*(?:folder|directory|dir)?\s*(?:in\b|on\b|at\b|$)',
        after, re.IGNORECASE,
    )
    if fm:
        candidate = fm.group(1).strip().rstrip()
        _noise = {'the', 'my', 'a', 'an', 'this', 'that',
                  'e', 'c', 'd', 'f', 'g', 'drive'}
        if candidate.lower() not in _noise and len(candidate) > 1:
            folder = candidate

    # 5. Build search root
    home = os.path.expanduser("~")
    _folder_map = {
        "downloads":  os.path.join(home, "Downloads"),
        "documents":  os.path.join(home, "Documents"),
        "desktop":    os.path.join(home, "Desktop"),
        "pictures":   os.path.join(home, "Pictures"),
        "videos":     os.path.join(home, "Videos"),
        "music":      os.path.join(home, "Music"),
    }

    if drive and folder:
        search_root = f"{drive}:\\{folder}"
    elif drive:
        search_root = f"{drive}:\\"
    elif folder:
        search_root = _folder_map.get(folder.lower(), folder)
    else:
        search_root = None      # caller will search default locations

    return filename, search_root


def _ext_pattern():
    """Return a compiled-ready regex pattern for filenames with known extensions."""
    exts = "|".join(_KNOWN_EXTENSIONS)
    return rf'[\w\-\.]+\.(?:{exts})'


class EmberAgent:
    """Core reasoning and action loop for EmberOS."""

    def __init__(self, config: Optional[Config] = None):
        self._lock = threading.RLock()
        self.config = config or load_config()

        # Components
        self.bitnet = BitNetManager(self.config)
        self.llm = LLMClient(
            host=self.config.server_host,
            port=self.config.server_port,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        self.tools = ToolRegistry()
        self.session_id = str(uuid.uuid4())
        self.conv_store = ConversationStore(str(self.config.abs_memory_db_path))
        self.vector_store = VectorStore(
            str(self.config.abs_vector_store_path),
            cache_dir=str(self.config.abs_sentence_transformer_cache),
        )
        self.ctx_manager = ContextWindowManager(self.config)
        self.ctx_manager.bind_stores(self.conv_store, self.vector_store)
        self.context_monitor = SystemContextMonitor()
        self.snapshot_mgr = SnapshotManager()

        # Notes manager (lazy-loaded to avoid circular import at module level)
        self._notes_mgr = None

        # Interrupt / confirmation state
        self.interrupt_flag = False
        self.pending_confirmation: Optional[dict] = None

        self._server_started = False

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _build_system_message(self, context_str: str, user_input: str) -> str:
        tool_signatures = []
        for tool in self.tools.list_tools():
            param_names = ", ".join(tool["parameters"].keys())
            tool_signatures.append(f"{tool['name']}({param_names})" if param_names else tool["name"])

        compact_context = self._truncate_text(context_str.replace("\n", " "), 80)
        compact_prompt = self._truncate_text(self.config.system_prompt, 110)

        system_msg = (
            f"{compact_prompt}\n"
            f"Context: {compact_context}\n"
            "For actions/tasks use JSON only:\n"
            '{"tool":"tool_name","params":{"arg":"value"}}\n'
            "For questions/chat reply in plain text. Do NOT use JSON for conversation.\n"
            "Tools: "
            + ", ".join(tool_signatures)
        )

        if any(kw in user_input.lower() for kw in _RECALL_KEYWORDS):
            try:
                past_hits = self.vector_store.search(user_input, top_k=2)
                if past_hits:
                    recall_parts = []
                    for hit in past_hits:
                        role = hit["metadata"].get("role", "?")
                        text = self._truncate_text(hit["text"].replace("\n", " "), 80)
                        recall_parts.append(f"- [{role}] {text}")
                    system_msg += "\nRelevant past context:\n" + "\n".join(recall_parts)
            except Exception:
                logger.debug("Vector recall failed", exc_info=True)

        return system_msg

    @property
    def notes_mgr(self):
        if self._notes_mgr is None:
            from use_cases.notes import NotesManager
            self._notes_mgr = NotesManager(str(self.config.abs_memory_db_path))
        return self._notes_mgr

    def start(self) -> None:
        """Start all agent components."""
        logger.info("Starting EmberOS agent...")
        logger.info("GPU mode: %s", self.config.gpu_mode)
        if self.config.gpu_mode == "cuda":
            logger.info("GPU: %s (%d MB VRAM)", self.config.gpu_name, self.config.gpu_vram_mb)
        else:
            logger.info("CPU: %s, %d threads", self.config.cpu_arch, self.config.threads)

        # Start context monitor
        self.context_monitor.start()

        # Cleanup old snapshots on start
        try:
            self.snapshot_mgr.cleanup_old(self.config.snapshot_retention_days)
        except Exception:
            pass

        # Start BitNet server
        try:
            self.bitnet.start_server()
            if self.bitnet.wait_for_server(timeout=120):
                self._server_started = True
                self.llm = LLMClient(
                    host=self.config.server_host,
                    port=self.bitnet.server_port,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                logger.info("Agent fully started")
            else:
                logger.error("BitNet server failed to start")
        except FileNotFoundError as e:
            logger.warning("BitNet binary not found — agent running without local LLM: %s", e)

    def stop(self) -> None:
        """Stop all agent components."""
        logger.info("Stopping EmberOS agent...")
        self.context_monitor.stop()
        self.bitnet.stop_server()
        self.conv_store.close()
        logger.info("Agent stopped")

    def run_once(self, user_input: str) -> str:
        """Process a single user input and return the response."""
        with self._lock:
            return self._process(user_input)

    # ── Confirmation Flow ────────────────────────────────────────

    def _check_confirmation(self, user_input: str) -> Optional[str]:
        """Handle pending confirmation if any. Returns a response or None."""
        if not self.pending_confirmation:
            return None
        lower = user_input.lower().strip()
        if lower in _CONFIRM_YES:
            action = self.pending_confirmation
            self.pending_confirmation = None
            return self._execute_confirmed(action)
        elif lower in _CONFIRM_NO:
            desc = self.pending_confirmation.get("description", "operation")
            self.pending_confirmation = None
            return f"Cancelled: {desc}"
        else:
            self.pending_confirmation = None
            return None  # treat as new query

    def _execute_confirmed(self, action: dict) -> str:
        """Execute a previously confirmed action."""
        act = action.get("action")
        params = action.get("params", {})
        try:
            if act == "organize_folder":
                from use_cases.file_ops import organize_folder_by_type
                result = organize_folder_by_type(params["folder"], preview_only=False,
                                                 snapshot_mgr=self.snapshot_mgr)
                moved = result.get("moved", {})
                lines = [f"\u2713 Moved {count} {cat}" for cat, count in moved.items()]
                return "Done!\n" + "\n".join(lines)
            elif act == "delete_file":
                from use_cases.file_ops import delete_file
                return delete_file(params["path"], snapshot_mgr=self.snapshot_mgr)
            elif act == "bulk_move":
                from use_cases.file_ops import move_file
                results = []
                for src, dst in zip(params["sources"], params["destinations"]):
                    if self.interrupt_flag:
                        self.interrupt_flag = False
                        results.append("Interrupted.")
                        break
                    results.append(move_file(src, dst, snapshot_mgr=self.snapshot_mgr))
                return "\n".join(results)
            elif act == "run_shell":
                result = self.tools.execute_tool("run_shell", {"cmd": params["cmd"]})
                return result.result if result.success else f"Error: {result.error}"
            else:
                return f"Unknown confirmed action: {act}"
        except Exception as e:
            return f"Error executing confirmed action: {e}"

    # ── Intent Detection / Routing ───────────────────────────────

    def _route_special_intents(self, user_input: str) -> Optional[str]:
        """Check for special intents and handle them directly. Returns response or None."""
        lower = user_input.lower().strip()

        # Identity / self-description
        if any(kw in lower for kw in _IDENTITY_KEYWORDS):
            return self._handle_identity()

        # Greetings
        if lower in _GREETING_KEYWORDS or lower.rstrip("!,.?") in _GREETING_KEYWORDS:
            return self._handle_greeting()

        # Note saving
        if any(kw in lower for kw in _NOTE_SAVE_KEYWORDS):
            return self._handle_note_save(user_input)

        # Note querying
        if any(kw in lower for kw in _NOTE_QUERY_KEYWORDS):
            return self._handle_note_query(user_input)

        # Calendar stub
        if any(kw in lower for kw in _CALENDAR_KEYWORDS):
            return self._handle_calendar_stub(user_input)

        # System queries (disk, RAM, CPU, processes, uptime) — direct Python, no LLM needed
        sys_result = self._handle_system_query(user_input)
        if sys_result is not None:
            return sys_result

        # App launching — direct Python, no LLM needed
        app_result = self._handle_app_launch(user_input)
        if app_result is not None:
            return app_result

        # File finding — direct Python, no LLM needed
        file_find = self._handle_file_find(user_input)
        if file_find is not None:
            return file_find

        # Folder organization with confirmation
        file_org = self._handle_file_organize(user_input)
        if file_org is not None:
            return file_org

        # File summarization ("what's in X.pdf", "summarize report.docx in E:\Quant")
        if any(kw in lower for kw in _FILE_SUMMARIZE_KEYWORDS):
            result = self._handle_file_summarize(user_input)
            if result is not None:
                return result

        # File deletion ("delete report.pdf from Downloads")
        if any(kw in lower for kw in _FILE_DELETE_KEYWORDS):
            result = self._handle_file_delete(user_input)
            if result is not None:
                return result

        # Web search stub
        if any(kw in lower for kw in _WEB_SEARCH_KEYWORDS):
            return self._handle_web_search(user_input)

        # Browser open
        if any(kw in lower for kw in _BROWSER_KEYWORDS):
            return self._handle_browser_open(user_input)

        # Vision stub
        if any(kw in lower for kw in _VISION_STUB_KEYWORDS):
            return ("Vision analysis is planned for a future version of EmberOS-Windows. "
                    "Currently I can analyze text, code, CSV, and document files.")

        # Screenshot
        if any(kw in lower for kw in _SCREENSHOT_KEYWORDS):
            return self._handle_screenshot(user_input)

        # Volume
        if any(kw in lower for kw in _VOLUME_UP_KEYWORDS):
            return self._handle_volume("up", user_input)
        if any(kw in lower for kw in _VOLUME_DOWN_KEYWORDS):
            return self._handle_volume("down", user_input)
        if any(kw in lower for kw in _MUTE_KEYWORDS):
            return self._handle_mute()

        # Dark mode / theme
        if any(kw in lower for kw in _DARK_MODE_KEYWORDS):
            return self._handle_dark_mode(user_input)

        # Brightness
        if any(kw in lower for kw in _BRIGHTNESS_KEYWORDS):
            return self._handle_brightness(user_input)

        # Battery
        if any(kw in lower for kw in _BATTERY_KEYWORDS):
            from use_cases.system_queries import get_battery_status
            return get_battery_status()

        # Screen lock / sleep / power
        if any(kw in lower for kw in _LOCK_KEYWORDS):
            from use_cases.system_queries import lock_screen
            return lock_screen()
        if any(kw in lower for kw in _SLEEP_KEYWORDS):
            from use_cases.system_queries import sleep_system
            return sleep_system()
        if any(kw in lower for kw in _SHUTDOWN_KEYWORDS):
            return self._handle_power("shutdown", user_input)
        if any(kw in lower for kw in _RESTART_KEYWORDS):
            return self._handle_power("restart", user_input)

        # Window management
        if any(kw in lower for kw in _WINDOW_LIST_KEYWORDS):
            from use_cases.system_queries import get_open_windows
            return get_open_windows()
        if any(kw in lower for kw in _WINDOW_MIN_KEYWORDS):
            from use_cases.system_queries import minimize_all_windows
            return minimize_all_windows()
        if any(kw in lower for kw in _WINDOW_FOCUS_KEYWORDS):
            return self._handle_window_focus(user_input)

        # Task management
        if any(kw in lower for kw in _TASK_ADD_KEYWORDS):
            return self._handle_task_add(user_input)
        if any(kw in lower for kw in _TASK_LIST_KEYWORDS):
            return self._handle_task_list(user_input)
        if any(kw in lower for kw in _TASK_COMPLETE_KEYWORDS):
            return self._handle_task_complete(user_input)
        if any(kw in lower for kw in _TASK_REMOVE_KEYWORDS):
            return self._handle_task_remove(user_input)
        if any(kw in lower for kw in _TASK_CLEAR_KEYWORDS):
            return self._handle_task_clear()

        # Archive operations
        if any(kw in lower for kw in _COMPRESS_KEYWORDS):
            return self._handle_compress(user_input)
        if any(kw in lower for kw in _EXTRACT_KEYWORDS):
            return self._handle_extract(user_input)

        # File discovery
        if any(kw in lower for kw in _FIND_LARGE_KEYWORDS):
            return self._handle_find_large_files(user_input)
        if any(kw in lower for kw in _FIND_OLD_KEYWORDS):
            return self._handle_find_old_files(user_input)
        if any(kw in lower for kw in _FIND_DUPES_KEYWORDS):
            return self._handle_find_duplicates(user_input)

        # Content search / analysis
        if any(kw in lower for kw in _GREP_KEYWORDS):
            return self._handle_grep(user_input)
        if any(kw in lower for kw in _DIFF_KEYWORDS):
            return self._handle_diff(user_input)
        if any(kw in lower for kw in _EXTRACT_PATTERNS_KEYWORDS):
            return self._handle_extract_patterns(user_input)

        return None

    def _handle_file_summarize(self, user_input: str) -> Optional[str]:
        """Find a file from a natural-language description and summarize its contents."""
        from use_cases.file_analysis import summarize_file, find_similar_files
        import os
        from pathlib import Path

        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return None  # couldn't parse a filename — let LLM handle it

        # --- Case 1: absolute path was given ---
        if os.path.isabs(filename) or (len(filename) > 2 and filename[1] == ':'):
            result = summarize_file(filename, llm_client=self.llm)
            if result is not None:
                return result
            suggestions = find_similar_files(
                os.path.basename(filename),
                [os.path.dirname(filename)] if os.path.dirname(filename) else None,
            )
            msg = f"File not found: {filename}"
            if suggestions:
                msg += "\n\nDid you mean:\n" + "\n".join(f"  • {s}" for s in suggestions)
            return msg

        # --- Case 2: search_root given (drive/folder specified) ---
        if search_root:
            # Try direct path first
            candidate = os.path.join(search_root, filename)
            result = summarize_file(candidate, llm_client=self.llm)
            if result is not None:
                return result

            # Recursive search inside search_root
            root_path = Path(search_root)
            if root_path.exists():
                try:
                    for p in root_path.rglob(filename):
                        r = summarize_file(str(p), llm_client=self.llm)
                        if r is not None:
                            return r
                        break
                except (PermissionError, OSError):
                    pass

            # Not found — suggest similar files in that root and elsewhere
            suggestions = find_similar_files(filename, [search_root])
            if not suggestions:
                suggestions = find_similar_files(filename)

            msg = f"'{filename}' not found in {search_root}."
            if suggestions:
                names = [f"  • {s}  (in {os.path.dirname(s) or '.'})" for s in suggestions]
                msg += "\n\nDid you mean:\n" + "\n".join(names)
            return msg

        # --- Case 3: no location given — search default locations ---
        home = os.path.expanduser("~")
        default_roots = [
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Pictures"),
            os.path.join(home, "Videos"),
            home,
        ]
        for root in default_roots:
            p = Path(root) / filename
            result = summarize_file(str(p), llm_client=self.llm)
            if result is not None:
                return result
            # Shallow rglob (2 levels) to avoid scanning whole drive
            try:
                for found in Path(root).rglob(filename):
                    r = summarize_file(str(found), llm_client=self.llm)
                    if r is not None:
                        return r
                    break
            except (PermissionError, OSError):
                pass

        # Still not found — fuzzy suggestions from common locations
        suggestions = find_similar_files(filename, default_roots)
        msg = f"'{filename}' was not found in your common folders."
        if suggestions:
            names = [f"  • {os.path.basename(s)}  (in {os.path.dirname(s)})" for s in suggestions]
            msg += "\n\nDid you mean:\n" + "\n".join(names)
        else:
            msg += "\nMake sure the file exists and try specifying the folder, e.g.:\n  what's in report.pdf in downloads"
        return msg

    def _handle_file_delete(self, user_input: str) -> Optional[str]:
        """Find a file from a natural-language description and prompt for confirmation before deleting."""
        from use_cases.file_analysis import find_similar_files
        import os
        from pathlib import Path

        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return None

        # Resolve the full path
        resolved = None

        if os.path.isabs(filename) or (len(filename) > 2 and filename[1] == ':'):
            if os.path.exists(filename):
                resolved = filename
        elif search_root:
            candidate = os.path.join(search_root, filename)
            if os.path.exists(candidate):
                resolved = candidate
            else:
                root_path = Path(search_root)
                if root_path.exists():
                    for p in root_path.rglob(filename):
                        resolved = str(p)
                        break
        else:
            home = os.path.expanduser("~")
            default_roots = [
                os.path.join(home, "Desktop"),
                os.path.join(home, "Downloads"),
                os.path.join(home, "Documents"),
                home,
            ]
            for root in default_roots:
                candidate = os.path.join(root, filename)
                if os.path.exists(candidate):
                    resolved = candidate
                    break
                try:
                    for found in Path(root).rglob(filename):
                        resolved = str(found)
                        break
                except (PermissionError, OSError):
                    pass
                if resolved:
                    break

        if not resolved:
            suggestions = find_similar_files(filename, [search_root] if search_root else None)
            msg = f"'{filename}' was not found."
            if suggestions:
                msg += "\n\nDid you mean:\n" + "\n".join(f"  • {s}" for s in suggestions)
            return msg

        self.pending_confirmation = {
            "action": "delete_file",
            "params": {"path": resolved},
            "description": f"Delete: {resolved}",
        }
        size = os.path.getsize(resolved) if os.path.isfile(resolved) else 0
        size_str = f"{size // 1024} KB" if size >= 1024 else f"{size} B"
        return (
            f"Are you sure you want to permanently delete:\n"
            f"  {resolved}  ({size_str})\n\n"
            f"A backup snapshot will be created first. (yes/no)"
        )

    def _handle_identity(self) -> str:
        return (
            "I'm EmberOS — a local AI agent running on your Windows machine.\n\n"
            "I can help with:\n"
            "  • File management — find, organize, move, copy, delete, compress, extract files\n"
            "  • File analysis — read/summarize PDF, DOCX, XLSX, PPTX, CSV, TXT and more\n"
            "  • Content search — grep files, diff two files, extract emails/URLs/phone numbers\n"
            "  • System info — disk, RAM, CPU, processes, uptime, battery\n"
            "  • System control — volume, brightness, dark mode, lock, sleep, shutdown, restart\n"
            "  • Window management — list open windows, minimize all, focus a window\n"
            "  • Media — take screenshots, resize/convert/rotate images\n"
            "  • App launching — open calculator, notepad, VS Code, browser, etc.\n"
            "  • Task management — add, list, complete, and remove to-do tasks\n"
            "  • Notes & memory — save and search notes across sessions\n"
            "  • Shell commands — run PowerShell or CMD commands\n\n"
            "Type :help to see REPL commands, or just ask me anything."
        )

    def _handle_greeting(self) -> str:
        import random
        greetings = [
            "Hello! How can I help you today?",
            "Hey! What can I do for you?",
            "Hi there! Ready to help. What do you need?",
            "Hello! I'm here and running. What's on your mind?",
        ]
        return random.choice(greetings)

    def _handle_note_save(self, user_input: str) -> str:
        """Save a user note."""
        # Extract content after save keywords
        content = user_input
        for kw in _NOTE_SAVE_KEYWORDS:
            idx = user_input.lower().find(kw)
            if idx != -1:
                content = user_input[idx + len(kw):].strip()
                break
        if not content:
            content = user_input

        # Extract any explicit user-provided tags first
        content, tags = _extract_explicit_tags(content)
        if not content:
            content = user_input

        # Use first line or first 50 chars as title
        title = content.split("\n")[0][:50]

        # If no explicit tags, try LLM-suggested tags
        if not tags:
            try:
                tag_prompt = (f"Suggest 2-3 short tags (single words) for this note, "
                              f"comma-separated, no other text: {content[:200]}")
                tag_resp = self.llm.chat([
                    {"role": "system", "content": "Reply with only comma-separated tags."},
                    {"role": "user", "content": tag_prompt},
                ])
                tags = [t.strip().lower().strip("#") for t in tag_resp.split(",") if t.strip()][:3]
            except Exception:
                pass

        note = self.notes_mgr.add(title, content, tags)
        tags_str = ", ".join(note["tags"]) if note["tags"] else "none"
        return f"I've saved that note.\nNote ID: #{note['id']}\nTags: {tags_str}"

    def _handle_note_query(self, user_input: str) -> str:
        """Search and return notes."""
        from use_cases.notes import time_ago
        # Extract query
        query = user_input
        for kw in _NOTE_QUERY_KEYWORDS:
            idx = user_input.lower().find(kw)
            if idx != -1:
                query = user_input[idx + len(kw):].strip().strip("?")
                break
        if not query:
            query = user_input

        results = self.notes_mgr.search(query, limit=5)
        if not results:
            return f"No notes found matching '{query}'."

        lines = []
        for note in results:
            ago = time_ago(note["timestamp"])
            lines.append(f"{note['content']}\n(From note #{note['id']}, saved {ago})")
        return "\n\n".join(lines)

    def _handle_calendar_stub(self, user_input: str) -> str:
        """Calendar integration stub — saves as note with time tag."""
        content = user_input
        for kw in _CALENDAR_KEYWORDS:
            idx = user_input.lower().find(kw)
            if idx != -1:
                content = user_input[idx + len(kw):].strip()
                break
        note = self.notes_mgr.add(content[:50], content, ["calendar", "reminder"])
        return (f"Calendar integration is coming soon. I've saved this as a note "
                f"with the time for now:\nNote ID: #{note['id']}\nTags: calendar, reminder")

    def _handle_web_search(self, user_input: str) -> str:
        """Web search stub — offers to open browser."""
        query = user_input
        for kw in _WEB_SEARCH_KEYWORDS:
            idx = user_input.lower().find(kw)
            if idx != -1:
                query = user_input[idx + len(kw):].strip().strip('"\'')
                break
        import urllib.parse
        safe_query = urllib.parse.quote_plus(query)
        url = f"https://google.com/search?q={safe_query}"
        return (f"Web search requires internet access. I can open your browser with the search.\n"
                f"Want me to open that search in your browser? (yes/no)")

    def _handle_browser_open(self, user_input: str) -> str:
        """Open a URL in the browser."""
        import re as _re
        url_match = _re.search(r'(https?://[^\s]+)', user_input)
        if url_match:
            url = url_match.group(1)
        else:
            # Try to extract domain-like text
            for kw in _BROWSER_KEYWORDS:
                idx = user_input.lower().find(kw)
                if idx != -1:
                    remainder = user_input[idx + len(kw):].strip()
                    if remainder:
                        url = remainder if remainder.startswith("http") else f"https://{remainder}"
                        break
            else:
                return "Please provide a URL to open."
        try:
            os.startfile(url)
            return f"Opening {url} in your browser..."
        except Exception as e:
            return f"Failed to open URL: {e}"

    def _handle_app_launch(self, user_input: str) -> Optional[str]:
        """Handle app launch requests like 'open calculator', 'launch vs code'."""
        from use_cases.app_launcher import APP_ALIASES, launch_app
        lower = user_input.lower().strip()

        for prefix in _APP_LAUNCH_PREFIXES:
            if lower.startswith(prefix):
                app_hint = lower[len(prefix):].strip().rstrip(".")
                # Direct alias match
                if app_hint in APP_ALIASES:
                    return launch_app(app_hint)
                # Partial: find longest alias contained in app_hint
                best = max(
                    (alias for alias in APP_ALIASES if alias in app_hint),
                    key=len, default=None,
                )
                if best:
                    return launch_app(best)
        return None

    def _handle_system_query(self, user_input: str) -> Optional[str]:
        """Handle direct system status queries (disk, RAM, CPU, processes, uptime)."""
        lower = user_input.lower()

        if any(kw in lower for kw in _DISK_QUERY_KEYWORDS):
            from use_cases.system_queries import get_disk_usage
            return get_disk_usage()

        if any(kw in lower for kw in _RAM_QUERY_KEYWORDS):
            from use_cases.system_queries import get_ram_status
            return get_ram_status()

        if any(kw in lower for kw in _PROCESS_QUERY_KEYWORDS):
            filter_name = None
            # Try to find a process filter word ("python processes", "chrome", etc.)
            import re as _re
            m = _re.search(r'(?:especially\s+any\s+|only\s+|filter\s+)?(\w+)\s+processes?', lower)
            if m and m.group(1) not in ("running", "active", "all", "show", "list", "what"):
                filter_name = m.group(1)
            from use_cases.system_queries import get_running_processes
            return get_running_processes(filter_name)

        if any(kw in lower for kw in _UPTIME_QUERY_KEYWORDS):
            from use_cases.system_queries import get_system_uptime
            return get_system_uptime()

        if any(kw in lower for kw in _CPU_QUERY_KEYWORDS):
            if "temperature" in lower or "temp" in lower:
                from use_cases.system_queries import check_cpu_temperature
                return check_cpu_temperature()
            from use_cases.system_queries import get_cpu_info
            return get_cpu_info()

        return None

    def _handle_file_find(self, user_input: str) -> Optional[str]:
        """Handle file search/find requests."""
        lower = user_input.lower()
        if not any(kw in lower for kw in _FILE_FIND_KEYWORDS):
            return None

        import os
        import re as _re
        from use_cases.file_ops import find_files

        # Determine file type from keywords
        file_type = None
        type_map = [
            ("pdf", "pdf"), (".py", "code"), ("python", "code"),
            ("image", "image"), ("picture", "image"), ("photo", "image"),
            ("document", "document"), ("spreadsheet", "spreadsheet"),
            ("video", "video"), ("audio", "audio"), ("archive", "archive"),
        ]
        for kw, ftype in type_map:
            if kw in lower:
                file_type = ftype
                break

        # Determine search root from common folder names
        home = os.path.expanduser("~")
        search_root = None
        for folder_kw, folder_path in [
            ("downloads", os.path.join(home, "Downloads")),
            ("documents", os.path.join(home, "Documents")),
            ("desktop", os.path.join(home, "Desktop")),
            ("pictures", os.path.join(home, "Pictures")),
            ("music", os.path.join(home, "Music")),
            ("videos", os.path.join(home, "Videos")),
        ]:
            if folder_kw in lower:
                search_root = folder_path
                break

        # Modified-time filter
        modified_days = None
        if "today" in lower or "modified today" in lower:
            modified_days = 1
        elif "last 7 days" in lower or "this week" in lower:
            modified_days = 7
        elif "last 30 days" in lower or "this month" in lower:
            modified_days = 30

        # Name query (e.g. "named budget", "name budget")
        query = ""
        m = _re.search(r"named?\s+['\"]?(\w[\w\-\.]*)", lower)
        if m:
            query = m.group(1)

        results = find_files(query, search_root, file_type, modified_days)

        if not results:
            root_desc = os.path.basename(search_root) if search_root else "home folder"
            type_desc = f" {file_type}" if file_type else ""
            q_desc = f" named '{query}'" if query else ""
            return f"No{type_desc} files{q_desc} found in {root_desc}."

        lines = [f"Found {len(results)} file(s):"]
        for path in results[:20]:
            lines.append(f"  {path}")
        if len(results) > 20:
            lines.append(f"  ... and {len(results) - 20} more")
        return "\n".join(lines)

    def _handle_file_organize(self, user_input: str) -> Optional[str]:
        """Handle folder organization with preview → confirmation flow."""
        lower = user_input.lower()
        if not any(kw in lower for kw in _FILE_ORGANIZE_KEYWORDS):
            return None

        import os
        home = os.path.expanduser("~")
        folder = None
        for folder_kw, folder_path in [
            ("downloads", os.path.join(home, "Downloads")),
            ("documents", os.path.join(home, "Documents")),
            ("desktop", os.path.join(home, "Desktop")),
            ("pictures", os.path.join(home, "Pictures")),
        ]:
            if folder_kw in lower:
                folder = folder_path
                break

        if not folder:
            return None

        from use_cases.file_ops import organize_folder_by_type
        result = organize_folder_by_type(folder, preview_only=True)

        if "error" in result:
            return result["error"]

        preview = result.get("preview", {})
        total = result.get("total_files", 0)

        if total == 0:
            return f"No files to organize in {folder}."

        lines = [f"Here's what organizing {os.path.basename(folder)} by file type would do:"]
        for cat, count in sorted(preview.items(), key=lambda x: -x[1]):
            lines.append(f"  {count} file(s) → {cat}/")
        lines.append(f"\nTotal: {total} file(s) will be moved.")
        lines.append("\nShall I proceed? (yes/no)")

        self.pending_confirmation = {
            "action": "organize_folder",
            "params": {"folder": folder},
            "description": f"Organize folder: {folder}",
        }
        return "\n".join(lines)

    # ── New capability handlers ──────────────────────────────────

    def _handle_screenshot(self, user_input: str) -> str:
        from use_cases.media_ops import take_screenshot
        return take_screenshot()

    def _handle_volume(self, direction: str, user_input: str) -> str:
        import re as _re
        from use_cases.system_queries import volume_up, volume_down, get_volume
        # Try to extract a step count ("turn up volume by 5")
        m = _re.search(r'\b(\d+)\b', user_input)
        steps = int(m.group(1)) if m else 2
        if direction == "up":
            result = volume_up(steps)
        else:
            result = volume_down(steps)
        # Append current level if readable
        level = get_volume()
        if "%" in level:
            result += f"\n{level}"
        return result

    def _handle_mute(self) -> str:
        from use_cases.system_queries import mute_volume
        return mute_volume()

    def _handle_dark_mode(self, user_input: str) -> str:
        from use_cases.system_queries import set_dark_mode, toggle_dark_mode
        lower = user_input.lower()
        if "dark" in lower and any(w in lower for w in ("enable", "turn on", "switch to", "on")):
            return set_dark_mode(True)
        if "light" in lower and any(w in lower for w in ("enable", "turn on", "switch to", "on")):
            return set_dark_mode(False)
        if "dark" in lower and any(w in lower for w in ("disable", "turn off", "off")):
            return set_dark_mode(False)
        return toggle_dark_mode()

    def _handle_brightness(self, user_input: str) -> str:
        import re as _re
        from use_cases.system_queries import get_brightness, set_brightness
        # Look for a percentage or number
        m = _re.search(r'\b(\d{1,3})\s*%?', user_input)
        if m:
            return set_brightness(int(m.group(1)))
        lower = user_input.lower()
        if any(w in lower for w in ("increase", "higher", "brighter", "up", "raise")):
            curr = get_brightness()
            # Parse currently, add 20
            nm = _re.search(r'(\d+)', curr)
            new_val = min(100, int(nm.group(1)) + 20) if nm else 80
            return set_brightness(new_val)
        if any(w in lower for w in ("decrease", "lower", "dimmer", "dim", "down", "reduce")):
            curr = get_brightness()
            nm = _re.search(r'(\d+)', curr)
            new_val = max(0, int(nm.group(1)) - 20) if nm else 40
            return set_brightness(new_val)
        return get_brightness()

    def _handle_power(self, action: str, user_input: str) -> str:
        import re as _re
        from use_cases.system_queries import shutdown_system, restart_system
        m = _re.search(r'\bin\s+(\d+)\s*(second|minute|min|sec)', user_input.lower())
        delay = 0
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            delay = val * 60 if "min" in unit else val
        if action == "shutdown":
            return shutdown_system(delay)
        return restart_system(delay)

    def _handle_window_focus(self, user_input: str) -> str:
        from use_cases.system_queries import focus_window
        import re as _re
        # Extract what to focus
        m = _re.search(r'(?:focus on|switch to window|bring up|focus window)\s+(.+)', user_input, _re.IGNORECASE)
        fragment = m.group(1).strip() if m else user_input
        return focus_window(fragment)

    # ── Task management handlers ──────────────────────────────────

    @property
    def _task_mgr(self):
        """Lazy-loaded TaskManager."""
        from use_cases.tasks import TaskManager
        if not hasattr(self, "_task_manager_instance"):
            self._task_manager_instance = TaskManager(str(self.config.abs_memory_db_path))
        return self._task_manager_instance

    def _handle_task_add(self, user_input: str) -> str:
        import re as _re
        from use_cases.tasks import time_until_due
        lower = user_input.lower()
        # Strip trigger keyword from the front
        title = user_input
        for kw in _TASK_ADD_KEYWORDS:
            idx = lower.find(kw)
            if idx != -1:
                title = user_input[idx + len(kw):].strip(" .:")
                break
        if not title:
            return "What task would you like to add?"

        # Priority extraction
        priority = "normal"
        if any(w in lower for w in ("urgent", "high priority", "asap", "important")):
            priority = "high"
        elif any(w in lower for w in ("low priority", "whenever", "someday")):
            priority = "low"

        # Due date extraction (simple ISO date or "tomorrow", "today")
        due = None
        if "tomorrow" in lower:
            import datetime as _dt
            due = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        elif "today" in lower:
            import datetime as _dt
            due = _dt.date.today().isoformat()
        else:
            m = _re.search(r'\b(\d{4}-\d{2}-\d{2})\b', user_input)
            if m:
                due = m.group(1)

        # Strip the priority/due qualifiers from title
        for noise in ("urgent", "high priority", "asap", "low priority", "whenever",
                      "someday", "today", "tomorrow"):
            title = _re.sub(rf'\b{noise}\b', '', title, flags=_re.IGNORECASE).strip()
        title = title.strip(" ,.")
        if not title:
            return "What task would you like to add?"

        task = self._task_mgr.add(title, due, priority)
        due_str = f" (due: {due})" if due else ""
        return f"Task #{task['id']} added: {task['title']}{due_str} [{task['priority']}]"

    def _handle_task_list(self, user_input: str) -> str:
        from use_cases.tasks import time_until_due
        lower = user_input.lower()
        show_all = any(w in lower for w in ("all", "completed", "done", "finished"))
        tasks = self._task_mgr.list_all() if show_all else self._task_mgr.list_pending()
        if not tasks:
            return "No tasks found." if show_all else "You have no pending tasks."
        lines = []
        for t in tasks:
            done = "[x]" if t["completed"] else "[ ]"
            pri = f" [{t['priority']}]" if t["priority"] != "normal" else ""
            due_str = f" — due: {time_until_due(t['due_date'])}" if t["due_date"] else ""
            lines.append(f"  #{t['id']} {done}{pri} {t['title']}{due_str}")
        label = "All tasks" if show_all else f"Pending tasks ({self._task_mgr.count_pending()})"
        return f"{label}:\n" + "\n".join(lines)

    def _handle_task_complete(self, user_input: str) -> str:
        import re as _re
        m = _re.search(r'#?(\d+)', user_input)
        if not m:
            return "Please specify the task ID, e.g. 'complete task #3'"
        task_id = int(m.group(1))
        task = self._task_mgr.complete(task_id)
        if task is None:
            return f"Task #{task_id} not found or already completed."
        return f"Task #{task_id} marked as done: {task['title']}"

    def _handle_task_remove(self, user_input: str) -> str:
        import re as _re
        m = _re.search(r'#?(\d+)', user_input)
        if not m:
            return "Please specify the task ID, e.g. 'remove task #2'"
        task_id = int(m.group(1))
        removed = self._task_mgr.remove(task_id)
        return f"Task #{task_id} deleted." if removed else f"Task #{task_id} not found."

    def _handle_task_clear(self) -> str:
        count = self._task_mgr.clear_completed()
        return f"Cleared {count} completed task(s)."

    # ── Archive / file analysis handlers ─────────────────────────

    def _handle_compress(self, user_input: str) -> str:
        from use_cases.file_ops import compress_to_zip
        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return "Please specify a file or folder to compress, e.g. 'compress report.docx in Downloads'"
        import os
        from pathlib import Path
        resolved = None
        if os.path.isabs(filename) and os.path.exists(filename):
            resolved = filename
        elif search_root:
            candidate = os.path.join(search_root, filename)
            if os.path.exists(candidate):
                resolved = candidate
        if not resolved:
            home = os.path.expanduser("~")
            for root in [os.path.join(home, d) for d in ("Downloads", "Documents", "Desktop")]:
                candidate = os.path.join(root, filename)
                if os.path.exists(candidate):
                    resolved = candidate
                    break
        if not resolved:
            return f"'{filename}' was not found — please provide the full path."
        return compress_to_zip([resolved])

    def _handle_extract(self, user_input: str) -> str:
        from use_cases.file_ops import extract_archive
        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return "Please specify an archive to extract, e.g. 'extract archive.zip from Downloads'"
        import os
        resolved = None
        if os.path.isabs(filename) and os.path.exists(filename):
            resolved = filename
        elif search_root:
            candidate = os.path.join(search_root, filename)
            if os.path.exists(candidate):
                resolved = candidate
        if not resolved:
            home = os.path.expanduser("~")
            for root in [os.path.join(home, d) for d in ("Downloads", "Documents", "Desktop")]:
                candidate = os.path.join(root, filename)
                if os.path.exists(candidate):
                    resolved = candidate
                    break
        if not resolved:
            return f"'{filename}' was not found — please provide the full path."
        return extract_archive(resolved)

    def _handle_find_large_files(self, user_input: str) -> str:
        import re as _re, os
        from use_cases.file_ops import find_large_files
        lower = user_input.lower()
        home = os.path.expanduser("~")
        root = None
        for folder_kw, folder_path in [
            ("downloads", os.path.join(home, "Downloads")),
            ("documents", os.path.join(home, "Documents")),
            ("desktop", os.path.join(home, "Desktop")),
        ]:
            if folder_kw in lower:
                root = folder_path
                break
        min_mb = 100
        m = _re.search(r'(\d+)\s*[mg]b', lower)
        if m:
            min_mb = int(m.group(1))
        results = find_large_files(root, min_mb, 20)
        if not results:
            return f"No files larger than {min_mb} MB found."
        lines = [f"Files larger than {min_mb} MB ({len(results)} found):"]
        for f in results[:20]:
            lines.append(f"  {f['size_human']:>8}  {f['path']}")
        return "\n".join(lines)

    def _handle_find_old_files(self, user_input: str) -> str:
        import re as _re, os
        from use_cases.file_ops import find_old_files
        lower = user_input.lower()
        days = 365
        m = _re.search(r'(\d+)\s*(?:day|week|month|year)', lower)
        if m:
            val = int(m.group(1))
            if "week" in lower:
                days = val * 7
            elif "month" in lower:
                days = val * 30
            elif "year" in lower:
                days = val * 365
            else:
                days = val
        results = find_old_files(None, days, 20)
        if not results:
            return f"No files older than {days} days found."
        lines = [f"Files not modified in {days}+ days ({len(results)} found):"]
        for f in results[:20]:
            lines.append(f"  {f['last_modified']}  {f['path']}")
        return "\n".join(lines)

    def _handle_find_duplicates(self, user_input: str) -> str:
        import os
        from use_cases.file_ops import find_duplicate_files
        lower = user_input.lower()
        home = os.path.expanduser("~")
        root = None
        for folder_kw, folder_path in [
            ("downloads", os.path.join(home, "Downloads")),
            ("documents", os.path.join(home, "Documents")),
            ("desktop", os.path.join(home, "Desktop")),
        ]:
            if folder_kw in lower:
                root = folder_path
                break
        info = find_duplicate_files(root, 20)
        if info["duplicate_groups"] == 0:
            return "No duplicate files found."
        lines = [
            f"Found {info['duplicate_groups']} duplicate group(s). "
            f"Wasted space: {info['wasted_space']}"
        ]
        for i, (h, paths) in enumerate(info["groups"].items()):
            if i >= 10:
                lines.append(f"  ... and {info['duplicate_groups']-10} more groups")
                break
            lines.append(f"\n  Group {i+1}:")
            for p in paths:
                lines.append(f"    • {p}")
        return "\n".join(lines)

    def _handle_grep(self, user_input: str) -> str:
        from use_cases.file_analysis import grep_file
        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return "Please specify a file to search, e.g. 'search inside notes.txt for TODO'"
        import re as _re, os
        # Extract search pattern — text after "for" or "containing"
        m = _re.search(r'(?:for|containing|pattern)\s+["\']?([^"\']+)["\']?', user_input, _re.IGNORECASE)
        pattern = m.group(1).strip() if m else ""
        if not pattern:
            return "Please specify a search pattern, e.g. 'grep notes.txt for TODO'"
        # Resolve file
        resolved = None
        if os.path.isabs(filename) and os.path.exists(filename):
            resolved = filename
        elif search_root:
            candidate = os.path.join(search_root, filename)
            if os.path.exists(candidate):
                resolved = candidate
        if not resolved:
            home = os.path.expanduser("~")
            for root in [os.path.join(home, d) for d in ("Downloads", "Documents", "Desktop")]:
                candidate = os.path.join(root, filename)
                if os.path.exists(candidate):
                    resolved = candidate
                    break
        if not resolved:
            return f"'{filename}' was not found."
        return grep_file(resolved, pattern)

    def _handle_diff(self, user_input: str) -> str:
        from use_cases.file_analysis import diff_files
        import re as _re
        ext_pat = _ext_pattern()
        files = _re.findall(ext_pat, user_input, _re.IGNORECASE)
        if len(files) < 2:
            return "Please specify two files to compare, e.g. 'diff report_v1.txt report_v2.txt'"
        return diff_files(files[0], files[1])

    def _handle_extract_patterns(self, user_input: str) -> str:
        from use_cases.file_analysis import extract_patterns
        filename, search_root = _parse_file_query(user_input)
        if not filename:
            return "Please specify a file, e.g. 'extract emails from contacts.txt'"
        import os
        resolved = None
        if os.path.isabs(filename) and os.path.exists(filename):
            resolved = filename
        elif search_root:
            candidate = os.path.join(search_root, filename)
            if os.path.exists(candidate):
                resolved = candidate
        if not resolved:
            home = os.path.expanduser("~")
            for root in [os.path.join(home, d) for d in ("Downloads", "Documents", "Desktop")]:
                candidate = os.path.join(root, filename)
                if os.path.exists(candidate):
                    resolved = candidate
                    break
        if not resolved:
            return f"'{filename}' was not found."
        lower = user_input.lower()
        types = []
        for pt in ("email", "url", "phone", "date", "ipv4"):
            if pt in lower or (pt == "ip" and "ip" in lower):
                types.append(pt)
        result = extract_patterns(resolved, types or None)
        summary = result.pop("_summary", "")
        lines = [summary]
        for ptype, matches in result.items():
            if matches:
                lines.append(f"\n{ptype.upper()}S ({len(matches)}):")
                lines.extend(f"  {m}" for m in matches[:20])
                if len(matches) > 20:
                    lines.append(f"  ... and {len(matches)-20} more")
        return "\n".join(lines)

    # ── Main Processing ──────────────────────────────────────────

    def _process(self, user_input: str) -> str:
        """Internal processing logic."""
        # Check pending confirmation first
        conf_result = self._check_confirmation(user_input)
        if conf_result is not None:
            return conf_result

        # Check special intents
        special = self._route_special_intents(user_input)
        if special is not None:
            self._store_turn(user_input, special)
            return special

        # Capture context
        context_str = self.context_monitor.format_context()
        timestamp = datetime.now(timezone.utc).isoformat()

        system_msg = self._build_system_message(context_str, user_input)

        # Build messages via context window manager
        messages = self.ctx_manager.get_messages_for_llm(
            self.session_id, system_msg, user_input, self.llm,
        )
        prompt_estimate = sum(self.ctx_manager._estimate_tokens(m["content"]) for m in messages)
        logger.info("LLM prompt estimate: ~%d tokens across %d messages", prompt_estimate, len(messages))

        # Call LLM
        try:
            response = self.llm.chat(messages)
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"[Error communicating with LLM: {e}]"

        # Check for tool calls in response
        tool_calls_raw = self._extract_tool_calls(response)
        tool_calls_json = None

        # If LLM returned a malformed tool call (e.g. params:"" instead of {}), recover it
        if not tool_calls_raw and _looks_like_tool_call(response):
            logger.warning("LLM returned malformed/bare tool call, attempting recovery: %r", response[:120])
            tool_name_match = re.search(r'"tool"\s*:\s*"([^"]+)"', response)
            if tool_name_match:
                tool_name = tool_name_match.group(1)
                if self.tools._tools.get(tool_name):
                    result = self.tools.execute_tool(tool_name, {})
                    response = str(result.result) if result.success else (
                        "I encountered an issue with that request. Could you rephrase it?")
                else:
                    response = "I'm not sure how to help with that. Could you rephrase your question?"
            else:
                response = "I'm not sure how to help with that. Could you rephrase your question?"

        if tool_calls_raw:
            # Check if any tool call needs confirmation
            needs_confirm = self._check_destructive(tool_calls_raw, user_input)
            if needs_confirm:
                self._store_turn(user_input, needs_confirm)
                return needs_confirm

            tool_results = self._execute_tools_with_interrupt(tool_calls_raw)
            tool_calls_json = json.dumps([c["tool"] for c in tool_calls_raw])

            # Build tool result message
            result_lines = []
            for call, result in zip(tool_calls_raw, tool_results):
                result_lines.append(
                    f"Tool `{call['tool']}` \u2192 "
                    f"{'success' if result.success else 'error'}: "
                    f"{json.dumps(result.result) if result.success else result.error}"
                )
            tool_result_msg = "\n".join(result_lines)

            # Check if interrupted
            if self.interrupt_flag:
                self.interrupt_flag = False
                self._store_turn(user_input, f"Task interrupted. Partial results:\n{tool_result_msg}")
                return f"Task interrupted. Partial results:\n{tool_result_msg}"

            # Store intermediate assistant + tool_result in conversation store
            self.conv_store.add(self.session_id, "assistant", response, tool_calls=tool_calls_json)
            self.conv_store.add(self.session_id, "tool_result", tool_result_msg)

            # Rebuild messages with tool results included and call LLM again
            if not self.interrupt_flag:
                messages = self.ctx_manager.get_messages_for_llm(
                    self.session_id, system_msg, f"[Tool results]\n{tool_result_msg}", self.llm,
                )
                try:
                    response = self.llm.chat(messages)
                except Exception as e:
                    logger.error("LLM follow-up call failed: %s", e)
                    response = f"[Tool results]\n{tool_result_msg}"

        self._store_turn(user_input, response, context_str=context_str,
                         tool_calls_json=tool_calls_json, timestamp=timestamp)
        return response

    def _check_destructive(self, tool_calls: list[dict], user_input: str) -> Optional[str]:
        """Check if tool calls require confirmation. Returns confirmation message or None."""
        if not self.config.confirm_destructive:
            return None

        for call in tool_calls:
            tool_name = call.get("tool", "")
            params = call.get("params", {})

            if tool_name == "run_shell":
                cmd = params.get("cmd", "").lower()
                if any(d in cmd for d in _DESTRUCTIVE_SHELL):
                    self.pending_confirmation = {
                        "action": "run_shell",
                        "params": params,
                        "description": f"Run command: {params.get('cmd', '')}",
                    }
                    return f"This command may be destructive: `{params.get('cmd', '')}`\nProceed? (yes/no)"

            elif tool_name == "delete_file":
                path = params.get("path", "")
                self.pending_confirmation = {
                    "action": "delete_file",
                    "params": params,
                    "description": f"Delete: {path}",
                }
                return f"About to delete: `{path}`\nProceed? (yes/no)"

            elif tool_name == "organize_folder":
                if not params.get("preview_only", True):
                    folder = params.get("folder", "")
                    self.pending_confirmation = {
                        "action": "organize_folder",
                        "params": params,
                        "description": f"Organize folder: {folder}",
                    }
                    return f"About to organize `{folder}` by file type into subfolders.\nProceed? (yes/no)"

        return None

    def _execute_tools_with_interrupt(self, calls: list[dict]) -> list[ToolResult]:
        """Execute tool calls, checking interrupt flag between calls."""
        results = []
        for call in calls:
            if self.interrupt_flag:
                results.append(ToolResult(success=False, error="Interrupted"))
                break
            result = self.tools.execute_tool(call["tool"], call.get("params", {}))
            results.append(result)
        return results

    def _store_turn(self, user_input: str, response: str,
                    context_str: str = "", tool_calls_json: str = None,
                    timestamp: str = None):
        """Persist a user+assistant turn to SQLite and vector store."""
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

        row_id_user = self.conv_store.add(
            self.session_id, "user", user_input, context_snapshot=context_str,
        )
        row_id_assistant = self.conv_store.add(
            self.session_id, "assistant", response, tool_calls=tool_calls_json,
        )
        self.vector_store.add(
            str(row_id_user), user_input,
            {"session_id": self.session_id, "role": "user", "timestamp": timestamp},
        )
        self.vector_store.add(
            str(row_id_assistant), response,
            {"session_id": self.session_id, "role": "assistant", "timestamp": timestamp},
        )
        self.conv_store.enforce_max(self.config.max_total_conversations)
        threading.Thread(
            target=self.ctx_manager.maybe_trigger_summarization,
            args=(self.session_id, self.llm),
            daemon=True,
        ).start()

    def _extract_tool_calls(self, text: str) -> list[dict]:
        """Extract tool call JSON from LLM response text."""
        calls = []

        # Try to find JSON array of tool calls
        array_pattern = r'\[\s*\{[^[\]]*"tool"\s*:.*?\}\s*\]'
        array_match = re.search(array_pattern, text, re.DOTALL)
        if array_match:
            try:
                parsed = json.loads(array_match.group())
                if isinstance(parsed, list):
                    for item in parsed:
                        if "tool" in item:
                            calls.append({
                                "tool": item["tool"],
                                "params": item.get("params", {}),
                            })
                    return calls
            except json.JSONDecodeError:
                pass

        # Try to find single tool call JSON objects
        obj_pattern = r'\{[^{}]*"tool"\s*:\s*"[^"]+"\s*,\s*"params"\s*:\s*\{[^{}]*\}[^{}]*\}'
        for match in re.finditer(obj_pattern, text, re.DOTALL):
            try:
                parsed = json.loads(match.group())
                if "tool" in parsed:
                    calls.append({
                        "tool": parsed["tool"],
                        "params": parsed.get("params", {}),
                    })
            except json.JSONDecodeError:
                continue

        return calls

    def _execute_tools(self, calls: list[dict]) -> list[ToolResult]:
        """Execute tool calls — parallel if multiple."""
        if len(calls) == 1:
            return [self.tools.execute_tool(calls[0]["tool"], calls[0]["params"])]
        return self.tools.execute_parallel(calls)

    def run_interactive_loop(self) -> None:
        """Run an interactive REPL."""
        print("EmberOS Agent — type 'exit' or 'quit' to stop")
        print("-" * 50)
        while True:
            try:
                user_input = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break
            response = self.run_once(user_input)
            print(f"\nEmberOS> {response}")
