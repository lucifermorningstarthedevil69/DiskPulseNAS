#!/usr/bin/env python3
"""
DiskPulse NAS Storage Hub & Server Launcher
"""
import os
import sys
import uvicorn
from pathlib import Path
from backend.config import HOST, PORT, STORAGE_ROOT
from backend.setup_manager import is_setup_complete

def print_banner():
    banner = f"""
\033[36m\033[1m
   ____  _     _    ____        _           
  |  _ \\(_)___| | _|  _ \\ _   _| |___  ___ 
  | | | | / __| |/ / |_) | | | | / __|/ _ \\
  | |_| | \\__ \\   <|  __/| |_| | \\__ \\  __/
  |____/|_|___/_|\\_\\_|    \\__,_|_|___/\\___|
\033[0m
\033[32m\033[1m=== DiskPulse NAS Storage Hub & Diagnostics Server ===\033[0m
\033[37m- Storage Root:  \033[33m{STORAGE_ROOT}\033[0m
\033[37m- Server URL:    \033[36mhttp://{HOST}:{PORT}\033[0m
\033[37m- Local Access:  \033[36mhttp://localhost:{PORT}\033[0m
\033[37m- Fast Telemetry & Terminal WebSockets Active\033[0m
======================================================
"""
    print(banner)

def main():
    # Only auto-provision/seed the storage root on startup once the
    # first-run setup wizard has actually been completed. Doing this
    # unconditionally (even before setup) used to plant demo data in the
    # default storage_pool folder regardless of which drive or "fresh
    # start" option the user later picked in the wizard, making it look
    # like the wizard's choices had no effect.
    if is_setup_complete():
        Path(STORAGE_ROOT).mkdir(parents=True, exist_ok=True)

        # Ensure demo data exists if empty and the user opted into it
        from backend.setup_manager import load_config
        cfg = load_config()
        if cfg.get("seed_demo_data", True) and not any(Path(STORAGE_ROOT).iterdir()):
            try:
                from generate_demo_data import generate_sample_storage
                generate_sample_storage(STORAGE_ROOT)
            except Exception as e:
                print(f"Note: Could not generate initial demo data: {e}")

    print_banner()

    # Launch Uvicorn via an explicit Server so we can intercept Ctrl+C: on the
    # exit signal we kill any live ffmpeg transcodes FIRST, which unblocks their
    # streaming responses (the source files stop being read/locked). Otherwise a
    # browser tab still holding a /api/media/stream connection would keep ffmpeg
    # alive and make uvicorn's graceful shutdown wait indefinitely.
    class GracefulServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            try:
                from backend.media_service import terminate_all_streams
                n = terminate_all_streams()
                if n:
                    print(f"\nStopping {n} active media stream(s)...")
            except Exception:
                pass
            return super().handle_exit(sig, frame)

    config_kwargs = dict(
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )
    # Never wait more than a few seconds for a lingering stream to drain.
    # (timeout_graceful_shutdown exists on modern uvicorn; ignore if not.)
    try:
        config = uvicorn.Config("backend.main:app", timeout_graceful_shutdown=5, **config_kwargs)
    except TypeError:
        config = uvicorn.Config("backend.main:app", **config_kwargs)

    server = GracefulServer(config)
    try:
        server.run()
    except KeyboardInterrupt:
        # Raised when Ctrl+C forces the event loop down before the graceful
        # shutdown finishes; streams were already killed in handle_exit, so
        # just exit quietly instead of dumping a traceback.
        pass

if __name__ == "__main__":
    main()
