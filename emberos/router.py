"""LLM-based intent router for EmberOS.

Each tool has a one-line description. The LLM reads the manifest and the
user's query, then returns only a tool name (or two names joined with ->
for a chained operation).  The agent maps the name(s) to handler methods.
"""

# ---------------------------------------------------------------------------
# Tool manifest — one line per tool, name: description
# Keep descriptions short so the prompt stays well within the 4096-token
# context window of the local BitNet model.
# ---------------------------------------------------------------------------
TOOL_MANIFEST = """\
list_dir: List or browse the contents of a folder or directory
file_summarize: Read and summarize a single file (PDF, DOCX, TXT, etc.)
folder_summarize: Summarize all documents inside a folder
folder_explain: Describe what a folder contains (file types, counts, sizes)
grep_folder: Search for a word, phrase, or pattern inside all files in a folder
grep_file: Search for a pattern inside one specific named file
batch_delete: Delete all files that were returned by a previous search
file_delete: Delete one specific named file
create_doc: Write and save a new document (TXT, Markdown, PDF, DOCX)
multi_doc_compare: Compare or contrast documents inside a folder
folder_topics: List the main topics covered by documents in a folder
doc_qa: Answer a question about a document that was already loaded
list_tasks: Show the current task list
add_task: Add a new item to the task list
complete_task: Mark a task as done or finished
remove_task: Delete a specific task from the list
clear_tasks: Remove all tasks from the list
screenshot: Take a screenshot of the screen
volume_up: Increase the system audio volume
volume_down: Decrease the system audio volume
mute: Toggle audio mute on or off
dark_mode: Enable or disable dark mode
brightness_up: Make the screen brighter
brightness_down: Make the screen dimmer
battery: Show the battery level and charge status
lock: Lock the Windows screen
sleep: Put the computer to sleep
shutdown: Shut down the computer
restart: Restart the computer
window_list: List all open application windows
window_minimize: Minimize all windows
window_focus: Bring a specific application window to the foreground
compress: Compress files or a folder into a ZIP archive
extract: Extract files from a ZIP or other archive
find_large_files: Find the largest files on disk
find_old_files: Find files that have not been accessed recently
find_duplicates: Find duplicate files
undo: Undo or revert the last file operation (organize, move, rename, delete)
smart_organize: Organize a folder by grouping similar documents together and images by filename patterns
diff_files: Show the line-by-line differences between two files
extract_patterns: Extract emails, URLs, phone numbers, or dates from a file
web_search: Search the internet for information
note_save: Save a note or reminder
note_query: Search or recall a previously saved note\
"""

_SYSTEM = (
    "You are an intent classifier for a desktop AI assistant. "
    "Given the tool list and a user query, output ONLY the tool name. "
    "If two tools are needed in order, output both separated by ->. "
    "No explanation. No extra words. Just the tool name(s)."
)

_PROMPT = """\
Tools:
{manifest}

Examples:
"show me my tasks" -> list_tasks
"find files with invoice in Documents then delete them" -> grep_folder -> batch_delete
"summarize all PDFs in Downloads" -> folder_summarize
"take a screenshot" -> screenshot
"what is my battery level" -> battery
"delete report.pdf" -> file_delete

Query: "{query}"
Answer:\
"""


# Set of valid tool names — built once at import time from the manifest
_VALID_TOOLS: frozenset = frozenset(
    line.split(":")[0].strip()
    for line in TOOL_MANIFEST.strip().splitlines()
    if ":" in line
)


def route(query: str, llm_client) -> dict:
    """Route a user query to a tool name using the LLM.

    Returns:
        {
          "tool":  str | None,          # primary tool name
          "chain": list[str] | None,    # [tool1, tool2] when chaining, else None
        }
    Returns {"tool": None, "chain": None} on any failure.
    """
    import re

    prompt = _PROMPT.format(manifest=TOOL_MANIFEST, query=query)
    try:
        raw = llm_client.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=24,
        ).strip()
    except Exception:
        return {"tool": None, "chain": None}

    # Normalise: lowercase, collapse whitespace, strip punctuation at edges
    cleaned = raw.lower().strip(" .,;:\n")

    # Keep only first line (model sometimes emits follow-up lines)
    first_line = cleaned.splitlines()[0] if cleaned else ""

    # Replace spaces within a tool name attempt (e.g. "list tasks" -> "list_tasks")
    # but keep "->" separators intact
    parts_raw = [p.strip() for p in first_line.split("->")]
    parts = [re.sub(r"\s+", "_", re.sub(r"[^\w\s]", "", p)) for p in parts_raw if p.strip()]

    if not parts:
        return {"tool": None, "chain": None}

    # Validate against known tool names; reject hallucinated names
    valid_parts = [p for p in parts if p in _VALID_TOOLS]
    if not valid_parts:
        return {"tool": None, "chain": None}

    if len(valid_parts) == 1:
        return {"tool": valid_parts[0], "chain": None}

    return {"tool": valid_parts[0], "chain": valid_parts}
