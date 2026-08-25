"""
DiskPulse NAS Standalone EXE Builder
Uses PyInstaller to compile DiskPulse into a single portable Windows executable (.exe).

Before building, automatically downloads ffmpeg and smartmontools binaries
into vendor/ if they are not already present.

Usage:
  python build_exe.py                     # Compiles DiskPulse.exe using DiskPulse.spec
  python build_exe.py --skip-vendor       # Skip vendor binary download
"""
import io
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib import request

from backend.icon_utils import create_diskpulse_icon

# Prevent PyInstaller from crashing in Windows Store Python isolated subproc environments
os.environ["PYINSTALLER_NO_SUBPROCESS_ISOLATION"] = "1"
os.environ["PYTHONNOUSERSITE"] = "1"

import PyInstaller.__main__

BASE_DIR = Path(__file__).resolve().parent

# ── Vendor binary URLs ────────────────────────────────────────────────────────
# FFmpeg "essentials" build from gyan.dev (widely used, trusted source).
# Update the version/filename here when a newer build is desired.
FFMPEG_ZIP_URL = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)
# Smartmontools portable Windows release (GitHub).
SMARTMONTOOLS_ZIP_URL = (
    "https://github.com/smartmontools/smartmontools/releases/download/"
    "RELEASE_7_4/smartmontools-7.4-1.win32-setup.exe"
)
# Direct-download of just smartctl.exe (standalone from a zip build).
SMARTCTL_ZIP_URL = (
    "https://sourceforge.net/projects/smartmontools/files/"
    "smartmontools/7.4/smartmontools-7.4-1.win32-setup.exe/download"
)
# LibreHardwareMonitor — CPU temperature sensor for Windows.
LHM_ZIP_URL = (
    "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/"
    "download/v0.9.3/LibreHardwareMonitor.zip"
)


