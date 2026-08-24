"""
Transfer history service for DiskPulse NAS.

Tracks completed downloads and uploads in ``.diskpulse/transfer_history.json``
with time-based auto-removal (configurable retention: 1 week, 1 month, etc.).

Follows the same pattern as ``speedtest_service.py``: JSON file in the storage
pool's ``.diskpulse/`` directory, loaded at startup, appended on completion,
written off the event loop, and pruned by TTL before save.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import BASE_DIR, DATA_DIR, _storage_pool_config

_HISTORY_FILE = DATA_DIR / "transfer_history.json"
_DEFAULT_RETENTION_DAYS = 7
_MAX_ENTRIES = 5000


def _resolve_history_file() -> Path:
    """Return the history file path, preferring the storage pool location."""
    default_path = DATA_DIR / "transfer_history.json"
    if _HISTORY_FILE != default_path:
        return _HISTORY_FILE

    try:
        sp_config = _storage_pool_config()
        if sp_config:
            candidate = sp_config.parent / "transfer_history.json"
            if candidate.exists() or sp_config.parent.exists():
                return candidate
    except Exception:
        pass

    return _HISTORY_FILE


_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _retention_days() -> int:
    """Read retention from config, falling back to the default."""
    try:
        from backend.setup_manager import load_config
        cfg = load_config()
        val = cfg.get("history_retention_days")
        if val is not None:
            try:
                days = int(val)
                return days if days > 0 else _DEFAULT_RETENTION_DAYS
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return _DEFAULT_RETENTION_DAYS


def _prune(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop entries older than the configured retention window."""
    cutoff = _now() - (_retention_days() * 86400)
    pruned = [e for e in history if e.get("timestamp", 0) >= cutoff]
    return pruned[-_MAX_ENTRIES:]


def load_history() -> List[Dict[str, Any]]:
    """Load history from disk. Returns oldest-first, capped by retention + max."""
    history_file = _resolve_history_file()
    try:
        raw = history_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    valid = [r for r in data if isinstance(r, dict)]
    return _prune(valid)


def append_history(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Append one entry, prune by TTL, and persist. Returns the new full list."""
    entry.setdefault("timestamp", _now())
    with _lock:
        history = load_history()
        history.append(entry)
        history = _prune(history)
        try:
            history_file = _resolve_history_file()
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
        except OSError:
            pass
        return list(history)


def clear_history() -> List[Dict[str, Any]]:
    """Wipe all history entries and return an empty list."""
    with _lock:
        try:
            history_file = _resolve_history_file()
            if history_file.exists():
                history_file.write_text("[]", encoding="utf-8")
        except OSError:
            pass
        return []


def get_retention_days() -> int:
    """Current retention setting in days."""
    return _retention_days()


def set_retention_days(days: int) -> int:
    """Persist a new retention window. Returns the effective value."""
    try:
        from backend.setup_manager import load_config, save_config
        cfg = load_config()
        cfg["history_retention_days"] = int(days) if int(days) > 0 else _DEFAULT_RETENTION_DAYS
        save_config(cfg)
        return cfg["history_retention_days"]
    except Exception:
        return _retention_days()


# ── Entry builders ────────────────────────────────────────────────────────────

def record_download(
    task_id: str,
    filename: str,
    url: str,
    size_bytes: int,
    status: str,
    destination: str = "",
    duration_secs: float = 0.0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a download event (called on completion / final error / cancel)."""
    entry: Dict[str, Any] = {
        "type": "download",
        "task_id": task_id,
        "filename": filename,
        "url": url,
        "size_bytes": size_bytes,
        "status": status,
        "destination": destination or "",
        "duration_secs": round(duration_secs, 2),
    }
    if extra:
        entry.update(extra)
    append_history(entry)


def record_upload(
    filename: str,
    size_bytes: int,
    status: str,
    destination: str = "",
    source: str = "browser",
    duration_secs: float = 0.0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record an upload event."""
    entry: Dict[str, Any] = {
        "type": "upload",
        "filename": filename,
        "size_bytes": size_bytes,
        "status": status,
        "destination": destination or "",
        "source": source,
        "duration_secs": round(duration_secs, 2),
    }
    if extra:
        entry.update(extra)
    append_history(entry)
