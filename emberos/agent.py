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

_SMART_ORGANIZE_KEYWORDS = (
    "organize by content", "organise by content",
    "group by content", "group similar files", "group similar documents",
    "organize similar", "organise similar",
    "smart organize", "smart organise",
    "group by topic", "organize by topic",
    "group related files", "organize related files",
    "sort by content", "cluster files",
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
# Catches natural patterns like "delete marcus-email.txt" or "remove report.pdf on Desktop"
_FILE_DELETE_PAT = re.compile(r'\b(?:delete|remove|erase)\s+(?:\w[\w.\-]+\.\w{2,5})')

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
                         "task list", "show todo", "what tasks", "tasks do i have",
                         "tasks for today", "tasks today", "my tasks today",
                         "any tasks", "open tasks", "remaining tasks", "tasks left")
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
_UNDO_KEYWORDS = (
    "undo", "revert", "rollback", "roll back",
    "turn things back", "put things back", "go back to how",
    "restore original", "don't like it", "i don't like it",
    "take it back", "undo organize", "undo the organize",
)
_GREP_KEYWORDS = ("search inside", "grep", "find text in", "search text in",
                    "look inside", "find in file", "search for text in")
_DIFF_KEYWORDS = ("diff", "compare file", "compare the files", "what changed between",
                    "difference between files", "show diff")
_EXTRACT_PATTERNS_KEYWORDS = ("extract emails", "find emails", "extract urls", "find urls",
                                "find phone numbers", "extract phone", "extract dates from",
                                "extract ips", "extract patterns")

_CONTENT_SEARCH_KEYWORDS = (
    "find documents mentioning", "find files mentioning",
    "find documents containing", "find files containing",
    "find files that contain", "find documents that contain",
    "search for text in folder", "search inside all",
    "search documents for", "search files for",
    "find documents that mention", "which files mention",
    "which documents contain", "which files contain",
    "look for text in", "scan folder for",
)

_BATCH_DELETE_KEYWORDS = (
    "delete those files", "delete all of them", "delete these files",
    "remove those files", "remove all of them", "remove these files",
    "delete them all", "remove them all", "delete the found files",
    "delete all those", "delete all these", "purge those files",
    "purge these files", "purge them", "delete the results",
    "delete the matches",
)

# ── Multi-document / synthesis keywords ──────────────────────────────────────

_LIST_DIR_KEYWORDS = (
    "list files in", "list the files in", "show files in", "what files are in",
    "what's in the folder", "show me the files in", "list directory",
    "show the contents of", "what is in the folder", "files in the",
    "what documents are in", "show folder contents",
    # broader natural phrasings
    "what are the files in", "what are the files on",
    "files in my", "files on my", "files in",
    "what's in my", "what is in my",
    "what's inside", "what is inside",
    "show me what's in", "show me what is in",
    "what do i have in",
)

_FOLDER_READ_KEYWORDS = (
    "go through all", "read all", "summarize all", "summarize every",
    "summarize all documents", "summarize all files", "summarize everything in",
    "process all", "analyze all", "go through the files", "go through the documents",
    "extract from all", "read all the documents", "read all the files",
    "create a knowledge summary", "build a study guide", "study guide from",
    "knowledge summary from",
)

_MULTI_DOC_COMPARE_KEYWORDS = (
    "compare these documents", "compare the documents", "compare these files",
    "compare all files", "compare all documents", "comparison of documents",
    "compare documents in", "compare files in", "create a comparison report",
    "comparison report",
)

_CREATE_DOC_KEYWORDS = (
    "create a document", "create a report", "create a summary document",
    "create a pdf", "create a markdown", "create a txt", "create a docx",
    "generate a document", "generate a report", "write a document",
    "write a report", "save this as a document", "save the summary",
    "save that as a", "create a combined", "create an analysis",
    "build a report", "build a document", "compile a report",
    "make a report", "make a document", "convert that to a",
    "create a short summary", "create a full report", "create an executive summary",
    "create a structured", "create a professional report",
    # Email / letter / memo types
    "write an email", "draft an email", "compose an email",
    "write a letter", "draft a letter", "compose a letter",
    "write a memo", "draft a memo",
    "write a follow-up", "draft a follow-up",
    "write a short follow-up", "write a short email",
    "write a message", "draft a message",
    "write me an email", "write me a letter", "write me a follow-up",
    "write me a short", "draft me an email", "draft me a letter",
)

_FOLDER_EXPLAIN_KEYWORDS = (
    "what does this folder contain", "what is in this folder",
    "explain this folder", "what's in this directory", "describe this folder",
    "folder overview", "what kinds of files", "folder summary",
    "what's in the directory", "explain the folder", "overview of the folder",
    "what does the folder contain", "what does the directory contain",
    "project folder contains", "what this folder contains",
)

_DOC_QA_KEYWORDS = (
    "what does the report say about", "what does the document say about",
    "which document mentions", "which document contains",
    "what does it say about", "find information about in the documents",
    "search the documents for", "what was mentioned about",
    "any mention of in the documents",
)

