"""
DiskPulse Configuration
Reads from diskpulse_config.json if present, otherwise falls back to env vars / defaults.
"""
import json
import os
import sys
from pathlib import Path

# When bundled into a single-file executable by PyInstaller:
# - sys._MEIPASS contains unpacked bundled assets (e.g. frontend/)
# - sys.executable's parent is where the .exe lives (for configs/storage_pool)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BUNDLE_DIR = Path(sys._MEIPASS)
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    BASE_DIR = BUNDLE_DIR

# ── Data & Config Directory ───────────────────────────────────────────────────
# Store json files for configuration and history in a dedicated data directory.
# When running as a frozen EXE, DATA_DIR lives next to the executable so it
# survives a moved/deleted EXE only if the user re-selects the storage pool.
# To make the EXE fully portable, configs are ALSO written inside the storage
# pool at STORAGE_ROOT/.diskpulse/, and a small pointer file records that path.
DATA_DIR = Path(os.environ.get("DISKPULSE_DATA_DIR", str(BASE_DIR / "data")))
CONFIG_FILE = DATA_DIR / "diskpulse_config.json"


# ── Config Pointer (EXE portability) ─────────────────────────────────────────
# A tiny text file in a fixed OS location that records where the real config
# lives inside the storage pool. This lets a moved/re-downloaded EXE recover
# the user's settings without re-running setup.

def _get_config_pointer_path() -> Path:
    if os.name == 'nt':
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "DiskPulse" / "config_path.txt"
    elif os.name == 'posix':
        config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        return Path(config_home) / "diskpulse" / "config_path.txt"
    return DATA_DIR / "config_path.txt"


def _read_config_pointer() -> Path | None:
    pointer = _get_config_pointer_path()
    if not pointer.exists():
        return None
    try:
        text = pointer.read_text(encoding="utf-8").strip()
        if text:
            return Path(text)
    except Exception:
        pass
    return None


def _write_config_pointer(config_path: Path) -> None:
    pointer = _get_config_pointer_path()
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(config_path.resolve()), encoding="utf-8")
    except Exception:
        pass


def _storage_pool_config() -> Path | None:
    """Best-effort search for the config inside the storage pool."""
    # 1. Pointer file
    ptr = _read_config_pointer()
    if ptr and ptr.exists():
        return ptr

    # 2. Env var
    env_root = os.environ.get("DISKPULSE_STORAGE_ROOT")
    if env_root:
        candidate = Path(env_root) / ".diskpulse" / "diskpulse_config.json"
        if candidate.exists():
            return candidate

    # 3. Default storage pool path (matches the wizard's suggested path)
    candidate = BASE_DIR / "storage_pool" / ".diskpulse" / "diskpulse_config.json"
    if candidate.exists():
        return candidate

    return None


