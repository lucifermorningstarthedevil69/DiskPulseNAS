import os
import asyncio
import json
import socket
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from pydantic import BaseModel

from backend.config import STORAGE_ROOT, FRONTEND_DIR, TELEMETRY_INTERVAL_SECS, DEBUG, PORT, folder_for_extension
from backend.telemetry import telemetry_engine
from backend.file_manager import file_manager
from backend.download_engine import download_manager
from backend.history_service import (
    load_history, clear_history, get_retention_days, set_retention_days,
    record_download, record_upload,
)
from backend.terminal_emulator import get_or_create_session, sessions
from backend.nas_generator import nas_generator
from backend.setup_manager import (
    get_available_drives, apply_setup, is_setup_complete,
    reset_setup, load_config
)
from backend.speedtest_service import speedtest_manager, run_speed_test, quick_ping_test


app = FastAPI(
    title="DiskPulse NAS Hub",
    description="Full-stack self-hosted NAS storage monitor, file manager, download engine & media hub",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def _shutdown_kill_streams():
    """Kill any ffmpeg transcodes still running when the server stops, so no
    source file stays locked and shutdown doesn't hang on a dangling stream."""
    try:
        from backend.media_service import terminate_all_streams
        n = terminate_all_streams()
        if n:
            print(f"Stopped {n} active media stream(s) on shutdown.")
    except Exception:
        pass
    try:
        telemetry_engine.cleanup()
    except Exception:
        pass


@app.on_event("startup")
async def _silence_windows_disconnect_noise():
    """On Windows the proactor event loop logs a scary traceback every time a
    remote end (a browser tab reloading, a killed ffmpeg child) drops a
    connection before we close our side: a ConnectionResetError raised inside
    ``_ProactorBasePipeTransport._call_connection_lost``. It is harmless
    disconnect noise, not an application error — swallow exactly that case and
    keep the default handling for everything else."""
    if os.name != "nt":
        return
    loop = asyncio.get_running_loop()

    def _handler(lp, context):
        exc = context.get("exception")
        message = context.get("message") or ""
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)) \
                and "_call_connection_lost" in message:
            return
        lp.default_exception_handler(context)

    loop.set_exception_handler(_handler)

# ----------------- Request Models -----------------
class MkdirRequest(BaseModel):
    parent_path: str = ""
    folder_name: str

class CreateFileRequest(BaseModel):
    parent_path: str = ""
    file_name: str
    content: str = ""

class WriteFileRequest(BaseModel):
    path: str
    content: str

class RenameRequest(BaseModel):
    path: str
    new_name: str

class MoveCopyRequest(BaseModel):
    source_paths: List[str]
    target_folder: str = ""

class DeleteRequest(BaseModel):
    paths: List[str]

class BatchZipRequest(BaseModel):
    paths: List[str]

class AddDownloadRequest(BaseModel):
    url: str
    category: Optional[str] = None
    custom_folder: str = ""
    custom_filename: Optional[str] = None
    backend: str = "auto"
    mode: str = "video"            # video | audio
    max_height: str = "best"       # best | 2160 | 1440 | 1080 | 720 | 480 | 360
    audio_format: str = "mp3"      # mp3 | m4a | opus | flac | wav
    audio_bitrate: str = "192"     # 320 | 256 | 192 | 128 | 96
    format_id: str = ""            # exact yt-dlp stream id picked from the probe
    progressive: bool = False      # True if that stream is already muxed (video+audio)
    sort_by_type: bool = True      # drop the finished file into a type subfolder
    meta_title: str = ""           # probed media title, shown on the task card
    meta_thumbnail: str = ""       # probed media thumbnail URL, shown on the card
    segments: int = 4              # parallel HTTP connections (1-16, default 4)

class ProbeRequest(BaseModel):
    url: str

class TerminalExecRequest(BaseModel):
    session_id: str = "default"
    command: str

# ----------------- Telemetry Routes -----------------
@app.get("/api/telemetry")
async def get_telemetry():
    return telemetry_engine.get_system_overview()

@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = telemetry_engine.get_system_overview()
            await websocket.send_json(data)
            await asyncio.sleep(TELEMETRY_INTERVAL_SECS)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

