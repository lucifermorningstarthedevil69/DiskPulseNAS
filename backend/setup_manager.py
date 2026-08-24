"""
DiskPulse Setup Manager
Handles first-run configuration, drive discovery, and persistent settings.
"""
import os
import sys
import json
import platform
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

import psutil
from backend.config import format_bytes, BASE_DIR, DATA_DIR, CONFIG_FILE, _storage_pool_config, _read_config_pointer

DEFAULT_CONFIG = {
    "setup_complete": False,
    "storage_root": str(BASE_DIR / "storage_pool"),
    "seed_demo_data": True,
    "app_port": 8000,
    "app_host": "0.0.0.0",
    "theme": "dark",
    "history_retention_days": 7,
}


# ─── Config persistence ────────────────────────────────────────────────────────

def load_config() -> Dict[str, Any]:
    """Load config from disk, merging with defaults for any missing keys."""
    # Primary: config inside the storage pool (survives EXE moves/deletes)
    sp_config = _storage_pool_config()
    if sp_config:
        try:
            stored = json.loads(sp_config.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **stored}
        except Exception:
            pass

    # Secondary: legacy DATA_DIR location
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **stored}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    """Write config to disk in the data directory and storage pool."""
    from backend.config import save_config as _save_config
    _save_config(config)


def is_setup_complete() -> bool:
    cfg = load_config()
    return bool(cfg.get("setup_complete", False))


def reset_setup() -> None:
    """Reset to first-run state (keeps existing storage files)."""
    cfg = load_config()
    cfg["setup_complete"] = False
    save_config(cfg)


# ─── Drive / Partition Discovery ───────────────────────────────────────────────

def get_available_drives() -> List[Dict[str, Any]]:
    """
    Returns a rich list of available drives and mountpoints,
    cross-platform (Windows + Linux/macOS).
    """
    drives = []
    is_windows = platform.system() == "Windows"

    seen_devices = set()

    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        partitions = []

    for part in partitions:
        # Skip duplicates (same device mounted multiple places)
        device_key = part.device.lower().rstrip("\\/")
        if device_key in seen_devices:
            continue
        seen_devices.add(device_key)

        # Skip very small / virtual partitions on Linux
        if not is_windows and part.fstype in ("tmpfs", "devtmpfs", "sysfs", "proc", "cgroup", "overlay", "squashfs", "efivarfs", "debugfs"):
            continue

        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue

        # Skip micro drives < 100 MB (EFI, recovery, etc.)
        if usage.total < 100 * 1024 * 1024:
            continue

        # Build a human label for the drive
        label = _build_drive_label(part, is_windows)

        # Recommended storage subfolder
        if is_windows:
            suggested_path = str(Path(part.mountpoint) / "DiskPulse_Storage")
        else:
            suggested_path = str(Path(part.mountpoint) / "diskpulse_storage")

        # Health warning if nearly full
        warning = None
        if usage.percent >= 95:
            warning = "Drive is almost full (≥95%)"
        elif usage.percent >= 85:
            warning = "Drive is getting full (≥85%)"

        drives.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype or "unknown",
            "label": label,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent_used": round(usage.percent, 1),
            "total_human": format_bytes(usage.total),
            "used_human": format_bytes(usage.used),
            "free_human": format_bytes(usage.free),
            "suggested_path": suggested_path,
            "is_system": _is_system_drive(part, is_windows),
            "warning": warning,
            "platform": "windows" if is_windows else "linux",
        })

    # Sort: largest non-system drives first, then system drives
    drives.sort(key=lambda d: (d["is_system"], -d["total"]))

    # Add a "Custom Path" sentinel entry at the end
    drives.append({
        "device": "custom",
        "mountpoint": "",
        "fstype": "custom",
        "label": "Custom Path",
        "total": 0,
        "used": 0,
        "free": 0,
        "percent_used": 0,
        "total_human": "--",
        "used_human": "--",
        "free_human": "--",
        "suggested_path": str(BASE_DIR / "storage_pool"),
        "is_system": False,
        "warning": None,
        "platform": platform.system().lower(),
    })

    return drives


def _build_drive_label(part, is_windows: bool) -> str:
    """Construct a friendly display name for a partition."""
    label_parts = []

    if is_windows:
        # Try to get Windows volume label via ctypes
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            vol_name_buf = ctypes.create_unicode_buffer(261)
            fs_buf = ctypes.create_unicode_buffer(261)
            kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(part.mountpoint),
                vol_name_buf, ctypes.sizeof(vol_name_buf),
                None, None, None,
                fs_buf, ctypes.sizeof(fs_buf)
            )
            vol_label = vol_name_buf.value.strip()
            if vol_label:
                label_parts.append(vol_label)
        except Exception:
            pass

        label_parts.append(f"({part.mountpoint.rstrip(chr(92))})")
        if part.fstype:
            label_parts.append(f"[{part.fstype}]")
    else:
        # Linux: try to get volume label from blkid
        blkid_label = _get_linux_label(part.device)
        if blkid_label:
            label_parts.append(blkid_label)
        label_parts.append(part.mountpoint)
        if part.fstype:
            label_parts.append(f"[{part.fstype}]")

    return " ".join(label_parts) if label_parts else part.device


def _get_linux_label(device: str) -> Optional[str]:
    """Try blkid to get filesystem label on Linux."""
    try:
        result = subprocess.run(
            ["blkid", "-s", "LABEL", "-o", "value", device],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _is_system_drive(part, is_windows: bool) -> bool:
    """Heuristic: detect if this is the OS / boot drive."""
    mp = part.mountpoint.lower()
    if is_windows:
        return mp.startswith("c:\\") or mp == "c:/"
    else:
        return mp == "/" or mp in ("/boot", "/boot/efi", "/efi")


# ─── Apply new configuration ───────────────────────────────────────────────────

def apply_setup(
    storage_root: str,
    seed_demo_data: bool,
    port: int = 8000,
) -> Dict[str, Any]:
    """
    Validate and persist the user's setup choices.
    Returns {"success": True} or {"success": False, "error": "..."}
    """
    try:
        root_path = Path(storage_root).resolve()
        root_path.mkdir(parents=True, exist_ok=True)

        # Quick write-access test
        test_file = root_path / ".diskpulse_write_test"
        test_file.write_text("ok")
        test_file.unlink()

    except PermissionError:
        return {"success": False, "error": f"No write permission on path: {storage_root}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    cfg = load_config()
    cfg["setup_complete"] = True
    cfg["storage_root"] = str(root_path)
    cfg["seed_demo_data"] = seed_demo_data
    cfg["app_port"] = port
    save_config(cfg)

    return {"success": True, "storage_root": str(root_path)}