_FOLDER_TOPICS_KEYWORDS = (
    "key topics across", "common topics in the", "recurring topics",
    "what information appears in most", "topics across",
    "main subjects across", "main themes across", "what are the themes in the",
    "important information across", "identify related documents",
    "detect repeated information", "what's repeated across",
    "group related documents",
)

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
    "hello", "hi", "hi!", "hi there", "hey", "hey!", "hey there",
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
        self.pending_clarification: Optional[dict] = None

        # Conversation context: tracks last folder, files, and document text
        # so follow-up commands like "what's in that document" resolve correctly.
        self._ctx: dict = {
            "last_dir": None,
            "last_files": [],
            "last_doc_text": "",    # summary/header shown to user
            "last_doc_full": "",    # full raw document content for doc QA
            "last_summary": "",
            "last_output_path": None,
        }

        # Handler map for LLM-based routing — populated lazily on first use
        self._tool_handler_map: dict = {}

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
                self._warmup_llm()
                logger.info("Agent fully started")
            else:
                logger.error("BitNet server failed to start")
        except FileNotFoundError as e:
            logger.warning("BitNet binary not found — agent running without local LLM: %s", e)

    def _warmup_llm(self) -> None:
        """Send a trivial request to prime the model's KV cache.

        The first inference after a cold model load is error-prone on some
        hardware (ConnectionResetError / OOM).  Warming up here means the
        first real user request hits an already-initialised model.
        If the warmup itself crashes BitNet, the server is restarted once
        and a second warmup is attempted.
        """
        for attempt in range(2):
            try:
                self.llm.chat(
                    [{"role": "user", "content": "hi"}],
                    max_tokens=3,
                )
                logger.info("LLM warmup OK")
                return
            except Exception as exc:
                logger.warning("LLM warmup failed (attempt %d): %s", attempt + 1, exc)
                if attempt == 0:
                    logger.info("Restarting BitNet after warmup failure...")
                    if self.bitnet.restart_server():
                        self.llm = LLMClient(
                            host=self.config.server_host,
                            port=self.bitnet.server_port,
                            temperature=self.config.temperature,
                            max_tokens=self.config.max_tokens,
                        )
                    else:
                        logger.error("BitNet restart failed — LLM unavailable")
                        return
        logger.warning("LLM warmup could not complete — first user request may be slow")

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
            elif act == "smart_organize":
                from use_cases.file_ops import smart_organize_folder
                result = smart_organize_folder(
                    params["folder"],
                    llm_client=self.llm,
                    preview_only=False,
                    snapshot_mgr=self.snapshot_mgr,
                )
                if "error" in result:
                    return result["error"]
                moved = result.get("moved", {})
                lines = [f"Done! Moved {result.get('total_moved', 0)} file(s):"]
                for group_name, count in moved.items():
                    lines.append(f"  {group_name}/  \u2190  {count} file(s)")
                return "\n".join(lines)
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
            elif act == "batch_delete":
                from use_cases.file_ops import delete_file
                paths = params.get("paths", [])
                if not paths:
                    return "No files to delete."
                results = []
                for path in paths:
                    if self.interrupt_flag:
                        self.interrupt_flag = False
                        results.append("Interrupted.")
                        break
                    try:
                        msg = delete_file(path, snapshot_mgr=self.snapshot_mgr)
                        results.append(msg)
                    except Exception as e:
                        results.append(f"Failed to delete {path}: {e}")
                self._ctx["last_files"] = []
                return "\n".join(results) if results else "No files were deleted."
            elif act == "undo_last":
                return self.snapshot_mgr.rollback_last_batch()
            else:
                return f"Unknown confirmed action: {act}"
        except Exception as e:
            return f"Error executing confirmed action: {e}"

    # ── Intent Detection / Routing ───────────────────────────────

    def _build_handler_map(self) -> dict:
        """Return a mapping from router tool names to callable handlers.

        All callables accept a single positional argument: the raw user_input string.
        Built lazily so __init__ stays clean and method references resolve correctly.
        """
        from use_cases.system_queries import (
            get_battery_status, lock_screen, sleep_system,
            get_open_windows, minimize_all_windows,
        )
        return {
            # --- File / folder ---
            "list_dir":           self._handle_list_dir,
            "file_summarize":     self._handle_file_summarize,
            "folder_summarize":   self._handle_folder_docs_request,
            "folder_explain":     self._handle_folder_explain,
            "grep_folder":        self._handle_content_search,
            "grep_file":          self._handle_grep,
            "batch_delete":       self._handle_batch_delete,
            "file_delete":        self._handle_file_delete,
            "create_doc":         self._handle_create_doc,
            "multi_doc_compare":  self._handle_multi_doc_compare,
            "folder_topics":      self._handle_folder_topics,
            "doc_qa":             self._handle_doc_qa,
            "diff_files":         self._handle_diff,
            "extract_patterns":   self._handle_extract_patterns,
            "compress":           self._handle_compress,
            "extract":            self._handle_extract,
            "find_large_files":   self._handle_find_large_files,
            "find_old_files":     self._handle_find_old_files,
            "find_duplicates":    self._handle_find_duplicates,
            "undo":               self._handle_undo,
            "smart_organize":     self._handle_smart_organize,
            # --- Tasks ---
            "list_tasks":         self._handle_task_list,
            "add_task":           self._handle_task_add,
            "complete_task":      self._handle_task_complete,
            "remove_task":        self._handle_task_remove,
            "clear_tasks":        lambda q: self._handle_task_clear(q),
            # --- System controls ---
            "screenshot":         self._handle_screenshot,
            "volume_up":          lambda q: self._handle_volume("up", q),
            "volume_down":        lambda q: self._handle_volume("down", q),
            "mute":               lambda q: self._handle_mute(),
            "dark_mode":          self._handle_dark_mode,
            "brightness_up":      self._handle_brightness,
            "brightness_down":    self._handle_brightness,
            "battery":            lambda q: get_battery_status(),
            "lock":               lambda q: lock_screen(),
            "sleep":              lambda q: sleep_system(),
            "shutdown":           lambda q: self._handle_power("shutdown", q),
            "restart":            lambda q: self._handle_power("restart", q),
            "window_list":        lambda q: get_open_windows(),
            "window_minimize":    lambda q: minimize_all_windows(),
            "window_focus":       self._handle_window_focus,
            # --- Web / notes ---
            "web_search":         self._handle_web_search,
            "note_save":          self._handle_note_save,
            "note_query":         self._handle_note_query,
        }

    def _route_via_manifest(self, user_input: str) -> Optional[str]:
        """Route user input by asking the LLM to match it against the tool manifest.

        Returns the handler's response string, or None if routing failed
        (unknown tool name, handler returned None, or LLM unavailable).
        For chained tools the handlers are called in sequence; context (_ctx)
        flows naturally between them since each handler reads/writes self._ctx.
        """
        if not self.llm:
            return None

        from emberos.router import route
        result = route(user_input, self.llm)
        tool  = result.get("tool")
        chain = result.get("chain")

        if not tool:
            return None

        if not self._tool_handler_map:
            self._tool_handler_map = self._build_handler_map()

        if chain and len(chain) > 1:
            # Multi-step: run each handler in order and collect outputs
            outputs = []
            for step in chain:
                handler = self._tool_handler_map.get(step)
                if not handler:
                    continue
                try:
                    step_result = handler(user_input)
                except Exception as e:
                    logger.warning("Chain step '%s' raised: %s", step, e)
                    step_result = None
                if step_result:
                    outputs.append(str(step_result))
                # Stop if a confirmation prompt is waiting (e.g. batch_delete)
                if self.pending_confirmation:
                    break
            return "\n\n".join(outputs) if outputs else None

        handler = self._tool_handler_map.get(tool)
        if not handler:
            return None

        try:
            return handler(user_input)
        except Exception as e:
            logger.warning("Manifest-routed handler '%s' raised: %s", tool, e)
            return None

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

        # Specific file-ops searches — must precede generic _handle_file_find
        # so "find duplicate files" / "find large files" are not swallowed by
        # the broad "find files" keyword in _FILE_FIND_KEYWORDS.
        if any(kw in lower for kw in _FIND_DUPES_KEYWORDS):
            return self._handle_find_duplicates(user_input)
        if any(kw in lower for kw in _FIND_LARGE_KEYWORDS):
            return self._handle_find_large_files(user_input)
        if any(kw in lower for kw in _FIND_OLD_KEYWORDS):
            return self._handle_find_old_files(user_input)
        if any(kw in lower for kw in _UNDO_KEYWORDS):
            return self._handle_undo(user_input)

        # File finding — direct Python, no LLM needed
        file_find = self._handle_file_find(user_input)
        if file_find is not None:
            return file_find

        # Directory listing — direct Python, no LLM needed
        if any(kw in lower for kw in _LIST_DIR_KEYWORDS):
            return self._handle_list_dir(user_input)

        # Task management — SQLite only, no LLM needed
        if any(kw in lower for kw in _TASK_ADD_KEYWORDS):
            return self._handle_task_add(user_input)
        if any(kw in lower for kw in _TASK_LIST_KEYWORDS):
            return self._handle_task_list(user_input)
        if any(kw in lower for kw in _TASK_COMPLETE_KEYWORDS):
            return self._handle_task_complete(user_input)
        if any(kw in lower for kw in _TASK_REMOVE_KEYWORDS):
            return self._handle_task_remove(user_input)
        if any(kw in lower for kw in _TASK_CLEAR_KEYWORDS):
            return self._handle_task_clear(user_input)
        if lower.endswith(("task", "tasks", "todo", "to-do")):
            first = lower.split()[0] if lower.split() else ""
            if first in ("clear", "remove", "delete", "erase", "drop"):
                return self._handle_task_remove(user_input)
            if first in ("complete", "done", "finish", "mark", "finished", "close"):
                return self._handle_task_complete(user_input)

        # Folder organization with confirmation
        file_org = self._handle_file_organize(user_input)
        if file_org is not None:
            return file_org

        # ── Deterministic ops — run before LLM routing ───────────────────────
        # All of these are pure local operations. Moving them here ensures they
        # work whether or not BitNet is available, without growing fallback lists.

        # File summarization
        if any(kw in lower for kw in _FILE_SUMMARIZE_KEYWORDS):
            result = self._handle_file_summarize(user_input)
            if result is not None:
                return result

        # Smart content-aware organization
        if any(kw in lower for kw in _SMART_ORGANIZE_KEYWORDS):
            return self._handle_smart_organize(user_input)

        # Batch delete from context ("delete those files", "remove all of them")
        if any(kw in lower for kw in _BATCH_DELETE_KEYWORDS):
            result = self._handle_batch_delete(user_input)
            if result is not None:
                return result

        # File deletion — keyword match OR naturalistic "delete X.ext" pattern
        if any(kw in lower for kw in _FILE_DELETE_KEYWORDS) or _FILE_DELETE_PAT.search(lower):
            result = self._handle_file_delete(user_input)
            if result is not None:
                return result

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

        # Archive operations
        if any(kw in lower for kw in _COMPRESS_KEYWORDS):
            return self._handle_compress(user_input)
        if any(kw in lower for kw in _EXTRACT_KEYWORDS):
            return self._handle_extract(user_input)

        # Content search across folder
        if any(kw in lower for kw in _CONTENT_SEARCH_KEYWORDS):
            return self._handle_content_search(user_input)

        # In-file content search / analysis
        if any(kw in lower for kw in _GREP_KEYWORDS):
            return self._handle_grep(user_input)
        if any(kw in lower for kw in _DIFF_KEYWORDS):
            return self._handle_diff(user_input)
        if any(kw in lower for kw in _EXTRACT_PATTERNS_KEYWORDS):
            return self._handle_extract_patterns(user_input)

        # ── LLM-based routing (primary path) ─────────────────────────────────
        # The LLM reads a compact tool manifest and picks the right handler.
        # This replaces the need to keep growing the keyword lists below.
        # If the LLM is unavailable, execution falls through to keyword fallback.
        manifest_result = self._route_via_manifest(user_input)
        if manifest_result is not None:
            return manifest_result

        # ── Keyword fallback (when LLM is unavailable) ────────────────────────
        # File summarization ("what's in X.pdf", "summarize report.docx in E:\Quant")
        if any(kw in lower for kw in _FILE_SUMMARIZE_KEYWORDS):
            result = self._handle_file_summarize(user_input)
            if result is not None:
                return result

        # Document follow-up questions (LLM offline — search stored document context)
        if (self._ctx.get("last_doc_text")
                and lower.split()
                and lower.split()[0] in {"what", "who", "how", "why", "when", "where",
                                          "explain", "tell", "describe", "which", "show"}):
            result = self._handle_doc_qa(user_input)
            if result is not None:
                return result

        # Smart content-aware organization
        if any(kw in lower for kw in _SMART_ORGANIZE_KEYWORDS):
            return self._handle_smart_organize(user_input)

        # Batch delete from context ("delete those files", "remove all of them")
        if any(kw in lower for kw in _BATCH_DELETE_KEYWORDS):
            result = self._handle_batch_delete(user_input)
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

        # Content search across folder ("find files containing X in Invoices")
        if any(kw in lower for kw in _CONTENT_SEARCH_KEYWORDS):
            return self._handle_content_search(user_input)

        # Content search / analysis
        if any(kw in lower for kw in _GREP_KEYWORDS):
            return self._handle_grep(user_input)
        if any(kw in lower for kw in _DIFF_KEYWORDS):
            return self._handle_diff(user_input)
        if any(kw in lower for kw in _EXTRACT_PATTERNS_KEYWORDS):
            return self._handle_extract_patterns(user_input)

        # Directory listing with context tracking
        if any(kw in lower for kw in _LIST_DIR_KEYWORDS):
            return self._handle_list_dir(user_input)

        # Folder-level document workflows
        if any(kw in lower for kw in _FOLDER_EXPLAIN_KEYWORDS):
            return self._handle_folder_explain(user_input)
        if any(kw in lower for kw in _FOLDER_READ_KEYWORDS):
            return self._handle_folder_docs_request(user_input)
        if any(kw in lower for kw in _MULTI_DOC_COMPARE_KEYWORDS):
            return self._handle_multi_doc_compare(user_input)
        if any(kw in lower for kw in _FOLDER_TOPICS_KEYWORDS):
            return self._handle_folder_topics(user_input)

        # Document creation with clarification flow
        if any(kw in lower for kw in _CREATE_DOC_KEYWORDS):
            return self._handle_create_doc(user_input)

        # Document QA against loaded context
        if any(kw in lower for kw in _DOC_QA_KEYWORDS):
            result = self._handle_doc_qa(user_input)
            if result is not None:
                return result

        return None

    def _store_doc_context(self, file_path: str, summary: str) -> None:
        """Store summary + raw content into conversation context for follow-up doc_qa."""
        from pathlib import Path as _Path
        self._ctx["last_doc_text"] = summary
        try:
            from use_cases.file_analysis import _read_full
            raw = _read_full(_Path(file_path), max_chars=25000)
            if raw and not raw[:60].lower().startswith(("[binary", "[image", "error")):
                self._ctx["last_doc_full"] = raw
        except Exception:
            pass

    def _handle_file_summarize(self, user_input: str) -> Optional[str]:
        """Find a file from a natural-language description and summarize its contents."""
        from use_cases.file_analysis import summarize_file, find_similar_files
        import os
        from pathlib import Path

        filename, search_root = _parse_file_query(user_input)

        # --- Vague/no-extension reference: "summarize the proposal", "read that PDF" ---
        if not filename:
            # Extract keywords by stripping action words, prepositions, and known folder names
            import re as _re
            _ACTION = frozenset({
                "summarize", "read", "open", "show", "explain", "describe",
                "what", "what's", "whats", "tell", "me", "about", "is", "in",
                "the", "a", "an", "my", "that", "this", "it", "its", "please",
                "give", "get", "fetch", "look", "at", "for", "can", "you",
                "folder", "directory", "file", "document",
                "downloads", "documents", "desktop", "pictures", "videos", "music",
            })
            words = [w for w in _re.sub(r'[^\w\s]', '', user_input.lower()).split()
                     if w not in _ACTION and len(w) > 2]

            if not words:
                return None  # nothing to search for

            # Resolve folder from query
            folder = self._resolve_folder_from_text(user_input.lower(), user_input)
            home = os.path.expanduser("~")
            search_dirs = [folder] if folder else [
                os.path.join(home, "Downloads"),
                os.path.join(home, "Documents"),
                os.path.join(home, "Desktop"),
            ]

            _DOC_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx", ".pptx", ".csv"}
            matches = []
            for d in search_dirs:
                if not d or not os.path.isdir(d):
                    continue
                try:
                    for entry in os.listdir(d):
                        p = os.path.join(d, entry)
                        if not os.path.isfile(p):
                            continue
                        ext = Path(entry).suffix.lower()
                        if ext not in _DOC_EXTS:
                            continue
                        stem = Path(entry).stem.lower()
                        if any(kw in stem for kw in words):
                            matches.append(p)
                except OSError:
                    pass
                if matches:
                    break

            if not matches:
                return None  # let LLM handle it
            if len(matches) == 1:
                result = summarize_file(matches[0], llm_client=self.llm)
                if result is not None:
                    self._store_doc_context(matches[0], result)
                    return result
            # Multiple keyword matches — pick the best one (most keyword overlap)
            best = max(matches, key=lambda p: sum(
                kw in Path(p).stem.lower() for kw in words
            ))
            result = summarize_file(best, llm_client=self.llm)
            if result is not None:
                self._store_doc_context(best, result)
                return result
            return None

        # --- Case 1: absolute path was given ---
        if os.path.isabs(filename) or (len(filename) > 2 and filename[1] == ':'):
            result = summarize_file(filename, llm_client=self.llm)
            if result is not None:
                self._store_doc_context(filename, result)
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
                self._store_doc_context(candidate, result)
                return result

            # Recursive search inside search_root
            root_path = Path(search_root)
            if root_path.exists():
                try:
                    for p in root_path.rglob(filename):
                        r = summarize_file(str(p), llm_client=self.llm)
                        if r is not None:
                            self._store_doc_context(str(p), r)
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

        # --- Case 3: no location given — search last_dir context, then default locations ---
        home = os.path.expanduser("~")
        default_roots = [
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Pictures"),
            os.path.join(home, "Videos"),
            home,
        ]
        # Prioritise the last folder the user was working in
        ctx_dir = self._ctx.get("last_dir")
        if ctx_dir and ctx_dir not in default_roots:
            default_roots = [ctx_dir] + default_roots
        for root in default_roots:
            p = Path(root) / filename
            result = summarize_file(str(p), llm_client=self.llm)
            if result is not None:
                self._store_doc_context(str(p), result)
                return result
            # Shallow rglob (2 levels) to avoid scanning whole drive
            try:
                for found in Path(root).rglob(filename):
                    r = summarize_file(str(found), llm_client=self.llm)
                    if r is not None:
                        self._store_doc_context(str(found), r)
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

    def _handle_batch_delete(self, user_input: str) -> Optional[str]:
        """Delete all files from the previous content search result, with confirmation."""
        files = self._ctx.get("last_files", [])
        if not files:
            return (
                "No files in context to delete. "
                "First run a content search, e.g. "
                "'find files containing \"password\" in Documents'"
            )

        import os
        # Filter to only existing files
        existing = [f for f in files if os.path.isfile(f)]
        if not existing:
            return "The files from the previous search no longer exist."

        lines = [f"About to delete {len(existing)} file(s):"]
        for p in existing:
            lines.append(f"  • {os.path.basename(p)}  ({p})")
        lines.append("\nThis will create snapshots before deletion.")
        lines.append("Type 'yes' to confirm, or 'no' to cancel.")
        preview = "\n".join(lines)

        self.pending_confirmation = {
            "action": "batch_delete",
            "description": f"delete {len(existing)} file(s)",
            "params": {"paths": existing},
            "preview": preview,
        }
        return preview

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
            "  • Multi-document workflows — summarize/compare all documents in a folder,\n"
            "    extract key topics, build knowledge summaries, create combined reports\n"
            "  • Document creation — write results to TXT, Markdown, PDF, or DOCX files\n"
            "  • Content search — grep files, diff two files, extract emails/URLs/phone numbers\n"
            "  • Contextual navigation — 'what is in the energy doc in there?' uses folder context\n"
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

        # Store context for follow-up queries
        if search_root:
            self._ctx["last_dir"] = search_root
        self._ctx["last_files"] = results[:20]

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
        from pathlib import Path
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

        from use_cases.file_ops import smart_organize_folder
        result = smart_organize_folder(folder, llm_client=self.llm, preview_only=True)

        if "error" in result:
            return result["error"]

        groups = result.get("groups", {})
        total = result.get("total_files", 0)

        if total == 0:
            return f"No files to organize in {Path(folder).name}."

        lines = [f"Here's how I'll organize {Path(folder).name} ({total} file(s)):\n"]
        for group_name, files in groups.items():
            lines.append(f"  [{group_name}/]  — {len(files)} file(s)")
            for fname in files[:4]:
                lines.append(f"      • {fname}")
            if len(files) > 4:
                lines.append(f"      ... and {len(files) - 4} more")
        lines.append("\nShall I proceed? (yes/no)")

        self.pending_confirmation = {
            "action": "smart_organize",
            "params": {"folder": folder},
            "description": f"Organize folder: {Path(folder).name}",
        }
        return "\n".join(lines)

    def _handle_smart_organize(self, user_input: str) -> str:
        """Organize a folder by content similarity (LLM clusters docs; images by filename)."""
        import os
        from pathlib import Path

        folder = self._resolve_folder_from_text(user_input.lower(), user_input)
        if not folder:
            folder = self._ctx.get("last_dir")
        if not folder or not os.path.isdir(folder):
            return (
                "Please specify a folder, e.g. "
                "'organize my Invoices folder by content' or "
                "'group similar files in Downloads'"
            )

        from use_cases.file_ops import smart_organize_folder
        result = smart_organize_folder(folder, llm_client=self.llm, preview_only=True)

        if "error" in result:
            return result["error"]

        groups = result.get("groups", {})
        total = result.get("total_files", 0)

        if total == 0:
            return f"No files to organize in {Path(folder).name}."

        lines = [
            f"Smart organization preview for {Path(folder).name} ({total} file(s)):\n"
        ]
        for group_name, files in groups.items():
            lines.append(f"  [{group_name}/]  — {len(files)} file(s)")
            for fname in files[:4]:
                lines.append(f"      • {fname}")
            if len(files) > 4:
                lines.append(f"      ... and {len(files) - 4} more")
        lines.append("\nType 'yes' to proceed, or 'no' to cancel.")

        self.pending_confirmation = {
            "action": "smart_organize",
            "params": {"folder": folder},
            "description": f"Smart organize: {folder}",
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
        return f"Task added: {task['title']}{due_str}"

    def _handle_task_list(self, user_input: str) -> str:
        from use_cases.tasks import time_until_due
        lower = user_input.lower()
        show_all = any(w in lower for w in ("all", "completed", "done", "finished"))
        tasks = self._task_mgr.list_all() if show_all else self._task_mgr.list_pending()
        if not tasks:
            return "No tasks found." if show_all else "You have no pending tasks."
        lines = []
        for i, t in enumerate(tasks, 1):
            done = "[x]" if t["completed"] else "[ ]"
            pri = f" [{t['priority']}]" if t["priority"] != "normal" else ""
            due_str = f" — due: {time_until_due(t['due_date'])}" if t["due_date"] else ""
            lines.append(f"  #{i} {done}{pri} {t['title']}{due_str}")
        label = "All tasks" if show_all else f"Pending tasks ({self._task_mgr.count_pending()})"
        return f"{label}:\n" + "\n".join(lines)

    def _handle_task_complete(self, user_input: str) -> str:
        import re as _re
        m = _re.search(r'#?(\d+)', user_input)
        if m:
            n = int(m.group(1))
            pending = self._task_mgr.list_pending()
            if n < 1 or n > len(pending):
                return f"No task #{n}. You have {len(pending)} pending task(s)."
            task = self._task_mgr.complete(pending[n - 1]["id"])
            return f"Marked as done: {pending[n - 1]['title']}"
        # Title-based lookup
        title = _re.sub(
            r'^(?:complete|done|finish|mark|finished|close|done with|mark as done)\s+',
            '', user_input.strip(), flags=_re.IGNORECASE,
        )
        title = _re.sub(r'\s+tasks?\s*$', '', title, flags=_re.IGNORECASE).strip()
        if not title:
            return "Please specify a task number or name, e.g. 'complete task #1' or 'done review invoice task'"
        matches = self._task_mgr.search(title)
        pending = [t for t in matches if not t["completed"]]
        if not pending:
            return f"No pending task found matching '{title}'."
        if len(pending) == 1:
            self._task_mgr.complete(pending[0]["id"])
            return f"Marked as done: {pending[0]['title']}"
        all_pending = self._task_mgr.list_pending()
        lines = [f"Multiple tasks match '{title}'. Specify by number:"]
        for t in pending[:5]:
            pos = next((i + 1 for i, p in enumerate(all_pending) if p["id"] == t["id"]), t["id"])
            lines.append(f"  #{pos} {t['title']}")
        return "\n".join(lines)

    def _handle_task_remove(self, user_input: str) -> str:
        import re as _re
        m = _re.search(r'#?(\d+)', user_input)
        if m:
            n = int(m.group(1))
            pending = self._task_mgr.list_pending()
            if n < 1 or n > len(pending):
                return f"No task #{n}. You have {len(pending)} pending task(s)."
            removed = self._task_mgr.remove(pending[n - 1]["id"])
            return f"Removed: {pending[n - 1]['title']}" if removed else "Could not remove task."
        # Title-based lookup
        title = _re.sub(
            r'^(?:clear|remove|delete|erase|drop)\s+',
            '', user_input.strip(), flags=_re.IGNORECASE,
        )
        title = _re.sub(r'\s+tasks?\s*$', '', title, flags=_re.IGNORECASE).strip()
        if not title:
            return "Please specify a task number or name, e.g. 'remove task #1' or 'clear review invoice task'"
        matches = self._task_mgr.search(title)
        if not matches:
            return f"No task found matching '{title}'."
        if len(matches) == 1:
            removed = self._task_mgr.remove(matches[0]["id"])
            return f"Removed: {matches[0]['title']}" if removed else "Could not remove task."
        all_pending = self._task_mgr.list_pending()
        lines = [f"Multiple tasks match '{title}'. Specify by number:"]
        for t in matches[:5]:
            pos = next((i + 1 for i, p in enumerate(all_pending) if p["id"] == t["id"]), t["id"])
            lines.append(f"  #{pos} {t['title']}")
        return "\n".join(lines)
        return "\n".join(lines)

    def _handle_task_clear(self, user_input: str = "") -> str:
        lower = user_input.lower()
        if any(w in lower for w in ("all", "everything", "every task", "entire")):
            count = self._task_mgr.clear_all()
            return f"Cleared all {count} task(s)."
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

    def _handle_undo(self, user_input: str) -> str:
        """Show a preview of the last operation and ask for confirmation to undo it."""
        batch = self.snapshot_mgr.peek_last_batch()
        if not batch:
            return "Nothing to undo — no recent operations found."

        ref_op = batch[0].get("operation", "unknown")
        op_labels = {
            "smart_organize": "smart organize",
            "organize": "organize by type",
            "delete": "delete",
            "move": "move",
            "rename": "rename",
        }
        op_label = op_labels.get(ref_op, ref_op)

        from pathlib import Path as _P
        lines = [
            f"This will undo the last '{op_label}' ({len(batch)} file(s)):",
        ]
        for s in batch[:6]:
            parent = _P(s["original_path"]).parent.name
            name = _P(s["original_path"]).name
            lines.append(f"  • {name}  →  {parent}/")
        if len(batch) > 6:
            lines.append(f"  ... and {len(batch) - 6} more")
        lines.append("\nType 'yes' to restore everything, or 'no' to cancel.")

        self.pending_confirmation = {
            "action": "undo_last",
            "description": f"undo {op_label}",
            "params": {},
        }
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

    def _handle_content_search(self, user_input: str) -> str:
        """Search for a text pattern across all readable files in a folder."""
        from use_cases.file_analysis import grep_folder, _read_full
        import re as _re, os
        from pathlib import Path

        lower = user_input.lower()

        # --- Extract the search pattern ---
        # Try quoted string first: "Alexandre Inc", 'password'
        quoted_m = _re.search(r'["\']([^"\']+)["\']', user_input)
        if quoted_m:
            pattern = quoted_m.group(1).strip()
        else:
            # Try: "mentioning X", "containing X", "for X", "about X"
            kw_m = _re.search(
                r'\b(?:mentioning|containing|mention|contain|for|about|'
                r'with|including|that include|that say|that have|with text|'
                r'with content|the word|the phrase)\s+(.+?)(?:\s+in\s+|\s+from\s+|$)',
                lower, _re.IGNORECASE,
            )
            if kw_m:
                pattern = kw_m.group(1).strip().strip('"\'')
            else:
                # Last resort: text at the end of the sentence after last keyword
                parts = _re.split(
                    r'\b(?:mentioning|containing|mention|contain)\b', lower, maxsplit=1
                )
                if len(parts) > 1:
                    pattern = parts[1].strip().split(" in ")[0].strip()
                else:
                    return (
                        "Please specify what to search for. "
                        "Example: find files containing \"password\" in Documents"
                    )

        if not pattern:
            return (
                "Please specify what to search for. "
                "Example: find documents mentioning \"Alexandre Inc\" in Invoices"
            )

        # --- Resolve the folder ---
        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            # Default to current context folder or Documents
            folder = self._ctx.get("last_dir") or os.path.join(os.path.expanduser("~"), "Documents")

        if not os.path.isdir(folder):
            return f"Folder not found: {folder}"

        # --- Run the search ---
        result = grep_folder(folder, pattern)

        if "error" in result:
            return f"Search error: {result['error']}"

        searched = result.get("searched", 0)
        matched = result.get("matched", 0)
        matches = result.get("matches", [])

        if matched == 0:
            return (
                f"No files found containing \"{pattern}\" in {Path(folder).name}. "
                f"({searched} file(s) searched)"
            )

        # --- Update conversation context ---
        matched_paths = [m["path"] for m in matches]
        self._ctx["last_files"] = matched_paths
        self._ctx["last_dir"] = folder

        # If exactly one match, pre-load its full text for follow-up commands
        if len(matches) == 1:
            try:
                doc_text = _read_full(Path(matches[0]["path"]), max_chars=15000)
                if doc_text and not doc_text.startswith("[Binary") and not doc_text.startswith("[Image"):
                    self._ctx["last_doc_text"] = doc_text
                    self._ctx["last_doc_full"] = doc_text
            except Exception:
                pass

        # --- Format output ---
        lines = [
            f"Found {matched} file(s) containing \"{pattern}\" "
            f"in {Path(folder).name} ({searched} searched):\n"
        ]
        for m in matches:
            lines.append(f"  {m['name']}")
            for snip in m.get("snippets", [])[:2]:
                lines.append(f"    …{snip}…")
        lines.append("")
        if len(matches) == 1:
            lines.append("Tip: say 'summarize that document' to read it in detail.")
        else:
            lines.append("Tip: say 'delete those files' to remove them all, or 'summarize (filename)' for a specific one.")
        return "\n".join(lines)

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
            if pt in lower or (pt == "ipv4" and "ip" in lower):
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

    # ── Multi-document / synthesis handlers ──────────────────────

    def _resolve_folder_from_text(self, text_lower: str,
                                   original: str = "") -> Optional[str]:
        """Resolve a folder path from natural language, with context fallback.

        Resolution priority:
          1. Explicit subfolder + base  ("X folder in Downloads", with or without my/the)
          2. Implicit subfolder + base  ("X in Downloads" — no 'folder' keyword)
          3. Context shortcut           (last_dir basename re-mentioned)
          4. Fuzzy directory scan       (capitalized names scanned against actual dirs)
          5. Standard base folder       (just "Downloads", "Documents", etc.)
          6. Absolute path in query
          7. Last-used directory
        """
        import os
        import re as _re
        from pathlib import Path as _P

        home = os.path.expanduser("~")
        _KNOWN = [
            ("downloads",  os.path.join(home, "Downloads")),
            ("documents",  os.path.join(home, "Documents")),
            ("desktop",    os.path.join(home, "Desktop")),
            ("pictures",   os.path.join(home, "Pictures")),
            ("videos",     os.path.join(home, "Videos")),
            ("music",      os.path.join(home, "Music")),
        ]
        _KNOWN_MAP = dict(_KNOWN)
        _bases = [path for _, path in _KNOWN]

        # Common words that are NOT folder names even if they appear before "in <base>"
        _STOP = frozenset({
            "files", "file", "what", "list", "show", "all", "the", "my", "some",
            "any", "stuff", "things", "items", "content", "contents", "everything",
            "folder", "directory", "look", "see", "get", "find", "search",
        })

        def _scan(name: str, base_path: str) -> Optional[str]:
            """Case-insensitive subfolder lookup inside base_path."""
            try:
                for entry in os.listdir(base_path):
                    if entry.lower() == name.lower() and os.path.isdir(
                        os.path.join(base_path, entry)
                    ):
                        return os.path.join(base_path, entry)
            except OSError:
                pass
            return None

        def _scan_all(name: str) -> Optional[str]:
            for bp in _bases:
                found = _scan(name, bp)
                if found:
                    return found
            return None

        _BASE_PAT = r"(downloads|documents|desktop|pictures|videos|music)"

        # 1a. "the/my X folder [, words] in [the/my] Y"  — hyphens OK, no spaces in name
        #     (spaces excluded to avoid matching across "the files in the demo-files folder")
        #     Allows 0-2 extra words between "folder" and "in" (e.g. "folder, present in")
        m = _re.search(
            r'\b(?:my|the)\s+([\w][\w\-]*)\s+folder\s*,?\s*(?:\w+\s+){0,2}?'
            r'(?:in|inside)\s+(?:the\s+|my\s+)?' + _BASE_PAT,
            text_lower,
        )
        if m:
            sub, bp = m.group(1).strip(), _KNOWN_MAP.get(m.group(2))
            if bp:
                return _scan(sub, bp) or os.path.join(bp, sub)

        # 1b. Multi-word title-case: "Research Papers folder in Downloads"
        #     Uses IGNORECASE for the base keyword but validates title-case on the name.
        m = _re.search(
            r'\b([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+)+)\s+folder\s*,?\s*'
            r'(?:in|inside)\s+(?:the\s+|my\s+)?'
            r'(downloads|documents|desktop|pictures|videos|music)',
            original,
            _re.IGNORECASE,
        )
        if m:
            sub = m.group(1).strip()
            if all(w[0].isupper() for w in sub.split()):  # every word must be capitalised
                bp = _KNOWN_MAP.get(m.group(2).lower())
                if bp:
                    return _scan(sub, bp) or os.path.join(bp, sub)

        # 1c. "X folder [, words] in Y"  — no my/the required, single/hyphenated name only
        #     Disk-only: only returns if the subfolder actually exists (avoids false positives)
        #     Allows 0-2 extra words between "folder" and "in" (e.g. "folder, present in")
        m = _re.search(
            r'\b([\w][\w\-]+)\s+folder\s*,?\s*(?:\w+\s+){0,2}?'
            r'(?:in|inside)\s+(?:the\s+|my\s+)?' + _BASE_PAT,
            text_lower,
        )
        if m:
            sub, bp = m.group(1).strip(), _KNOWN_MAP.get(m.group(2))
            if sub not in _STOP and bp:
                found = _scan(sub, bp)
                if found:
                    return found

        # 2. "X in Y"  — no 'folder' keyword; sub must exist on disk (avoids false positives)
        m = _re.search(
            r'\b([\w][\w \-]+?)\s+(?:in|inside)\s+(?:the\s+|my\s+)?' + _BASE_PAT,
            text_lower,
        )
        if m:
            sub, bp = m.group(1).strip(), _KNOWN_MAP.get(m.group(2))
            if sub not in _STOP and bp and len(sub) > 2:
                found = _scan(sub, bp)
                if found:
                    return found

        # 3. Context shortcut: last_dir basename re-mentioned in query
        last = self._ctx.get("last_dir")
        if last:
            basename = _P(last).name.lower()
            if basename and len(basename) > 3 and basename in text_lower:
                return last

        # 4. Fuzzy directory scan — find any word/phrase in the query that
        #    matches an actual subdirectory name across all known base paths.
        #    Handles: quoted names, hyphenated names, multi-word CamelCase phrases.
        candidates: list[str] = []

        # Quoted strings have highest confidence
        candidates += [g1 or g2 for g1, g2 in _re.findall(r'"([^"]+)"|\'([^\']+)\'', original)]

        # Hyphenated tokens like "Demo-files", "research-data"
        candidates += _re.findall(r'\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b', original)

        # Multi-word title-case phrases: "Research Papers", "My Projects"
        candidates += _re.findall(
            r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', original
        )

        # Single CamelCase / Title-case word: "Downloads", "Projects"
        candidates += _re.findall(r'\b([A-Z][A-Za-z0-9]{2,})\b', original)

        seen: set[str] = set()
        for name in candidates:
            name = name.strip()
            if not name or name.lower() in seen or name.lower() in _STOP:
                continue
            seen.add(name.lower())
            found = _scan_all(name)
            if found:
                return found

        # 5. Standard base folder match
        for kw, path in _KNOWN:
            if not _re.search(rf'\b{kw}\b', text_lower):
                continue
            if kw == "documents" and _re.search(
                r'\b(?:all\s+(?:the\s+)?|the\s+)documents\s+in\b', text_lower
            ):
                continue
            return path

        # 6. Absolute path in original text
        abs_m = _re.search(r'[A-Za-z]:\\[\w\\/ \-\.]+', original)
        if abs_m:
            candidate = abs_m.group().rstrip()
            if _P(candidate).exists():
                return candidate

        # 7. Context fallback
        return self._ctx.get("last_dir")

    def _format_folder_docs(self, result: dict,
                             folder_name: str) -> tuple[str, str]:
        """Build combined document text and a header from batch_read_folder result."""
        files = result.get("files", [])
        n = len(files)
        if n == 0:
            return "", f"No readable documents found in {folder_name}."
        names = ", ".join(f["name"] for f in files[:5])
        if n > 5:
            names += f", and {n - 5} more"
        header = f"Read {n} document(s) from {folder_name}: {names}"
        combined = "\n\n".join(
            f"=== {f['name']} ===\n{f['text']}" for f in files
        )
        return combined, header

    # ── Clarification engine ──────────────────────────────────────

    def _advance_clarification(self, answer: str) -> str:
        """Store one clarification answer; return next question or execute."""
        cl = self.pending_clarification
        key, _ = cl["questions"][cl["idx"]]
        cl["params"][key] = answer.strip()
        cl["idx"] += 1

        if cl["idx"] < len(cl["questions"]):
            return cl["questions"][cl["idx"]][1]

        intent = cl["intent"]
        params = dict(cl["params"])
        self.pending_clarification = None

        if intent == "create_doc":
            return self._execute_create_doc(params)
        return f"[Unknown clarification intent: {intent}]"

    def _offline_doc_template(self, topic: str, reference_text: str = "") -> str:
        """Generate a basic document template without the LLM.

        Parses names, amounts, and key actions from the topic text and produces
        a short professional email or a plain structured document as appropriate.
        """
        import re as _re
        lower = topic.lower()

        is_email = any(w in lower for w in ("email", "e-mail", "follow-up", "follow up", "letter", "memo"))

        # Extract recipient name: "to <Name>" where Name starts with a capital
        name_m = _re.search(r'\bto\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', topic)
        recipient = name_m.group(1) if name_m else ""

        # Extract dollar amounts from topic + snippet of reference
        combined = topic + " " + reference_text[:600]
        amounts = _re.findall(r'\$[\d,]+(?:\.\d{2})?(?:/\w+)?', combined)
        amounts = list(dict.fromkeys(amounts))  # deduplicate, preserve order

        # Strip the leading verb-phrase to get the body of the request
        body = _re.sub(
            r'^(?:please\s+)?(?:can you\s+)?(?:write|draft|create|compose|send)\s+(?:a\s+|an\s+)?'
            r'(?:short\s+|brief\s+|quick\s+)?(?:follow-?up\s+)?(?:email|letter|memo|message|document|report)\s+',
            '', topic, flags=_re.IGNORECASE,
        ).strip()
        # Drop "to <Name>" now that recipient is captured separately
        body = _re.sub(r'^to\s+[A-Z][a-z]+\s+', '', body).strip()
        # Drop save-path directive at the end
        body = _re.sub(r'[.,]?\s+(?:save|store)\s+it\s+to\b.*', '', body, flags=_re.IGNORECASE).strip()

        # Split composite actions on "and" for bullet points
        action_parts = [p.strip() for p in _re.split(r'\s+and\s+', body, flags=_re.IGNORECASE) if p.strip()]

        if is_email:
            subject_amount = amounts[0] if amounts else ""
            subject = f"Follow-up{' — ' + subject_amount if subject_amount else ''}"
            greeting = f"Dear {recipient}," if recipient else "Hello,"

            if len(action_parts) > 1:
                bullet_lines = ["I wanted to reach out to:"]
                for part in action_parts:
                    bullet_lines.append(f"  - {part.capitalize()}")
                body_text = "\n".join(bullet_lines)
            else:
                body_text = f"I wanted to {body}."

            return "\n".join([
                f"Subject: {subject}",
                "",
                greeting,
                "",
                body_text,
                "",
                "Please let me know if you have any questions.",
                "",
                "Best regards",
            ])

        # Generic document
        return f"# Document\n\n{body or topic}"

    def _execute_create_doc(self, params: dict) -> str:
        """Generate document content (if needed) and write the file."""
        from use_cases.doc_gen import write_document
        import os

        # Resolve save directory
        raw_dir = params.get("save_dir", "desktop").strip()
        home = os.path.expanduser("~")
        dir_map = {
            "desktop":   os.path.join(home, "Desktop"),
            "downloads": os.path.join(home, "Downloads"),
            "documents": os.path.join(home, "Documents"),
        }
        save_dir = dir_map.get(raw_dir.lower())
        if save_dir is None:
            save_dir = raw_dir if os.path.isabs(raw_dir) else os.path.join(home, "Desktop")

        filename = params.get("filename", "document.md")
        out_path = os.path.join(save_dir, filename)

        content = params.get("content", "")
        if not content:
            topic = params.get("topic") or params.get("user_request", "")
            label = params.get("label", "structured")
            reference_text = params.get("reference_text", "")
            if topic and self.llm:
                try:
                    if reference_text:
                        prompt = (
                            f"Write a {label} document. The user's request: {topic}\n\n"
                            "Use the following reference material to inform the content. "
                            "Extract relevant details (names, amounts, dates, items) and "
                            "incorporate them naturally into the document.\n\n"
                            f"--- Reference Material ---\n{reference_text[:3500]}\n--- End Reference ---\n\n"
                            "Write the document now, using clear headings and professional tone."
                        )
                    else:
                        prompt = (
                            f"Write a {label} document about the following topic or request. "
                            "Use clear headings (# Heading) and well-organised paragraphs.\n\n"
                            + topic[:3000]
                        )
                    content = self.llm.chat([
                        {"role": "system",
                         "content": "You are a professional writer. Write well-structured documents."},
                        {"role": "user", "content": prompt},
                    ])
                except Exception as e:
                    logger.warning("LLM content generation failed: %s", e)
            # Offline fallback: generate a basic template when LLM is unavailable or failed
            if not content and topic:
                content = self._offline_doc_template(topic, reference_text)
            if not content:
                return "No content to write — please provide a topic or run a folder summary first."

        result = write_document(out_path, content)
        self._ctx["last_output_path"] = out_path
        return result

    # ── Directory listing ─────────────────────────────────────────

    def _handle_list_dir(self, user_input: str) -> str:
        """List files in a folder and store the folder in context."""
        from use_cases.file_ops import list_directory
        import os
        lower = user_input.lower()
        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            return "Please specify a folder, e.g. 'list files in Downloads'"

        self._ctx["last_dir"] = folder
        items = list_directory(folder)
        if items and "error" in items[0]:
            return items[0]["error"]

        folder_name = os.path.basename(folder) or folder
        lines = [f"{folder_name}  ({len(items)} item(s)):"]
        for item in items:
            prefix = "  [dir] " if item["type"] == "directory" else "       "
            lines.append(f"{prefix}{item['name']:<40} {item.get('size_human',''):>8}  {item['modified']}")
        return "\n".join(lines)

    # ── Folder explanation ────────────────────────────────────────

    def _handle_folder_explain(self, user_input: str) -> str:
        """Return a structured overview of what a folder contains."""
        from use_cases.file_analysis import folder_explain
        import os
        lower = user_input.lower()
        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            return "Please specify a folder, e.g. 'explain what my Downloads folder contains'"

        self._ctx["last_dir"] = folder
        info = folder_explain(folder)
        if "error" in info:
            return info["error"]

        folder_name = os.path.basename(folder) or folder
        lines = [
            f"{folder_name}  ({info['total_files']} file(s), {info['total_size']} total)"
        ]
        if info["by_type"]:
            lines.append("\nFile types:")
            for cat, count in sorted(info["by_type"].items(), key=lambda x: -x[1]):
                lines.append(f"  {count:>3}  {cat}")
        if info["largest_files"]:
            lines.append("\nLargest files:")
            for f in info["largest_files"]:
                lines.append(f"  {f['size']:>8}  {f['name']}")
        return "\n".join(lines)

    # ── Folder-level document summarization ──────────────────────

    def _handle_folder_docs_request(self, user_input: str) -> str:
        """Read all documents in a folder and summarize them with the LLM.

        Uses a multi-pass strategy for large documents:
        1. Per-document pass: extract up to 12,000 chars per file, ask BitNet
           for a ~300-word focused summary of each (max_tokens=500).
        2. Synthesis pass: feed all per-doc summaries back to BitNet for a
           comprehensive 1000+ word overview (max_tokens=1500).
        This keeps each LLM call safely within BitNet's 4096-token context.
        """
        from use_cases.file_analysis import batch_read_folder
        import os
        lower = user_input.lower()

        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            return (
                "Please specify a folder, e.g. "
                "'summarize all documents in Downloads'"
            )

        # Optional extension filter from the request
        ext_filter = None
        for label, exts in [
            ("pdf", ["pdf"]), ("docx", ["docx"]), ("txt", ["txt"]),
            ("markdown", ["md"]), ("csv", ["csv"]),
            ("excel", ["xlsx"]), ("spreadsheet", ["xlsx", "csv"]),
        ]:
            if label in lower:
                ext_filter = exts
                break

        # Use large per-file budget so research papers get meaningful extraction
        result = batch_read_folder(folder, ext_filter,
                                   max_chars_per_file=15000,
                                   max_total_chars=100000)
        if "error" in result:
            return result["error"]

        files = result.get("files", [])
        if not files:
            return f"No readable documents found in {os.path.basename(folder)}."

        folder_name = os.path.basename(folder) or folder
        self._ctx["last_dir"] = folder
        self._ctx["last_files"] = [f["name"] for f in files]

        names = ", ".join(f["name"] for f in files[:5])
        if len(files) > 5:
            names += f", and {len(files) - 5} more"
        header = f"Read {len(files)} document(s) from {folder_name}: {names}"

        # No LLM — return text previews
        if not self.llm:
            preview = "\n\n".join(
                f"{f['name']}:\n" + "\n".join(f["text"].split("\n")[:8])
                for f in files[:3]
            )
            return f"{header}\n\n{preview}"

        # ── Pass 1: per-document summaries ─────────────────────────
        # Each call: ~12,000 chars text (~3000 tokens) + 150 token instruction
        # + 500 token output  ≈ 3650 total — safely within BitNet's 4096 ctx.
        per_doc_summaries = []
        for f in files:
            doc_text = f["text"][:12000]
            doc_name = f["name"]
            try:
                per_doc_prompt = (
                    f"Document: {doc_name}\n\n"
                    f"{doc_text}\n\n"
                    "Write a thorough summary (~300 words) covering: "
                    "main topic/research question, methodology or approach, "
                    "key findings or content, and conclusions."
                )
                doc_summary = self.llm.chat(
                    [
                        {"role": "system",
                         "content": (
                             "You are a scientific analyst. "
                             "Summarise the given document accurately and thoroughly."
                         )},
                        {"role": "user", "content": per_doc_prompt},
                    ],
                    max_tokens=500,
                )
                per_doc_summaries.append(
                    f"=== {doc_name} ===\n{doc_summary.strip()}"
                )
            except Exception as e:
                per_doc_summaries.append(
                    f"=== {doc_name} ===\n[Could not summarise: {e}]"
                )

        all_summaries_text = "\n\n".join(per_doc_summaries)
        self._ctx["last_doc_text"] = all_summaries_text

        # ── Pass 2: synthesis ───────────────────────────────────────
        # Input: per-doc summaries capped at 8,000 chars (~2000 tokens)
        # + ~200 token instruction = ~2200 input.
        # max_tokens=1500 → ~1100 words — fits within BitNet's 4096 ctx.
        try:
            synth_prompt = (
                f"Below are individual summaries of {len(files)} document(s) "
                f"from the '{folder_name}' collection.\n\n"
                f"{all_summaries_text[:8000]}\n\n"
                "Write a comprehensive, well-structured overview of at least "
                "1000 words. Cover the main themes, methodologies, findings, "
                "and conclusions across all documents. Identify common threads, "
                "contrasts, and the overall significance of this collection."
            )
            synthesis = self.llm.chat(
                [
                    {"role": "system",
                     "content": (
                         "You are a scientific analyst writing thorough research "
                         "overviews. Be detailed and write at least 1000 words."
                     )},
                    {"role": "user", "content": synth_prompt},
                ],
                max_tokens=1500,
            )
            self._ctx["last_summary"] = synthesis.strip()
            return f"{header}\n\n{synthesis.strip()}"
        except Exception as e:
            # Synthesis failed — return individual summaries as fallback
            self._ctx["last_summary"] = all_summaries_text
            return (
                f"{header}\n\n"
                f"[Synthesis unavailable: {e}]\n\n"
                f"Individual summaries:\n\n{all_summaries_text}"
            )

    # ── Multi-document comparison ─────────────────────────────────

    def _handle_multi_doc_compare(self, user_input: str) -> str:
        """Compare documents in a folder using the LLM."""
        from use_cases.file_analysis import batch_read_folder
        import os
        lower = user_input.lower()

        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            return "Please specify a folder to compare documents in."

        result = batch_read_folder(folder)
        if "error" in result:
            return result["error"]

        files = result.get("files", [])
        folder_name = os.path.basename(folder) or folder
        if len(files) < 2:
            return (
                f"Need at least 2 documents to compare. "
                f"Found {len(files)} readable file(s) in {folder_name}."
            )

        combined, header = self._format_folder_docs(result, folder_name)
        self._ctx.update({
            "last_dir": folder,
            "last_files": [f["name"] for f in files],
            "last_doc_text": combined,
        })

        if not self.llm:
            return f"{header}\n\nLLM unavailable — cannot run comparison."

        try:
            prompt = (
                f"Compare the following {len(files)} documents from {folder_name}. "
                "For each document briefly describe its content and focus. "
                "Then highlight similarities and key differences across all of them.\n\n"
                + combined[:9000]
            )
            comparison = self.llm.chat([
                {"role": "system",
                 "content": "You are a document analyst. Compare documents objectively and clearly."},
                {"role": "user", "content": prompt},
            ])
            self._ctx["last_summary"] = comparison.strip()
            return f"{header}\n\n{comparison.strip()}"
        except Exception as e:
            return f"{header}\n\n[LLM comparison failed: {e}]"

    # ── Folder topic / theme extraction ──────────────────────────

    def _handle_folder_topics(self, user_input: str) -> str:
        """Identify key topics and themes across folder documents."""
        from use_cases.file_analysis import batch_read_folder
        import os
        lower = user_input.lower()

        folder = self._resolve_folder_from_text(lower, user_input)
        if not folder:
            return "Please specify a folder, e.g. 'key topics across my Documents folder'"

        result = batch_read_folder(folder)
        if "error" in result:
            return result["error"]

        files = result.get("files", [])
        if not files:
            return f"No readable documents found in {os.path.basename(folder)}."

        folder_name = os.path.basename(folder) or folder
        combined, header = self._format_folder_docs(result, folder_name)
        self._ctx.update({"last_dir": folder, "last_doc_text": combined})

        if not self.llm:
            return f"{header}\n\nLLM unavailable — cannot extract topics."

        try:
            prompt = (
                "Identify the key topics, themes, and subjects that appear across "
                "these documents. List the most important topics and note which "
                "documents cover each one.\n\n" + combined[:9000]
            )
            topics = self.llm.chat([
                {"role": "system",
                 "content": "You are a document analyst. Identify key topics and themes clearly."},
                {"role": "user", "content": prompt},
            ])
            return f"{header}\n\n{topics.strip()}"
        except Exception as e:
            return f"{header}\n\n[LLM topic extraction failed: {e}]"

    # ── Document creation with clarification ─────────────────────

    def _handle_create_doc(self, user_input: str) -> str:
        """Create a document, asking for filename/location if not specified."""
        import re as _re
        lower = user_input.lower()

        params: dict = {"user_request": user_input}

        # Detect reference context triggers — user wants a doc based on previously seen content
        _REFERENCE_TRIGGERS = (
            "based on", "referencing", "reference", "acknowledging",
            "responding to", "response to", "reply to", "in response to",
            "about that", "regarding that", "from that", "using that",
            "for that invoice", "for that document", "for that file",
            "acknowledging receipt", "acknowledging their",
            "following up on", "follow up on",
            "confirming the", "to confirm",
        )
        has_reference_trigger = any(t in lower for t in _REFERENCE_TRIGGERS)

        # Prefer full raw content for richer context; fall back to summary/text
        _ctx_ref = self._ctx.get("last_doc_full") or self._ctx.get("last_doc_text", "")

        if has_reference_trigger and _ctx_ref:
            # Use the previously loaded document as reference material
            params["topic"] = user_input
            params["reference_text"] = _ctx_ref[:4000]
        elif self._ctx.get("last_summary"):
            params["content"] = self._ctx["last_summary"]
        elif _ctx_ref:
            params["topic"] = user_input
            params["reference_text"] = _ctx_ref[:3000]
        else:
            params["topic"] = user_input

        # Style / length label
        if "short" in lower or "brief" in lower or "quick" in lower:
            params["label"] = "short and concise"
        elif "full" in lower or "detailed" in lower or "comprehensive" in lower:
            params["label"] = "detailed and comprehensive"
        elif "executive" in lower:
            params["label"] = "executive"
        elif "study guide" in lower or "knowledge" in lower:
            params["label"] = "study guide with key concepts"
        elif "comparison" in lower or "compare" in lower:
            params["label"] = "comparison"
        else:
            params["label"] = "structured"

        # Format preference
        for fmt in ("pdf", "docx", "markdown", "md", "txt"):
            if fmt in lower:
                params["format"] = "md" if fmt == "markdown" else fmt
                break

        # Save directory from request
        import os
        home = os.path.expanduser("~")
        for kw, path in [
            ("downloads",  os.path.join(home, "Downloads")),
            ("documents",  os.path.join(home, "Documents")),
            ("desktop",    os.path.join(home, "Desktop")),
        ]:
            if kw in lower:
                params["save_dir"] = path
                break

        # Explicit filename in request (word.ext pattern)
        fname_m = _re.search(_ext_pattern(), user_input, _re.IGNORECASE)
        if fname_m:
            params["filename"] = fname_m.group().strip()

        # Build question queue for missing required info
        questions = []
        if "filename" not in params:
            default_ext = params.get("format", "md")
            questions.append(
                ("filename",
                 f"What should the file be named? "
                 f"(e.g. summary.{default_ext}, report.md, notes.pdf)")
            )
        if "save_dir" not in params:
            questions.append(
                ("save_dir",
                 "Where should I save it? "
                 "(Desktop, Downloads, Documents, or a full path)")
            )

        if not questions:
            return self._execute_create_doc(params)

        self.pending_clarification = {
            "intent": "create_doc",
            "params": params,
            "questions": questions,
            "idx": 0,
        }
        return questions[0][1]

    # ── Document QA against loaded context ────────────────────────

    def _handle_doc_qa(self, user_input: str) -> Optional[str]:
        """Answer a question using the currently loaded document context."""
        # Prefer full raw content for richer answers; fall back to stored summary
        doc_full = self._ctx.get("last_doc_full", "")
        doc_text = self._ctx.get("last_doc_text") or self._ctx.get("last_summary", "")
        if not doc_full and not doc_text:
            return None  # no document context — let LLM handle it

        # Use raw content for LLM (more material) but cap to keep prompt in context window
        llm_content = (doc_full or doc_text)[:8000]

        if self.llm:
            try:
                prompt = (
                    f"User question: {user_input}\n\n"
                    "Based only on the following document content, answer the question:\n\n"
                    + llm_content
                )
                answer = self.llm.chat([
                    {"role": "system",
                     "content": "You are a document analyst. Answer questions based only on the provided document content."},
                    {"role": "user", "content": prompt},
                ])
                return answer.strip()
            except Exception:
                pass  # fall through to text search

        # LLM unavailable — find relevant sentences by keyword overlap
        import re as _re
        _STOP = frozenset({
            "what", "exactly", "do", "the", "a", "an", "is", "are", "in", "of",
            "and", "or", "how", "where", "when", "why", "which", "that", "this",
            "it", "was", "say", "says", "tell", "me", "about", "can", "you",
            "does", "did", "have", "has", "any", "please", "give",
        })
        words = [w.lower() for w in _re.sub(r'[^\w\s]', '', user_input).split()
                 if w.lower() not in _STOP and len(w) > 2]
        if not words:
            return None

        # Search in full raw content when available for broader coverage
        search_text = doc_full or doc_text

        # Split into sentences for fine-grained matching
        try:
            from use_cases.file_analysis import _split_sentences
            sentences = _split_sentences(search_text.replace('\n', ' '))
        except Exception:
            sentences = [l.strip() for l in search_text.splitlines()
                         if l.strip() and len(l.split()) >= 4]
        if not sentences:
            return None

        scored = [(sum(1 for w in words if w in s.lower()), s) for s in sentences]
        scored.sort(key=lambda x: -x[0])
        matches = [s for score, s in scored[:8] if score > 0]
        if not matches:
            return None

        return "Based on the document:\n\n" + "\n".join(matches[:5])

    # ── Main Processing ──────────────────────────────────────────

    def _process(self, user_input: str) -> str:
        """Internal processing logic."""
        # Clarification flow takes priority (collecting answers to queued questions)
        if self.pending_clarification is not None:
            cl_result = self._advance_clarification(user_input)
            self._store_turn(user_input, cl_result)
            return cl_result

        # Check pending confirmation next
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
