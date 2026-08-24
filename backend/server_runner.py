"""
Background Server Runner for DiskPulse NAS
Provides thread-safe start, readiness probe, and graceful shutdown of Uvicorn.
"""
import io
import os
import socket
import sys
import threading
import time
from pathlib import Path
import uvicorn
from backend.config import HOST, PORT, STORAGE_ROOT
from backend.setup_manager import is_setup_complete, load_config

# Safe stream fallback for PyInstaller --windowed / --noconsole mode
class SafeStream:
    def write(self, data):
        pass
    def flush(self):
        pass
    def isatty(self):
        return False

if sys.stdout is None:
    sys.stdout = SafeStream()
if sys.stderr is None:
    sys.stderr = SafeStream()
if sys.stdin is None:
    sys.stdin = io.StringIO()

class BackgroundServer:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self._is_running = False

    def is_port_open(self, timeout: float = 0.5) -> bool:
        """Check if server port is accepting connections."""
        target_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        try:
            with socket.create_connection((target_host, self.port), timeout=timeout):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        """Poll until server is ready to accept connections."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_port_open(timeout=0.2):
                return True
            time.sleep(0.1)
        return False

    def start(self) -> None:
        """Start Uvicorn in a daemon thread."""
        if self._is_running:
            return

        # Pre-seed demo data if setup was completed
        if is_setup_complete():
            try:
                Path(STORAGE_ROOT).mkdir(parents=True, exist_ok=True)
                cfg = load_config()
                if cfg.get("seed_demo_data", True) and not any(Path(STORAGE_ROOT).iterdir()):
                    from generate_demo_data import generate_sample_storage
                    generate_sample_storage(STORAGE_ROOT)
            except Exception as e:
                print(f"[DiskPulse] Note: Could not seed demo data: {e}")

        safe_log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s [%(levelname)s] %(message)s",
                },
                "access": {
                    "format": "%(asctime)s [%(levelname)s] %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        }

        from backend.main import app as fastapi_app

        config_kwargs = {
            "host": self.host,
            "port": self.port,
            "log_level": "info",
            "reload": False,
            "use_colors": False,
            "log_config": safe_log_config,
        }
        try:
            config = uvicorn.Config(fastapi_app, timeout_graceful_shutdown=3, **config_kwargs)
        except TypeError:
            config = uvicorn.Config(fastapi_app, **config_kwargs)

        self.server = uvicorn.Server(config)
        self._is_running = True

        def _run_loop():
            try:
                self.server.run()
            except Exception as e:
                import traceback
                err_text = f"[DiskPulse] Server error: {e}\n{traceback.format_exc()}"
                try:
                    from backend.config import BASE_DIR, _storage_pool_config
                    log_path = BASE_DIR / "diskpulse_server_error.log"
                    sp_config = _storage_pool_config()
                    if sp_config:
                        log_path = sp_config.parent / "diskpulse_server_error.log"
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(log_path, "a", encoding="utf-8") as err_f:
                        err_f.write(err_text + "\n")
                except Exception:
                    pass
                print(err_text)
            finally:
                self._is_running = False

        self.thread = threading.Thread(target=_run_loop, name="DiskPulse-Server-Thread", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Gracefully terminate active media streams and stop the server."""
        if not self._is_running and not (self.server and self.server.started):
            return

        print("[DiskPulse] Gracefully stopping server...")
        try:
            from backend.media_service import terminate_all_streams
            terminate_all_streams()
        except Exception:
            pass

        if self.server:
            self.server.should_exit = True

        self._is_running = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# Global default instance
server_runner = BackgroundServer()