# ----------------- Server Info Routes -----------------
def _lan_ip() -> str:
    """Best-effort LAN address other devices can reach this server on.

    Used by the web player's cast button: a cast target fetches media URLs
    itself, so a page opened on localhost needs the server's network address.
    The UDP-connect trick picks the OS routing address without sending data.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"

@app.get("/api/server/info")
async def get_server_info():
    return {"host": _lan_ip(), "port": PORT}

# ----------------- File Manager Routes -----------------
@app.get("/api/files/list")
async def list_files(path: str = ""):
    return file_manager.list_directory(path)

@app.get("/api/files/search")
async def search_files(query: str = "", path: str = ""):
    return file_manager.search_files(query, path)

@app.post("/api/files/mkdir")
async def make_directory(req: MkdirRequest):
    res = file_manager.create_directory(req.parent_path, req.folder_name)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.post("/api/files/create")
async def create_file(req: CreateFileRequest):
    res = file_manager.create_file(req.parent_path, req.file_name, req.content)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.get("/api/files/read")
async def read_file(path: str):
    res = file_manager.read_file_content(path)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.post("/api/files/write")
async def write_file(req: WriteFileRequest):
    res = file_manager.write_file_content(req.path, req.content)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.post("/api/files/rename")
async def rename_file(req: RenameRequest):
    res = file_manager.rename_item(req.path, req.new_name)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.post("/api/files/move")
async def move_files(req: MoveCopyRequest):
    # Returns immediately with an op_id; the actual move runs on a worker
    # thread and reports live progress via /api/files/operation/{op_id}.
    res = file_manager.start_move(req.source_paths, req.target_folder)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.post("/api/files/copy")
async def copy_files(req: MoveCopyRequest):
    res = file_manager.start_copy(req.source_paths, req.target_folder)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@app.get("/api/files/operation/{op_id}")
async def get_file_operation(op_id: str):
    state = file_manager.get_transfer_status(op_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown or expired operation")
    return state

@app.post("/api/files/delete")
async def delete_files(req: DeleteRequest):
    return file_manager.delete_items(req.paths)

def _cleanup_file(path: str):
    """Best-effort delete of a temp archive once it has been streamed out."""
    try:
        os.remove(path)
    except OSError:
        pass


@app.post("/api/files/zip")
async def create_zip(req: BatchZipRequest):
    zip_path = file_manager.create_zip_archive(req.paths)
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=400, detail="Failed to create zip archive")
    return FileResponse(
        zip_path,
        filename="diskpulse_archive.zip",
        media_type="application/zip",
        background=BackgroundTask(_cleanup_file, zip_path),
    )


@app.post("/api/files/zip-download")
async def create_zip_download(paths: List[str] = Form(...)):
    """Form-based twin of /api/files/zip.

    The frontend submits a hidden <form> here (targeting an off-screen iframe)
    instead of fetch()+blob(). The browser then streams the archive straight to
    disk, so multi-GB selections no longer have to fit in a JS Blob in RAM.
    """
    zip_path = file_manager.create_zip_archive(paths)
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=400, detail="Failed to create zip archive")
    return FileResponse(
        zip_path,
        filename="diskpulse_archive.zip",
        media_type="application/zip",
        background=BackgroundTask(_cleanup_file, zip_path),
    )

@app.get("/api/files/download")
async def download_file(path: str):
    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        str(target),
        filename=target.name,
        media_type="application/octet-stream"
    )

@app.get("/api/files/raw")
async def stream_raw_file(path: str):
    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    info = file_manager._get_file_info(target)
    return FileResponse(
        str(target),
        media_type=info["mime"],
        content_disposition_type="inline"
    )

# ----------------- Multi-Device Uploader Routes -----------------
def _truthy(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@app.post("/api/upload")
async def upload_files(
    target_folder: str = Form(""),
    sort_by_type: str = Form("true"),
    files: List[UploadFile] = File(...)
):
    """Save uploaded files under the storage root.

    Two organising modes:
      • sort_by_type ON  → each file is dropped into a type subfolder
        (Images / Video / Audio / … / Other) nested inside ``target_folder``.
      • sort_by_type OFF → the file's own relative path is preserved, so an
        uploaded folder keeps its structure. Folder uploads arrive with the
        relative path in ``UploadFile.filename`` (the browser's
        webkitRelativePath, sent as the multipart filename).

    Every destination is re-validated through ``_resolve_safe_path`` so a
    crafted relative path can never escape the storage root.
    """
    sort = _truthy(sort_by_type)
    root = str(file_manager.root_dir)
    base_rel = (target_folder or "").strip().strip("/\\")

    uploaded_files = []
    skipped = []
    for f in files:
        # Folder uploads carry a relative path ("MyFolder/sub/file.jpg");
        # normalise separators and split off the basename.
        raw = (f.filename or "uploaded_file").replace("\\", "/")
        basename = os.path.basename(raw)
        if not basename or basename in (".", ".."):
            basename = "uploaded_file"
        ext = os.path.splitext(basename)[1]

        if sort:
            # Ignore any original structure; route purely by file type.
            sub_rel = folder_for_extension(ext)
        else:
            # Preserve the uploaded folder's structure.
            sub_rel = os.path.dirname(raw)

        combined_rel = "/".join(p for p in (base_rel, sub_rel) if p)
        dest_dir = file_manager._resolve_safe_path(combined_rel)
        dest_dir.mkdir(parents=True, exist_ok=True)

        file_path = dest_dir / basename
        # Handle collision by appending counter
        counter = 1
        stem, fext = os.path.splitext(basename)
        while file_path.exists():
            file_path = dest_dir / f"{stem}_{counter}{fext}"
            counter += 1

        # Final belt-and-braces traversal guard on the fully-resolved path.
        if not str(file_path.resolve()).startswith(root):
            skipped.append(raw)
            continue

        contents = await f.read()
        file_path.write_bytes(contents)
        uploaded_files.append(file_manager._get_file_info(file_path))

    # Record upload history (best-effort, never fails the upload response)
    try:
        for uf in uploaded_files:
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, record_upload,
                uf.get("name", ""),
                uf.get("size", 0),
                "completed",
                str(Path(uf.get("path", "")).parent).replace("\\", "/"),
                "browser",
                0.0,
            )
    except Exception:
        pass

    return {
        "success": True,
        "uploaded": uploaded_files,
        "count": len(uploaded_files),
        "skipped": skipped,
    }

# ----------------- Download Manager Routes -----------------
@app.get("/api/downloads")
async def get_downloads():
    return download_manager.list_all()

@app.post("/api/downloads/add")
async def add_download(req: AddDownloadRequest):
    task = download_manager.add_download(
        url=req.url,
        category=req.category,
        custom_folder=req.custom_folder,
        custom_filename=req.custom_filename,
        backend=req.backend,
        mode=req.mode,
        max_height=req.max_height,
        audio_format=req.audio_format,
        audio_bitrate=req.audio_bitrate,
        format_id=req.format_id,
        progressive=req.progressive,
        sort_by_type=req.sort_by_type,
        meta_title=req.meta_title,
        meta_thumbnail=req.meta_thumbnail,
        segments=req.segments,
    )
    return task.to_dict()

@app.post("/api/downloads/probe")
async def probe_download(req: ProbeRequest):
    """Inspect a media URL and report the qualities that actually exist for it.

    Runs in a thread because yt-dlp extraction is blocking network I/O and would
    otherwise stall the event loop (and the 1s telemetry stream).
    """
    from backend.ytdlp_service import probe_formats, friendly_error

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, probe_formats, req.url.strip())
        return {"success": True, **data}
    except Exception as e:
        return {"success": False, "error": friendly_error(e)}

@app.get("/api/downloads/ytdlp-version")
async def get_ytdlp_version():
    """Installed yt-dlp version, staleness warning, ffmpeg + cookie availability."""
    from backend.ytdlp_service import ytdlp_version_info, cookie_status

    loop = asyncio.get_event_loop()
    info = ytdlp_version_info()
    # Cookie detection reads browser DBs, so keep it off the event loop.
    info["cookies"] = await loop.run_in_executor(None, cookie_status)
    return info

@app.post("/api/downloads/ytdlp-update")
async def post_ytdlp_update():
    """Run `pip install -U yt-dlp` — the usual fix for YouTube bot-check errors."""
    from backend.ytdlp_service import update_ytdlp

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, update_ytdlp)

@app.post("/api/downloads/pause/{task_id}")
async def pause_download(task_id: str):
    success = download_manager.pause_task(task_id)
    return {"success": success}

@app.post("/api/downloads/resume/{task_id}")
async def resume_download(task_id: str):
    success = download_manager.resume_task(task_id)
    return {"success": success}

@app.post("/api/downloads/cancel/{task_id}")
async def cancel_download(task_id: str):
    success = download_manager.cancel_task(task_id)
    return {"success": success}

@app.post("/api/downloads/retry/{task_id}")
async def retry_download(task_id: str):
    success = download_manager.retry_task(task_id)
    return {"success": success}

@app.delete("/api/downloads/{task_id}")
async def delete_download(task_id: str, delete_file: bool = False):
    success = download_manager.delete_task(task_id, delete_file)
    return {"success": success}

# ----------------- Transfer History Routes -----------------

@app.get("/api/history")
async def get_history():
    """Return transfer history entries, newest first."""
    history = load_history()
    return {"history": list(reversed(history))}

@app.delete("/api/history")
async def delete_history():
    """Clear all transfer history."""
    clear_history()
    return {"success": True}

@app.get("/api/history/settings")
async def get_history_settings():
    """Return current retention setting."""
    return {"retention_days": get_retention_days()}

@app.post("/api/history/settings")
async def post_history_settings(retention_days: int = 7):
    """Update retention window (in days)."""
    val = set_retention_days(retention_days)
    return {"retention_days": val}

# ----------------- Web Media Player Routes -----------------
# Plain HTML5 <video> can't switch embedded audio tracks or render embedded
# subtitles, so these endpoints use ffmpeg/ffprobe to introspect a file and
# (when needed) remux/transcode it on demand with a chosen audio track.

@app.get("/api/media/info")
async def media_info(path: str):
    """List a media file's audio/subtitle/video tracks + any sidecar subs."""
    from backend.media_service import probe_media, find_external_subs, has_ffmpeg, has_ffprobe

    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, probe_media, str(target))
    info["external_subs"] = await loop.run_in_executor(None, find_external_subs, str(target))
    info["ffmpeg"] = has_ffmpeg()
    info["ffprobe"] = has_ffprobe()
    return info


