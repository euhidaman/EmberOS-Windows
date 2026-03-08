"""File attachment analysis for EmberOS-Windows."""

import csv
import io
import logging
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger("emberos.use_cases.file_analysis")

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".csv", ".json", ".log",
    ".html", ".xml", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".bat", ".ps1", ".c", ".cpp", ".h", ".rs", ".go", ".java",
    ".css", ".sh", ".rb",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"}


def _read_text_file(path: Path, max_chars: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "\n[... truncated]"
        return text
    except Exception as e:
        return f"Error reading: {e}"


def _read_csv_file(path: Path) -> str:
    try:
        lines = []
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i >= 100:
                    lines.append("[... truncated at 100 rows]")
                    break
                lines.append(" | ".join(row))
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading CSV: {e}"


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            if "word/document.xml" not in z.namelist():
                return "Could not find document.xml in .docx"
            xml_data = z.read("word/document.xml")
        root = ET.fromstring(xml_data)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = []
            for t in para.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
                if t.text:
                    texts.append(t.text)
            if texts:
                paragraphs.append("".join(texts))
        text = "\n".join(paragraphs)
        if len(text) > 6000:
            return text[:6000] + "\n[... truncated]"
        return text if text else "Empty document"
    except Exception as e:
        return f"Error reading .docx: {e}"


def _read_xlsx(path: Path) -> str:
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            # Read shared strings
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss_xml = z.read("xl/sharedStrings.xml")
                ss_root = ET.fromstring(ss_xml)
                ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                for si in ss_root.iter(f"{{{ns}}}si"):
                    parts = []
                    for t in si.iter(f"{{{ns}}}t"):
                        if t.text:
                            parts.append(t.text)
                    shared.append("".join(parts))

            # Read first sheet
            sheet_xml = z.read("xl/worksheets/sheet1.xml")
            sheet_root = ET.fromstring(sheet_xml)
            ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            rows = []
            for row_el in sheet_root.iter(f"{{{ns}}}row"):
                cells = []
                for c in row_el.iter(f"{{{ns}}}c"):
                    val_el = c.find(f"{{{ns}}}v")
                    val = val_el.text if val_el is not None else ""
                    cell_type = c.get("t", "")
                    if cell_type == "s" and val:
                        idx = int(val)
                        val = shared[idx] if idx < len(shared) else val
                    cells.append(val or "")
                rows.append(" | ".join(cells))
                if len(rows) >= 100:
                    rows.append("[... truncated at 100 rows]")
                    break
            return "\n".join(rows) if rows else "Empty spreadsheet"
    except Exception as e:
        return f"Error reading .xlsx: {e}"


def _read_pdf(path: Path) -> str:
    # Try pdftotext first
    import subprocess
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
            if len(text) > 6000:
                return text[:6000] + "\n[... truncated]"
            return text
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Fallback: try to extract text from raw PDF bytes
    try:
        raw = path.read_bytes()
        text_parts = []
        i = 0
        while i < len(raw):
            start = raw.find(b"(", i)
            if start == -1:
                break
            end = raw.find(b")", start)
            if end == -1:
                break
            chunk = raw[start + 1:end]
            try:
                decoded = chunk.decode("latin-1")
                if len(decoded) > 2 and decoded.isprintable():
                    text_parts.append(decoded)
            except Exception:
                pass
            i = end + 1
        if text_parts:
            text = " ".join(text_parts)
            if len(text) > 6000:
                return text[:6000] + "\n[... truncated]"
            return text
    except Exception:
        pass

    return "PDF content could not be extracted \u2014 pdftotext not available"


def _read_archive(path: Path) -> str:
    try:
        ext = path.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(str(path), "r") as z:
                names = z.namelist()[:100]
                return f"Archive contents ({len(z.namelist())} files):\n" + "\n".join(names)
        elif ext in (".tar", ".gz", ".bz2", ".tgz"):
            mode = "r:gz" if ext in (".gz", ".tgz") else "r:bz2" if ext == ".bz2" else "r"
            with tarfile.open(str(path), mode) as t:
                names = t.getnames()[:100]
                return f"Archive contents ({len(t.getnames())} files):\n" + "\n".join(names)
    except Exception as e:
        return f"Error reading archive: {e}"
    return f"[Archive: {path.name}]"


def read_attached_file(path: str) -> str:
    """Read content from an attached file based on its type."""
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"

    ext = p.suffix.lower()
    size = p.stat().st_size

    if ext == ".csv":
        return _read_csv_file(p)
    elif ext in _TEXT_EXTENSIONS:
        return _read_text_file(p)
    elif ext == ".pdf":
        return _read_pdf(p)
    elif ext == ".docx":
        return _read_docx(p)
    elif ext == ".xlsx":
        return _read_xlsx(p)
    elif ext in _IMAGE_EXTENSIONS:
        return f"[Image file: {p.name}, {size} bytes \u2014 vision analysis not available in this version]"
    elif ext in (".zip", ".tar", ".gz", ".bz2", ".tgz", ".7z"):
        if ext == ".7z":
            return f"[7z archive: {p.name} \u2014 extraction requires 7-Zip]"
        return _read_archive(p)
    else:
        return f"[Binary file: {p.name} \u2014 cannot analyze content]"


def analyze_attached_files(file_paths: list[str], user_message: str,
                           llm_client=None) -> str:
    """Analyze multiple attached files and build a combined prompt."""
    sections = []
    for fp in file_paths:
        content = read_attached_file(fp)
        name = Path(fp).name
        sections.append(f"=== FILE: {name} ===\n{content}")

    combined = "\n\n".join(sections)
    # Truncate total to 6000 chars
    if len(combined) > 6000:
        combined = combined[:6000] + "\n[... truncated]"

    prompt = (
        f"The user attached {len(file_paths)} file(s) and asks: '{user_message}'\n\n"
        + combined
    )

    if llm_client:
        try:
            response = llm_client.chat([
                {"role": "system", "content": "Analyze the attached files and answer the user's question concisely."},
                {"role": "user", "content": prompt},
            ])
            return response
        except Exception as e:
            return f"Analysis error: {e}\n\nRaw file contents:\n{combined}"

    return combined
