"""
Locate and register vendor / embedded binaries (ffmpeg, ffprobe, smartctl).

When running from a PyInstaller bundle the binaries live inside ``_MEIPASS``.
In dev mode the ``vendor/`` tree at the project root is used instead.

Either way the directories that contain the executables are **prepended** to
``os.environ["PATH"]`` so that every downstream ``shutil.which()`` call and
bare-command ``subprocess.run()`` invocation finds them transparently — no
per-call-site changes needed.

Call :func:`setup_embedded_tools` once, as early as possible in the process
lifetime (before any backend import that might probe for ffmpeg / smartctl).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def setup_embedded_tools() -> None:
    """Prepend bundled-binary directories to *PATH*.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    if getattr(setup_embedded_tools, "_done", False):
        return

    dirs_to_add: list[str] = []

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # Running inside a PyInstaller one-file bundle.
        base = Path(sys._MEIPASS)
    else:
        # Dev / source mode — look in the project root's vendor/ folder.
        base = Path(__file__).resolve().parent.parent

    ffmpeg_dir = base / "vendor" / "ffmpeg"
    smart_dir = base / "vendor" / "smartmontools"

    if ffmpeg_dir.is_dir():
        dirs_to_add.append(str(ffmpeg_dir))
    if smart_dir.is_dir():
        dirs_to_add.append(str(smart_dir))

    if dirs_to_add:
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(dirs_to_add) + os.pathsep + current_path

    setup_embedded_tools._done = True  # type: ignore[attr-defined]
