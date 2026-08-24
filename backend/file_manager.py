import os
import shutil
import mimetypes
import zipfile
import tempfile
import io
import time
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
import humanize

from backend.config import STORAGE_ROOT, format_bytes

class FileManager:
    def __init__(self, root_dir: str = None):
        # Resolve lazily (not at import time) so a storage root chosen
        # later via the setup wizard is picked up correctly.
        if root_dir is None:
            import backend.config as _config
            root_dir = _config.STORAGE_ROOT
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

        # Background move/copy registry: op_id -> live progress state.
        self._operations: Dict[str, Dict[str, Any]] = {}
        self._op_lock = threading.Lock()

    def set_root(self, new_root_dir: str) -> None:
        """Repoint this (singleton) FileManager at a new storage root.
        Called by the setup wizard after the user picks a drive/path so
        the running server starts using it immediately, no restart needed."""
        self.root_dir = Path(new_root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, target_rel_path: str) -> Path:
        """Resolves target relative path safely under root_dir."""
        clean_rel = target_rel_path.strip().lstrip("/\\")
        resolved = (self.root_dir / clean_rel).resolve()
        # Security: Prevent path traversal escaping root_dir
        if not str(resolved).startswith(str(self.root_dir)):
            return self.root_dir
        return resolved

    def _get_relative_path(self, full_path: Path) -> str:
        try:
            rel = full_path.relative_to(self.root_dir)
            return str(rel).replace("\\", "/")
        except ValueError:
            return ""

    def _get_file_info(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        is_dir = path.is_dir()
        rel_path = self._get_relative_path(path)
        
        mime_type, _ = mimetypes.guess_type(str(path))
        if is_dir:
            mime_type = "inode/directory"
            try:
                children_count = len(list(path.iterdir()))
            except Exception:
                children_count = 0
            size_bytes = 0
            size_human = f"{children_count} items"
        else:
            children_count = 0
            size_bytes = stat.st_size
            size_human = format_bytes(size_bytes)

        ext = path.suffix.lstrip(".").lower() if not is_dir else ""
        
        # Categorize file type for UI icons and actions
        category = "other"
        if is_dir:
            category = "folder"
        elif ext in ["mp4", "mkv", "webm", "avi", "mov", "flv"]:
            category = "video"
        elif ext in ["mp3", "wav", "flac", "ogg", "aac", "m4a"]:
            category = "audio"
        elif ext in ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"]:
            category = "image"
        elif ext in ["pdf"]:
            category = "pdf"
        elif ext in ["txt", "md", "py", "js", "html", "css", "json", "yml", "yaml", "sh", "bat", "ini", "conf", "sql", "xml", "csv", "log"]:
            category = "text"
        elif ext in ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "iso"]:
            category = "archive"
        elif ext in ["exe", "msi", "deb", "rpm", "bin", "apk"]:
            category = "executable"

        return {
            "name": path.name,
            "path": rel_path if rel_path != "." else "",
            "is_dir": is_dir,
            "size": size_bytes,
            "size_human": size_human,
            "mime": mime_type or "application/octet-stream",
            "category": category,
            "extension": ext,
            "items_count": children_count,
            "modified": stat.st_mtime,
            "modified_human": humanize.naturaltime(time.time() - stat.st_mtime),
            "permissions": oct(stat.st_mode)[-3:],
        }

    def list_directory(self, rel_path: str = "") -> Dict[str, Any]:
        target = self._resolve_safe_path(rel_path)
        if not target.exists():
            return {"error": "Directory not found", "files": [], "current_path": ""}
        if not target.is_dir():
            return {"error": "Path is not a directory", "files": [], "current_path": ""}

        items = []
        try:
            for child in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    items.append(self._get_file_info(child))
                except (PermissionError, OSError):
                    continue
        except Exception as e:
            return {"error": str(e), "files": [], "current_path": self._get_relative_path(target)}

        # Build breadcrumbs
        current_rel = self._get_relative_path(target)
        breadcrumbs = [{"name": "root", "path": ""}]
        if current_rel and current_rel != ".":
            parts = current_rel.split("/")
            accum = ""
            for p in parts:
                if not p:
                    continue
                accum = f"{accum}/{p}" if accum else p
                breadcrumbs.append({"name": p, "path": accum})

        return {
            "success": True,
            "current_path": current_rel if current_rel != "." else "",
            "breadcrumbs": breadcrumbs,
            "files": items,
            "total_items": len(items),
        }

    def search_files(self, query: str, rel_path: str = "") -> List[Dict[str, Any]]:
        target = self._resolve_safe_path(rel_path)
        if not target.exists():
            return []

        query = query.lower()
        results = []
        try:
            for item in target.rglob("*"):
                if query in item.name.lower():
                    results.append(self._get_file_info(item))
                    if len(results) >= 100:  # Cap search results
                        break
        except Exception:
            pass
        return results

    def create_directory(self, rel_parent: str, name: str) -> Dict[str, Any]:
        clean_name = name.strip().replace("/", "_").replace("\\", "_")
        if not clean_name or clean_name in (".", ".."):
            return {"success": False, "error": "Invalid directory name"}
        
        target = self._resolve_safe_path(rel_parent) / clean_name
        if target.exists():
            return {"success": False, "error": "Directory already exists"}
        
        try:
            target.mkdir(parents=True, exist_ok=True)
            return {"success": True, "file": self._get_file_info(target)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_file(self, rel_parent: str, name: str, content: str = "") -> Dict[str, Any]:
        clean_name = name.strip().replace("/", "_").replace("\\", "_")
        if not clean_name or clean_name in (".", ".."):
            return {"success": False, "error": "Invalid file name"}

        target = self._resolve_safe_path(rel_parent) / clean_name
        if target.exists():
            return {"success": False, "error": "File already exists"}

        try:
            target.write_text(content, encoding="utf-8")
            return {"success": True, "file": self._get_file_info(target)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_file_content(self, rel_path: str, max_bytes: int = 5 * 1024 * 1024) -> Dict[str, Any]:
        target = self._resolve_safe_path(rel_path)
        if not target.exists() or not target.is_file():
            return {"success": False, "error": "File not found"}

        if target.stat().st_size > max_bytes:
            return {"success": False, "error": "File exceeds maximum preview limit (5MB)"}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return {
                "success": True,
                "content": content,
                "info": self._get_file_info(target)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file_content(self, rel_path: str, content: str) -> Dict[str, Any]:
        target = self._resolve_safe_path(rel_path)
        if not target.exists() or not target.is_file():
            return {"success": False, "error": "File not found"}

        try:
            target.write_text(content, encoding="utf-8")
            return {"success": True, "info": self._get_file_info(target)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _release_media_streams(self, target: Path) -> None:
        """Kill any ffmpeg transcode still reading this file/folder.

        ffmpeg opens its source with a sharing mode that makes Windows refuse
        rename/move/delete on it ("file is in use by ffmpeg"). A live stream
        can outlive the player view (another tab, a paused video keeping its
        buffer connection open), so file operations release it first."""
        try:
            from backend.media_service import stop_streams_for_path
            stop_streams_for_path(str(target))
        except Exception:
            pass

    def rename_item(self, rel_path: str, new_name: str) -> Dict[str, Any]:
        target = self._resolve_safe_path(rel_path)
        if not target.exists():
            return {"success": False, "error": "Source item does not exist"}

        clean_new_name = new_name.strip().replace("/", "_").replace("\\", "_")
        if not clean_new_name or clean_new_name in (".", ".."):
            return {"success": False, "error": "Invalid new name"}

        destination = target.parent / clean_new_name
        if destination.exists():
            return {"success": False, "error": "An item with that name already exists"}

        self._release_media_streams(target)
        try:
            target.rename(destination)
            return {"success": True, "file": self._get_file_info(destination)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---- Background move / copy with live progress ----
    # The old synchronous move/copy ran inside the request handler, which
    # blocked the FastAPI event loop (freezing telemetry and the whole UI)
    # for the entire transfer and only reported pass/fail at the very end.
    # Transfers now run on a worker thread; the API returns an op_id that
    # the frontend polls via get_transfer_status().

    # 4 MiB per read/write: good throughput while keeping progress updates
    # frequent enough to feel live even on slow disks.
    _COPY_CHUNK = 4 * 1024 * 1024

    def start_move(self, source_rel_paths: List[str], dest_rel_folder: str) -> Dict[str, Any]:
        return self._start_transfer("move", source_rel_paths, dest_rel_folder)

    def start_copy(self, source_rel_paths: List[str], dest_rel_folder: str) -> Dict[str, Any]:
        return self._start_transfer("copy", source_rel_paths, dest_rel_folder)

    def get_transfer_status(self, op_id: str) -> Optional[Dict[str, Any]]:
        with self._op_lock:
            state = self._operations.get(op_id)
            if state is None:
                return None
            snapshot = dict(state)
            snapshot["completed"] = list(state["completed"])
            snapshot["errors"] = list(state["errors"])
        return snapshot

    def _start_transfer(self, op_type: str, source_rel_paths: List[str], dest_rel_folder: str) -> Dict[str, Any]:
        dest_folder = self._resolve_safe_path(dest_rel_folder)
        if not dest_folder.exists() or not dest_folder.is_dir():
            return {"success": False, "error": "Target destination folder not found"}

        items = []
        errors = []
        for src in source_rel_paths:
            src_target = self._resolve_safe_path(src)
            if not src_target.exists():
                errors.append(f"Source {src} not found")
                continue
            if op_type == "move" and (src_target == dest_folder or dest_folder.is_relative_to(src_target)):
                errors.append(f"Cannot move {src} into itself")
                continue
            items.append((src, src_target))

        if not items:
            return {"success": False, "error": "; ".join(errors) or "No valid source items"}

        op_id = uuid.uuid4().hex[:12]
        state = {
            "op_id": op_id,
            "type": op_type,
            "status": "running",       # running | done | error
            "total_bytes": 0,
            "transferred_bytes": 0,
            "total_files": 0,
            "done_files": 0,
            "total_items": len(items),
            "done_items": 0,
            "current_item": "",        # top-level source being processed
            "current_file": "",        # file inside it currently in flight
            "completed": [],
            "errors": errors,
            "started_at": time.time(),
            "finished_at": None,
        }
        with self._op_lock:
            self._operations[op_id] = state
            # Keep the registry bounded; evict oldest finished ops first.
            if len(self._operations) > 24:
                running = {k for k, v in self._operations.items() if v["status"] == "running"}
                for key, _ in sorted(self._operations.items(), key=lambda kv: kv[1]["started_at"]):
                    if len(self._operations) <= 24 or key in running:
                        continue
                    del self._operations[key]

        thread = threading.Thread(
            target=self._run_transfer, args=(op_id, items, dest_folder),
            name=f"transfer-{op_type}-{op_id}", daemon=True,
        )
        thread.start()
        return {"success": True, "op_id": op_id}

    def _run_transfer(self, op_id: str, items: List, dest_folder: Path) -> None:
        with self._op_lock:
            state = self._operations[op_id]
        op_type = state["type"]
        try:
            # Scan sizes up front so the UI can render a real percentage.
            total_bytes, total_files = self._scan_totals([src for _, src in items])
            with self._op_lock:
                state["total_bytes"] = total_bytes
                state["total_files"] = total_files

            for src_rel, src_target in items:
                with self._op_lock:
                    state["current_item"] = src_rel
                    state["current_file"] = ""
                try:
                    dest_file = dest_folder / src_target.name
                    if op_type == "move":
                        self._move_item_tracked(src_target, dest_file, state)
                    else:
                        if dest_file.exists():
                            dest_file = dest_folder / f"Copy_of_{src_target.name}"
                        if src_target.is_dir():
                            self._copy_tree_tracked(src_target, dest_file, state)
                        else:
                            with self._op_lock:
                                state["current_file"] = src_target.name
                            self._copy_file_tracked(src_target, dest_file, state)
                    with self._op_lock:
                        state["completed"].append(src_rel)
                except Exception as e:
                    with self._op_lock:
                        state["errors"].append(f"Failed to {op_type} {src_rel}: {str(e)}")
                with self._op_lock:
                    state["done_items"] += 1

            with self._op_lock:
                state["status"] = "done"
        except Exception as e:
            with self._op_lock:
                state["errors"].append(str(e))
                state["status"] = "error"
        finally:
            with self._op_lock:
                state["current_item"] = ""
                state["current_file"] = ""
                state["finished_at"] = time.time()

    def _scan_totals(self, paths: List[Path]) -> tuple:
        total_bytes = 0
        total_files = 0
        for p in paths:
            try:
                if p.is_file():
                    total_bytes += p.stat().st_size
                    total_files += 1
                elif p.is_dir():
                    for root, _, files in os.walk(p):
                        for name in files:
                            try:
                                total_bytes += (Path(root) / name).stat().st_size
                                total_files += 1
                            except OSError:
                                pass
            except OSError:
                pass
        return total_bytes, total_files

    def _copy_file_tracked(self, src: Path, dst: Path, state: Dict[str, Any]) -> None:
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(self._COPY_CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                with self._op_lock:
                    state["transferred_bytes"] += len(chunk)
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
        with self._op_lock:
            state["done_files"] += 1

    def _copy_tree_tracked(self, src: Path, dst: Path, state: Dict[str, Any]) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for entry in os.scandir(src):
            child = Path(entry.path)
            target = dst / entry.name
            if entry.is_dir():
                self._copy_tree_tracked(child, target, state)
            else:
                with self._op_lock:
                    state["current_file"] = entry.name
                self._copy_file_tracked(child, target, state)
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass

    def _move_item_tracked(self, src: Path, dst: Path, state: Dict[str, Any]) -> None:
        # shutil.move semantics: moving onto an existing directory moves into it.
        if dst.exists() and dst.is_dir():
            dst = dst / src.name

        # A live transcode holds the source open and would make both the
        # same-volume rename and the cross-device delete fail on Windows.
        self._release_media_streams(src)

        # Same volume: metadata-only rename, instant regardless of size.
        try:
            os.rename(src, dst)
        except OSError:
            pass
        else:
            self._account_item_bytes(dst, state)
            return

        # Cross-device (or a Windows name collision): copy with progress,
        # then delete the source.
        if src.is_dir():
            self._copy_tree_tracked(src, dst, state)
            shutil.rmtree(src)
        else:
            with self._op_lock:
                state["current_file"] = src.name
            self._copy_file_tracked(src, dst, state)
            os.unlink(src)

    def _account_item_bytes(self, path: Path, state: Dict[str, Any]) -> None:
        """Count a same-volume rename as fully transferred: it moved no data,
        but the progress bar should still reach 100%."""
        total_bytes = 0
        total_files = 0
        try:
            if path.is_file():
                total_bytes = path.stat().st_size
                total_files = 1
            elif path.is_dir():
                for root, _, files in os.walk(path):
                    for name in files:
                        try:
                            total_bytes += (Path(root) / name).stat().st_size
                            total_files += 1
                        except OSError:
                            pass
        except OSError:
            pass
        with self._op_lock:
            state["transferred_bytes"] += total_bytes
            state["done_files"] += total_files

    def delete_items(self, rel_paths: List[str]) -> Dict[str, Any]:
        deleted = []
        errors = []
        for rel in rel_paths:
            target = self._resolve_safe_path(rel)
            if not target.exists() or target == self.root_dir:
                errors.append(f"Cannot delete {rel}")
                continue
            self._release_media_streams(target)
            try:
                if target.is_dir():
                    shutil.rmtree(str(target))
                else:
                    target.unlink()
                deleted.append(rel)
            except Exception as e:
                errors.append(f"Failed to delete {rel}: {str(e)}")

        return {"success": len(deleted) > 0, "deleted": deleted, "errors": errors}

    # Extensions that are already compressed — DEFLATE-ing them again just burns
    # CPU for ~0% size gain, which is what made multi-GB movie archives crawl.
    _PRECOMPRESSED_EXTS = {
        # video
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
        # audio
        ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma",
        # images
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif",
        # already-archived / compressed containers
        ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".br",
        # docs that are internally zipped
        ".pdf", ".docx", ".xlsx", ".pptx",
    }
    # Above this size, skip compression regardless of type: the CPU cost of
    # DEFLATE on a huge file dwarfs any realistic bandwidth/disk saving.
    _STORE_ABOVE_BYTES = 50 * 1024 * 1024  # 50 MB

    def _zip_compression_for(self, path: Path) -> int:
        """Pick STORED (fast copy) vs DEFLATED per file so large/precompressed
        media doesn't stall the whole archive on CPU-bound compression."""
        try:
            if path.suffix.lower() in self._PRECOMPRESSED_EXTS:
                return zipfile.ZIP_STORED
            if path.stat().st_size >= self._STORE_ABOVE_BYTES:
                return zipfile.ZIP_STORED
        except OSError:
            pass
        return zipfile.ZIP_DEFLATED

    def create_zip_archive(self, rel_paths: List[str]) -> Optional[str]:
        """Creates a temporary zip archive of requested paths and returns file path.

        Compression is chosen per-entry: already-compressed media and any file
        over 50 MB are STORED (no compression) so building an archive of a big
        movie is essentially I/O-bound rather than pegging a CPU core for
        minutes. Small, compressible files still get DEFLATE.
        """
        if not rel_paths:
            return None

        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        temp_zip.close()

        # allowZip64 keeps archives valid past the 4 GB / 65k-entry limits.
        with zipfile.ZipFile(temp_zip.name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for rel in rel_paths:
                target = self._resolve_safe_path(rel)
                if not target.exists():
                    continue
                if target.is_file():
                    zf.write(str(target), arcname=target.name,
                             compress_type=self._zip_compression_for(target))
                elif target.is_dir():
                    for root, _, files in os.walk(str(target)):
                        for f in files:
                            full_file = Path(root) / f
                            arcname = full_file.relative_to(target.parent)
                            zf.write(str(full_file), arcname=str(arcname),
                                     compress_type=self._zip_compression_for(full_file))
        return temp_zip.name

file_manager = FileManager()
