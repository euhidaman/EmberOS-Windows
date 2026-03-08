"""Snapshot and rollback system for EmberOS-Windows."""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from emberos.config import ROOT_DIR

logger = logging.getLogger("emberos.snapshot")

BACKUP_DIR = ROOT_DIR / "data" / "backups"


class SnapshotManager:
    """Manages file snapshots for rollback support."""

    def __init__(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    def snapshot_file(self, path: str, operation: str = "unknown") -> str:
        """Create a snapshot of a file before a destructive operation."""
        src = Path(path)
        if not src.exists():
            return ""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        snap_id = f"{ts}_{src.name}"
        snap_dir = BACKUP_DIR / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(snap_dir / "original"))
        else:
            shutil.copy2(str(src), str(snap_dir / "original"))

        meta = {
            "original_path": str(src.resolve()),
            "operation": operation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "size": src.stat().st_size if src.is_file() else 0,
            "is_dir": src.is_dir(),
        }
        with open(snap_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        logger.info("Snapshot created: %s → %s", path, snap_id)
        return snap_id

    def rollback_last(self) -> str:
        """Rollback the most recent snapshot."""
        snapshots = self._get_snapshot_dirs()
        if not snapshots:
            return "No snapshots available for rollback."
        return self._restore(snapshots[-1])

    def rollback_by_id(self, snapshot_id: str) -> str:
        """Rollback a specific snapshot."""
        snap_dir = BACKUP_DIR / snapshot_id
        if not snap_dir.exists():
            return f"Snapshot not found: {snapshot_id}"
        return self._restore(snap_dir)

    def list_snapshots(self) -> list[dict]:
        """Return all snapshots sorted by time desc."""
        result = []
        for snap_dir in self._get_snapshot_dirs():
            meta_file = snap_dir / "meta.json"
            if meta_file.exists():
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["id"] = snap_dir.name
                orig = snap_dir / "original"
                if orig.exists():
                    if orig.is_file():
                        meta["size"] = orig.stat().st_size
                    else:
                        meta["size"] = sum(
                            f.stat().st_size for f in orig.rglob("*") if f.is_file()
                        )
                result.append(meta)
        result.reverse()
        return result

    def has_snapshots(self) -> bool:
        return len(self._get_snapshot_dirs()) > 0

    def cleanup_old(self, days: int = 7):
        """Delete snapshot folders older than N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for snap_dir in self._get_snapshot_dirs():
            meta_file = snap_dir / "meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    ts = datetime.fromisoformat(meta["timestamp"])
                    if ts < cutoff:
                        shutil.rmtree(str(snap_dir), ignore_errors=True)
                        logger.info("Cleaned old snapshot: %s", snap_dir.name)
                except Exception:
                    pass

    def _get_snapshot_dirs(self) -> list[Path]:
        """Return snapshot dirs sorted by name (oldest first)."""
        if not BACKUP_DIR.exists():
            return []
        dirs = [d for d in BACKUP_DIR.iterdir() if d.is_dir() and (d / "meta.json").exists()]
        dirs.sort(key=lambda d: d.name)
        return dirs

    def _restore(self, snap_dir: Path) -> str:
        """Restore a snapshot."""
        meta_file = snap_dir / "meta.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        original_path = Path(meta["original_path"])
        backup_src = snap_dir / "original"

        if not backup_src.exists():
            return f"Snapshot data missing for: {snap_dir.name}"

        try:
            if meta.get("is_dir"):
                if original_path.exists():
                    shutil.rmtree(str(original_path))
                shutil.copytree(str(backup_src), str(original_path))
            else:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(backup_src), str(original_path))

            shutil.rmtree(str(snap_dir), ignore_errors=True)
            logger.info("Restored: %s", original_path)
            return f"Restored: {original_path}"
        except Exception as e:
            return f"Rollback failed: {e}"
