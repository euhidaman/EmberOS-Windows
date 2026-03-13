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


_CONFIRM_YES = {"yes", "y", "proceed", "ok", "sure", "do it", "go ahead", "yep", "yeah"}
_CONFIRM_NO = {"no", "n", "cancel", "stop", "nope", "don't", "abort"}


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
            "If a tool is needed, reply with JSON only.\n"
            '{"tool":"tool_name","params":{"arg":"value"}}\n'
            "Multiple tools: JSON array only.\n"
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
        lower = user_input.lower()

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

        return None

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