@app.get("/api/media/thumb")
async def media_thumb(path: str, t: float = 0.0, w: int = 200):
    """Return a small JPEG frame at ``t`` seconds — powers the scrub-bar hover preview."""
    from backend.media_service import grab_thumbnail, has_ffmpeg

    if not has_ffmpeg():
        raise HTTPException(status_code=503, detail="ffmpeg is not installed on the server.")

    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    loop = asyncio.get_event_loop()
    img = await loop.run_in_executor(None, grab_thumbnail, str(target), t, w)
    if not img:
        raise HTTPException(status_code=422, detail="Could not extract a frame at this position.")
    return Response(content=img, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/media/subtitle")
async def media_subtitle(
    path: str,
    kind: str = "embedded",     # embedded | external
    track: int = 0,             # embedded: type-relative subtitle index
    file: str = "",             # external: sidecar filename (same dir as video)
    offset: float = 0.0,        # shift cues earlier (matches a seeked stream's -ss)
):
    """Return a subtitle as WebVTT so it can be attached via <track>."""
    from backend.media_service import extract_subtitle_vtt, convert_external_sub_vtt, shift_vtt

    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    loop = asyncio.get_event_loop()
    if kind == "external":
        # Sidecar lives beside the video; keep it inside the storage root.
        sub_path = (target.parent / os.path.basename(file)).resolve()
        if not str(sub_path).startswith(str(file_manager.root_dir)) or not sub_path.is_file():
            raise HTTPException(status_code=404, detail="Subtitle file not found")
        vtt = await loop.run_in_executor(None, convert_external_sub_vtt, str(sub_path))
    else:
        vtt = await loop.run_in_executor(None, extract_subtitle_vtt, str(target), track)

    if not vtt:
        raise HTTPException(status_code=422, detail="Could not produce WebVTT for this subtitle (it may be image-based).")
    if offset and offset > 0:
        vtt = await loop.run_in_executor(None, shift_vtt, vtt, offset)
    return Response(content=vtt, media_type="text/vtt; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/media/stream")
async def media_stream(
    path: str,
    audio: int = 0,            # type-relative audio track index to play
    t: float = 0.0,            # start offset in seconds (for seeking)
    vcodec: str = "",          # optional hint from /info to skip a re-probe
    max_height: Optional[int] = None,  # optional playback quality cap (e.g. 1080)
):
    """Stream the file as fragmented MP4 with the chosen audio track.

    Video is copied when it's already H.264 (cheap), otherwise transcoded.
    Used whenever the container/codec isn't browser-native or the user picks a
    non-default audio track.
    """
    from backend.media_service import stream_transcode, probe_media, has_ffmpeg

    if not has_ffmpeg():
        raise HTTPException(status_code=503, detail="ffmpeg is not installed on the server.")

    target = file_manager._resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if not vcodec:
        loop = asyncio.get_event_loop()
        probed = await loop.run_in_executor(None, probe_media, str(target))
        vcodec = ((probed.get("video") or {}).get("codec") or "") if probed.get("ok") else ""

    generator = stream_transcode(str(target), audio_rel_index=audio,
                                 start_time=t, video_codec=vcodec,
                                 max_height=max_height)
    return StreamingResponse(generator, media_type="video/mp4",
                             headers={"Cache-Control": "no-store"})


# ----------------- Speed Test Routes -----------------
@app.post("/api/speedtest/run")
async def trigger_speedtest():
    return await run_speed_test()

@app.get("/api/speedtest/latest")
async def get_latest_speedtest():
    return speedtest_manager.get_status()

@app.get("/api/speedtest/ping")
async def get_quick_ping():
    return await quick_ping_test()

# ----------------- Embedded Terminal Routes -----------------
@app.post("/api/terminal/exec")
async def terminal_exec(req: TerminalExecRequest):
    session = get_or_create_session(req.session_id)
    result = session.execute(req.command)
    return result

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await websocket.accept()
    session_id = str(id(websocket))
    session = get_or_create_session(session_id)
    
    # Send welcome banner
    banner_result = session.execute("diskpulse")
    await websocket.send_json({
        "output": banner_result["output"] + f"Interactive NAS Shell initialized.\nType 'help' for commands.\n",
        "cwd": session.get_prompt_path(),
        "exit_code": 0
    })

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            cmd = data.get("command", "")
            res = session.execute(cmd)
            await websocket.send_json(res)
    except WebSocketDisconnect:
        sessions.pop(session_id, None)
    except Exception:
        sessions.pop(session_id, None)

# ----------------- NAS Generator & Exporter -----------------
@app.get("/api/nas/export")
async def export_nas_config(
    platform_type: str = Query("docker", enum=["docker", "systemd", "synology", "truenas", "installer"]),
    storage_path: str = "/mnt/storage",
    port: int = 8000
):
    if platform_type == "docker":
        content = nas_generator.generate_docker_compose(storage_path, port)
        filename = "docker-compose.yml"
        mime = "text/yaml"
    elif platform_type == "systemd":
        content = nas_generator.generate_systemd_service("root", "/opt/diskpulse", storage_path)
        filename = "diskpulse.service"
        mime = "text/plain"
    elif platform_type == "synology":
        content = nas_generator.generate_synology_script(storage_path, port)
        filename = "diskpulse-synology.sh"
        mime = "application/x-sh"
    elif platform_type == "truenas":
        content = nas_generator.generate_truenas_scale_config(storage_path.lstrip("/mnt/"), port)
        filename = "truenas-diskpulse.yaml"
        mime = "text/yaml"
    else:
        content = nas_generator.generate_install_sh()
        filename = "install-nas.sh"
        mime = "application/x-sh"

    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ----------------- Setup API Routes -----------------

class SetupConfigRequest(BaseModel):
    storage_root: str
    seed_demo_data: bool = True
    port: int = 8000


@app.get("/api/setup/status")
async def setup_status():
    cfg = load_config()
    import platform
    return {
        "setup_complete": cfg.get("setup_complete", False),
        "storage_root": cfg.get("storage_root", ""),
        "seed_demo_data": cfg.get("seed_demo_data", True),
        "os": platform.system(),
        "os_release": platform.release(),
    }


@app.get("/api/setup/drives")
async def list_drives():
    drives = get_available_drives()
    return {"drives": drives}


@app.post("/api/setup/configure")
async def configure_setup(req: SetupConfigRequest):
    result = apply_setup(
        storage_root=req.storage_root,
        seed_demo_data=req.seed_demo_data,
        port=req.port,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Setup failed"))

    new_root = result["storage_root"]

    # ── Propagate the newly chosen storage root to every already-running
    # singleton / module. These were all instantiated at server startup
    # using the OLD default root, so without this step the wizard's
    # drive/path choice (and the "fresh start" option) silently has no
    # effect until the whole process is restarted.
    import backend.config as _cfg_mod
    _cfg_mod.STORAGE_ROOT = new_root

    import backend.download_engine as _download_engine_mod
    _download_engine_mod.STORAGE_ROOT = new_root

    import backend.telemetry as _telemetry_mod
    _telemetry_mod.STORAGE_ROOT = new_root

    # file_manager is a long-lived singleton with the root baked into
    # self.root_dir at construction time - repoint it directly.
    file_manager.set_root(new_root)

    # Seed demo data if requested - pass the chosen path explicitly so it
    # always lands in the wizard-selected directory, never the repo's
    # own default folder.
    if req.seed_demo_data:
        try:
            from generate_demo_data import generate_sample_storage
            generate_sample_storage(new_root)
        except Exception as e:
            print(f"Demo data seeding warning: {e}")

    return result


@app.post("/api/setup/reset")
async def reset_configuration():
    reset_setup()
    return {"success": True, "message": "Setup reset. Reload the page to run the setup wizard again."}


# ----------------- Root Redirect for Setup -----------------
@app.get("/")
async def root_handler():
    """Serve setup.html if not yet configured, else the main app."""
    # no-cache so freshly-edited markup (new buttons, etc.) is always served.
    no_cache = {"Cache-Control": "no-cache"}
    if not is_setup_complete():
        setup_file = FRONTEND_DIR / "setup.html"
        if setup_file.exists():
            return FileResponse(str(setup_file), headers=no_cache)
    main_file = FRONTEND_DIR / "index.html"
    if main_file.exists():
        return FileResponse(str(main_file), headers=no_cache)
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


# ----------------- Static Frontend Assets (CSS / JS / fonts) -----------------
class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that asks browsers to always revalidate before reusing a file.

    Sends ``Cache-Control: no-cache`` so an edited CSS/JS file is picked up on
    the next reload instead of a stale copy being served silently from the
    browser's heuristic cache. ETag / Last-Modified revalidation (304) still
    works, so this stays cheap on the wire while guaranteeing freshness during
    active development.
    """
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mount the frontend directory under /static so CSS/JS can be found,
# AND also mount at root so /css/... and /js/... paths work directly.
if FRONTEND_DIR.exists():
    app.mount("/css",    NoCacheStaticFiles(directory=str(FRONTEND_DIR / "css")), name="fe_css")
    app.mount("/js",     NoCacheStaticFiles(directory=str(FRONTEND_DIR / "js")),  name="fe_js")
    # Catch-all for any other static assets (images, fonts, etc.)
    app.mount("/assets", NoCacheStaticFiles(directory=str(FRONTEND_DIR)),         name="fe_assets")
    # Optionally vendored third-party libraries (PDF.js). Absent by default: the
    # frontend probes /vendor first and falls back to a CDN, so this mount is
    # what makes a fully offline / air-gapped install possible.
    # See frontend/vendor/pdfjs/README.md.
    vendor_dir = FRONTEND_DIR / "vendor"
    if vendor_dir.exists():
        app.mount("/vendor", NoCacheStaticFiles(directory=str(vendor_dir)),       name="fe_vendor")