def generate_ico_if_missing():
    """Ensure an icon.ico exists for the .exe build."""
    ico_path = BASE_DIR / "diskpulse.ico"
    if not ico_path.exists():
        print("[Build] Generating diskpulse.ico...")
        img = create_diskpulse_icon(128)
        img.save(str(ico_path), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
    return ico_path


def _download(url: str, desc: str) -> bytes:
    """Download a URL with a simple progress indicator."""
    print(f"[Vendor] Downloading {desc}...")
    print(f"         {url}")
    req = request.Request(url, headers={"User-Agent": "DiskPulse-Builder/1.0"})
    resp = request.urlopen(req, timeout=300)
    total = int(resp.headers.get("Content-Length", 0))
    data = bytearray()
    chunk_size = 1024 * 256
    while True:
        chunk = resp.read(chunk_size)
        if not chunk:
            break
        data.extend(chunk)
        if total:
            pct = len(data) * 100 // total
            mb = len(data) / (1024 * 1024)
            print(f"\r         {mb:.1f} MB ({pct}%)", end="", flush=True)
    print()
    return bytes(data)


def ensure_ffmpeg():
    """Download and extract ffmpeg.exe + ffprobe.exe into vendor/ffmpeg/."""
    vendor_ffmpeg = BASE_DIR / "vendor" / "ffmpeg"
    vendor_ffmpeg.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = vendor_ffmpeg / "ffmpeg.exe"
    ffprobe_exe = vendor_ffmpeg / "ffprobe.exe"

    if ffmpeg_exe.exists() and ffprobe_exe.exists():
        print("[Vendor] ffmpeg.exe and ffprobe.exe already present [OK]")
        return

    data = _download(FFMPEG_ZIP_URL, "FFmpeg essentials")
    print("[Vendor] Extracting ffmpeg.exe and ffprobe.exe...")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        extracted = {"ffmpeg.exe": False, "ffprobe.exe": False}
        for member in zf.namelist():
            basename = os.path.basename(member).lower()
            if basename in extracted and not extracted[basename]:
                target = vendor_ffmpeg / basename
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted[basename] = True
                print(f"         -> {target}")
        missing = [k for k, v in extracted.items() if not v]
        if missing:
            print(f"[Vendor] WARNING: Could not find {missing} in the zip archive!")
        else:
            print("[Vendor] FFmpeg extracted successfully [OK]")


def ensure_smartctl():
    """Download smartctl.exe into vendor/smartmontools/.

    smartmontools distributes Windows builds as an installer (.exe) or as a zip.
    We attempt to find a zip release first; if the user already placed the binary
    manually, we skip the download entirely.
    """
    vendor_smart = BASE_DIR / "vendor" / "smartmontools"
    vendor_smart.mkdir(parents=True, exist_ok=True)

    smartctl_exe = vendor_smart / "smartctl.exe"
    if smartctl_exe.exists():
        print("[Vendor] smartctl.exe already present [OK]")
        return

    # smartmontools doesn't publish a standalone zip for Windows, so we guide the
    # user to place it manually. The installer is an NSIS exe, not a zip, so we
    # can't easily auto-extract.
    print("[Vendor] ---------------------------------------------------------")
    print("[Vendor] smartctl.exe not found in vendor/smartmontools/")
    print("[Vendor]")
    print("[Vendor] To embed smartmontools in the build:")
    print("[Vendor]   1. Download smartmontools for Windows from:")
    print("[Vendor]      https://www.smartmontools.org/wiki/Download")
    print("[Vendor]   2. Install or extract it")
    print("[Vendor]   3. Copy smartctl.exe to:")
    print(f"[Vendor]      {vendor_smart}")
    print("[Vendor]")
    print("[Vendor] Or, if smartmontools is already installed on this machine:")

    # Try to auto-copy from system install
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        system_smartctl = os.path.join(base, "smartmontools", "bin", "smartctl.exe")
        if os.path.exists(system_smartctl):
            print(f"[Vendor] Found system install: {system_smartctl}")
            shutil.copy2(system_smartctl, smartctl_exe)
            print(f"[Vendor] Copied to {smartctl_exe} [OK]")
            return

    # Also check PATH
    which_smartctl = shutil.which("smartctl")
    if which_smartctl:
        print(f"[Vendor] Found on PATH: {which_smartctl}")
        shutil.copy2(which_smartctl, smartctl_exe)
        print(f"[Vendor] Copied to {smartctl_exe} [OK]")
        return

    print("[Vendor] Could not find smartctl on this system.")
    print("[Vendor] The build will proceed WITHOUT smartctl embedded.")
    print("[Vendor] ---------------------------------------------------------")


def ensure_librehardwaremonitor():
    """Download LibreHardwareMonitor into vendor/librehardwaremonitor/."""
    vendor_lhm = BASE_DIR / "vendor" / "librehardwaremonitor"
    vendor_lhm.mkdir(parents=True, exist_ok=True)

    lhm_exe = vendor_lhm / "LibreHardwareMonitor.exe"
    if lhm_exe.exists():
        print("[Vendor] LibreHardwareMonitor already present [OK]")
        return

    try:
        data = _download(LHM_ZIP_URL, "LibreHardwareMonitor")
        print("[Vendor] Extracting LibreHardwareMonitor...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                basename = os.path.basename(member)
                if not basename:
                    continue
                target = vendor_lhm / basename
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        print(f"[Vendor] LibreHardwareMonitor extracted to {vendor_lhm} [OK]")
    except Exception as e:
        print(f"[Vendor] WARNING: Could not download LibreHardwareMonitor: {e}")
        print("[Vendor] CPU temperature may show N/A on Windows.")


def ensure_vendor_binaries():
    """Download/verify all vendor binaries before building."""
    print("==================================================")
    print("  Checking Vendor Binaries                        ")
    print("==================================================")
    ensure_ffmpeg()
    ensure_smartctl()
    ensure_librehardwaremonitor()
    print()


def build():
    print("==================================================")
    print("  Building DiskPulse NAS Standalone Executable    ")
    print("==================================================")

    skip_vendor = "--skip-vendor" in sys.argv
    if not skip_vendor:
        ensure_vendor_binaries()
    else:
        print("[Build] Skipping vendor binary download (--skip-vendor)")

    generate_ico_if_missing()
    spec_path = BASE_DIR / "DiskPulse.spec"

    args = [
        "--noconfirm",
        "--clean",
        str(spec_path),
    ]

    print("[Build] Running PyInstaller with spec:")
    print(str(spec_path))
    print("--------------------------------------------------")

    try:
        PyInstaller.__main__.run(args)
        exe_path = BASE_DIR / "dist" / "DiskPulse.exe"
        print("==================================================")
        print(" [SUCCESS] Build completed successfully!")
        print(f" Executable location: {exe_path}")
        print("==================================================")
    except Exception as e:
        import traceback
        print("==================================================")
        print(f" [ERROR] Build failed: {e}")
        traceback.print_exc()
        print("==================================================")

if __name__ == "__main__":
    build()
