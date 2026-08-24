"""
DiskPulse NAS Standalone EXE Builder
Uses PyInstaller to compile DiskPulse into a single portable Windows executable (.exe).

Usage:
  python build_exe.py                     # Compiles DiskPulse.exe using DiskPulse.spec
"""
import os
import sys
from pathlib import Path
from backend.icon_utils import create_diskpulse_icon

# Prevent PyInstaller from crashing in Windows Store Python isolated subproc environments
os.environ["PYINSTALLER_NO_SUBPROCESS_ISOLATION"] = "1"
os.environ["PYTHONNOUSERSITE"] = "1"

import PyInstaller.__main__

BASE_DIR = Path(__file__).resolve().parent

def generate_ico_if_missing():
    """Ensure an icon.ico exists for the .exe build."""
    ico_path = BASE_DIR / "diskpulse.ico"
    if not ico_path.exists():
        print("[Build] Generating diskpulse.ico...")
        img = create_diskpulse_icon(128)
        img.save(str(ico_path), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
    return ico_path

def build():
    print("==================================================")
    print("  Building DiskPulse NAS Standalone Executable    ")
    print("==================================================")

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
