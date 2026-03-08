"""File management operations for EmberOS-Windows."""

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("emberos.use_cases.file_ops")

_TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".json", ".csv", ".log", ".md", ".html",
    ".xml", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".rs", ".go", ".java", ".css", ".sh", ".rb",
}

_FILE_CATEGORIES = {
    "PDFs": {".pdf"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"},
    "Documents": {".doc", ".docx", ".txt", ".md", ".odt", ".rtf"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs", ".go", ".html", ".css"},
}

_TYPE_EXTENSIONS = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"},
    "document": {".doc", ".docx", ".txt", ".md", ".odt", ".rtf", ".pdf"},
    "spreadsheet": {".xls", ".xlsx", ".csv", ".ods"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    "archive": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs", ".go", ".html", ".css"},
}


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def find_files(query: str, search_root: str = None, file_type: str = None,
               modified_within_days: int = None) -> list[str]:
    root = Path(search_root) if search_root else Path.home()
    if not root.exists():
        return []

    cutoff = None
    if modified_within_days:
        cutoff = datetime.now().timestamp() - (modified_within_days * 86400)

    ext_filter = None
    if file_type:
        ft = file_type.lower()
        if ft in _TYPE_EXTENSIONS:
            ext_filter = _TYPE_EXTENSIONS[ft]
        elif not ft.startswith("."):
            ext_filter = {f".{ft}"}
        else:
            ext_filter = {ft}

    results = []
    query_lower = query.lower()
    try:
        for p in root.rglob("*"):
            if len(results) >= 50:
                break
            if not p.is_file():
                continue
            if ext_filter and p.suffix.lower() not in ext_filter:
                continue
            if cutoff:
                try:
                    if os.path.getmtime(str(p)) < cutoff:
                        continue
                except OSError:
                    continue
            if query_lower and query_lower not in p.name.lower():
                continue
            results.append(str(p))
    except PermissionError:
        pass
    return results


def read_file_contents(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"
    if not p.is_file():
        return f"Not a file: {path}"
    if p.suffix.lower() in _TEXT_EXTENSIONS:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > 8000:
                return text[:8000] + "\n[... truncated]"
            return text
        except Exception as e:
            return f"Error reading file: {e}"
    return f"Binary or unreadable file: {p.name}"


def get_file_info(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    stat = p.stat()
    return {
        "name": p.name,
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "extension": p.suffix,
        "is_dir": p.is_dir(),
        "permissions": oct(stat.st_mode),
    }


def create_directory(path: str) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Directory created: {p.resolve()}"


def move_file(src: str, dst: str, snapshot_mgr=None) -> str:
    s = Path(src)
    if not s.exists():
        return f"Source not found: {src}"
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(src, "move")
    shutil.move(str(s), dst)
    return f"Moved: {src} → {dst}"


def copy_file(src: str, dst: str) -> str:
    s = Path(src)
    if not s.exists():
        return f"Source not found: {src}"
    if s.is_dir():
        shutil.copytree(str(s), dst)
    else:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), dst)
    return f"Copied: {src} → {dst}"


def rename_file(path: str, new_name: str, snapshot_mgr=None) -> str:
    p = Path(path)
    if not p.exists():
        return f"Not found: {path}"
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(path, "rename")
    new_path = p.parent / new_name
    p.rename(new_path)
    return f"Renamed: {p.name} → {new_name}"


def delete_file(path: str, snapshot_mgr=None) -> str:
    p = Path(path)
    if not p.exists():
        return f"Not found: {path}"
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(path, "delete")
    if p.is_dir():
        shutil.rmtree(str(p))
        return f"Deleted directory: {path}"
    else:
        os.remove(str(p))
        return f"Deleted: {path}"


def organize_folder_by_type(folder: str, preview_only: bool = True,
                            snapshot_mgr=None) -> dict:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return {"error": f"Not a directory: {folder}"}

    groups = {}
    for f in root.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        category = "Other"
        for cat, exts in _FILE_CATEGORIES.items():
            if ext in exts:
                category = cat
                break
        groups.setdefault(category, []).append(f)

    summary = {cat: len(files) for cat, files in groups.items()}

    if preview_only:
        return {"preview": summary, "total_files": sum(summary.values())}

    moved = {}
    for category, files in groups.items():
        cat_dir = root / category
        cat_dir.mkdir(exist_ok=True)
        count = 0
        for f in files:
            dst = cat_dir / f.name
            if snapshot_mgr:
                snapshot_mgr.snapshot_file(str(f), "organize")
            shutil.move(str(f), str(dst))
            count += 1
        moved[category] = count
    return {"moved": moved, "total_moved": sum(moved.values())}


def list_directory(path: str) -> list[dict]:
    root = Path(path)
    if not root.exists():
        return [{"error": f"Not found: {path}"}]
    items = []
    for entry in sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        stat = entry.stat()
        items.append({
            "name": entry.name,
            "type": "directory" if entry.is_dir() else "file",
            "size_human": _human_size(stat.st_size) if entry.is_file() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return items