# ── Load persisted config (written by setup wizard) ────────────────────────────
def _load_persisted() -> dict:
    # Primary: config inside the storage pool (survives EXE moves/deletes)
    sp_config = _storage_pool_config()
    if sp_config:
        try:
            import json
            return json.loads(sp_config.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Secondary: legacy DATA_DIR location
    legacy_file = BASE_DIR / "diskpulse_config.json"
    if not CONFIG_FILE.exists() and legacy_file.exists():
        try:
            import shutil
            shutil.copy2(legacy_file, CONFIG_FILE)
        except Exception:
            pass

    target = CONFIG_FILE if CONFIG_FILE.exists() else legacy_file
    if target.exists():
        try:
            import json
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    """Write config to disk atomically in the data directory and storage pool."""
    # Always write to the legacy location so the current process can reload
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

    # Also write inside the storage pool so the config survives EXE deletion
    storage_root = config.get("storage_root")
    if storage_root and config.get("setup_complete"):
        try:
            storage_dir = Path(storage_root) / ".diskpulse"
            storage_dir.mkdir(parents=True, exist_ok=True)
            storage_config = storage_dir / "diskpulse_config.json"
            storage_config.write_text(json.dumps(config, indent=2), encoding="utf-8")
            _write_config_pointer(storage_config)
        except Exception:
            pass

_persisted = _load_persisted()

# ── Active settings (env vars override persisted config) ───────────────────────
STORAGE_ROOT = os.environ.get(
    "DISKPULSE_STORAGE_ROOT",
    _persisted.get("storage_root", str(BASE_DIR / "storage_pool"))
)

HOST = os.environ.get("DISKPULSE_HOST", _persisted.get("app_host", "0.0.0.0"))
PORT = int(os.environ.get("DISKPULSE_PORT", str(_persisted.get("app_port", 8000))))
DEBUG = os.environ.get("DISKPULSE_DEBUG", "False").lower() in ("true", "1", "yes")

FRONTEND_DIR = BUNDLE_DIR / "frontend"

# ── Telemetry ──────────────────────────────────────────────────────────────────
TELEMETRY_INTERVAL_SECS = 1.0

# ── Download category → file extension map ────────────────────────────────────
DOWNLOAD_CATEGORIES = {
    "media":     ["mp4", "mkv", "avi", "mov", "webm", "mp3", "flac", "wav", "aac", "ogg"],
    "iso":       ["iso", "img", "vmdk", "qcow2", "vdi"],
    "documents": ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv", "json"],
    "software":  ["exe", "msi", "dmg", "pkg", "deb", "rpm", "AppImage", "zip", "tar.gz", "tar.xz", "7z"],
    "backups":   ["bak", "tar", "gz", "dump", "sql", "bundle"],
}

# ── Type-based sort folders (uploads + downloads) ─────────────────────────────
# When "sort into type folders" is enabled, an incoming file is dropped into the
# matching subfolder below (nested inside the chosen destination). This is the
# single source of truth shared by the uploader and the download engine so both
# organise files identically. Order matters: the FIRST bucket that lists an
# extension wins, and anything unmatched lands in "Other".
FILE_TYPE_FOLDERS = {
    "Images":      [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
                    ".tif", ".tiff", ".heic", ".heif", ".avif", ".ico", ".raw"],
    "Video":       [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv",
                    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".ogv"],
    "Audio":       [".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma",
                    ".opus", ".aiff", ".alac", ".mid", ".midi"],
    "Documents":   [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                    ".txt", ".md", ".csv", ".rtf", ".odt", ".ods", ".odp",
                    ".epub", ".mobi", ".json", ".xml"],
    "Archives":    [".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2",
                    ".xz", ".zst", ".lz", ".lzma", ".cab", ".arj"],
    "Disk Images": [".iso", ".img", ".vmdk", ".qcow2", ".vdi", ".vhd", ".vhdx",
                    ".bin", ".cue", ".nrg", ".toast"],
    "Programs":    [".exe", ".msi", ".msix", ".apk", ".deb", ".rpm", ".appimage",
                    ".pkg", ".dmg", ".bat", ".sh", ".jar", ".flatpak", ".snap"],
}

# Folder used when no extension bucket matches.
FILE_TYPE_OTHER = "Other"


def folder_for_extension(ext: str) -> str:
    """Return the type-folder name (Images, Video, …) for a file extension.

    Accepts the extension with or without a leading dot; matching is
    case-insensitive. Unknown or blank extensions map to ``Other``.
    """
    if not ext:
        return FILE_TYPE_OTHER
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    for folder, exts in FILE_TYPE_FOLDERS.items():
        if ext in exts:
            return folder
    return FILE_TYPE_OTHER


# ── Human-readable formatting ─────────────────────────────────────────────────
# One implementation for the whole app so the sidebar, the dashboard card, the
# file manager and the download list can never disagree about the same drive.
#
# Sizes are 1024-based but labelled KB/MB/GB/TB, which is what Windows Explorer
# and most NAS UIs report. The strictly-correct IEC labels (KiB/GiB) are what
# `humanize.naturalsize(binary=True)` produced before, and they read as noise to
# most people looking at a storage gauge.
_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(num_bytes) -> str:
    """``1503238553`` → ``'1.4 GB'``; picks the unit from the magnitude.

    A decimal is kept only below 10, the same rule ``ls -lh`` uses — so "4.0 KB"
    and "1.4 GB" stay precise while "40 KB" and "932 GB" stay short enough for
    narrow columns and the sidebar.
    """
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    if size <= 0:
        return "0 B"

    idx = 0
    while size >= 1024 and idx < len(_BYTE_UNITS) - 1:
        size /= 1024.0
        idx += 1

    if idx == 0 or size >= 10:
        return f"{round(size)} {_BYTE_UNITS[idx]}"
    return f"{size:.1f} {_BYTE_UNITS[idx]}"


def format_bytes_short(num_bytes) -> str:
    """Coreutils style — ``'1.4G'``, ``'932G'``, ``'4.0K'``.

    Only for the terminal emulator: real ``ls -lh`` / ``du -h`` / ``df -h``
    print a single-letter suffix, so using :func:`format_bytes` there would make
    the emulated shell look wrong to anyone who knows the real tools.
    """
    text = format_bytes(num_bytes)
    value, _, unit = text.partition(" ")
    return f"{value}{unit[0]}" if unit != "B" else f"{value}B"


def format_uptime(seconds) -> str:
    """``15129`` → ``'4h 12m'``; ``188400`` → ``'2d 4h 20m'``.

    Minutes are zero-padded so the string doesn't change width every minute and
    jitter the sidebar layout. Days appear only past 24h — a NAS that has been
    up for a fortnight would otherwise read "412h 07m".
    """
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "0h 00m"
    total = max(total, 0)

    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    if days:
        return f"{days}d {hours}h {minutes:02d}m"
    return f"{hours}h {minutes:02d}m"
