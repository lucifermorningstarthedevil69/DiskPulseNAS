"""
DiskPulse media introspection & on-demand transcoding service.

The web media player is a plain HTML5 <video>, which on its own cannot:
  * switch between multiple embedded audio tracks (dual-audio MKV), or
  * render subtitles that live *inside* the container (embedded SRT/ASS/PGS).

This module bridges that gap with ffmpeg/ffprobe:
  * probe_media()          -> lists video/audio/subtitle tracks
  * find_external_subs()   -> sidecar .srt/.vtt/.ass next to the video
  * extract_subtitle_vtt() -> pull one embedded subtitle stream out as WebVTT
  * convert_external_sub_vtt() -> normalise a sidecar file to WebVTT
  * stream_transcode()     -> remux/transcode to fragmented MP4 with a chosen
                              audio track (video is *copied* when it is already
                              H.264, so the common case is nearly free)

All ffmpeg/ffprobe calls are plain subprocess invocations (no shell), so they
work the same on Windows, Linux and macOS. Paths must already be resolved and
safety-checked by the caller (file_manager._resolve_safe_path).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional


_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


# ---- capability detection -------------------------------------------------

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


# ---- classification tables ------------------------------------------------

# Subtitle codecs we can turn into WebVTT. Bitmap subs (PGS/VobSub/DVB) are
# images, so they can only be burned in, not shown as a selectable text track.
_TEXT_SUB_CODECS = {
    "subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "vtt", "text",
    "stl", "eia_608", "subviewer", "microdvd",
}
_BITMAP_SUB_CODECS = {
    "hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
    "dvb_subtitle", "dvbsub", "xsub",
}

# Sidecar subtitle extensions we look for next to a video file.
_EXTERNAL_SUB_EXTS = (".srt", ".vtt", ".ass", ".ssa", ".sub")

# Container + codec combos a browser can generally play without transcoding.
_BROWSER_CONTAINERS = {".mp4", ".m4v", ".webm", ".ogg", ".ogv"}
_BROWSER_VIDEO_CODECS = {"h264", "avc1", "vp8", "vp9", "av1"}
_BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis"}


def _lang_label(tags: Dict[str, Any], fallback: str) -> str:
    lang = (tags.get("language") or tags.get("LANGUAGE") or "").strip()
    title = (tags.get("title") or tags.get("TITLE") or "").strip()
    if title and lang:
        return f"{title} ({lang})"
    if title:
        return title
    if lang and lang.lower() not in ("und", "unknown"):
        return lang
    return fallback


# ---- probing --------------------------------------------------------------

def probe_media(abs_path: str) -> Dict[str, Any]:
    """Run ffprobe and return a normalised description of the file's tracks.

    Audio/subtitle entries carry a *type-relative* index (``rel_index``) — the
    N used by ffmpeg's ``-map 0:a:N`` / ``0:s:N`` — which is what the stream and
    subtitle endpoints need, not the absolute stream index.
    """
    if not has_ffprobe():
        return {"ok": False, "error": "ffprobe not found. Install ffmpeg to use advanced playback."}

    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        abs_path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=_CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "error": f"ffprobe failed: {e}"}
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "ffprobe error").strip()[:300]}

    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "Could not parse ffprobe output."}

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    video: Optional[Dict[str, Any]] = None
    audio: List[Dict[str, Any]] = []
    subtitles: List[Dict[str, Any]] = []
    a_rel = 0
    s_rel = 0

    for st in streams:
        ctype = st.get("codec_type")
        disp = st.get("disposition", {}) or {}
        tags = st.get("tags", {}) or {}
        if ctype == "video":
            # Skip cover-art / thumbnail "video" streams.
            if disp.get("attached_pic"):
                continue
            if video is None:
                video = {
                    "codec": st.get("codec_name"),
                    "width": st.get("width"),
                    "height": st.get("height"),
                }
        elif ctype == "audio":
            audio.append({
                "rel_index": a_rel,
                "codec": st.get("codec_name"),
                "channels": st.get("channels"),
                "label": _lang_label(tags, f"Audio track {a_rel + 1}"),
                "default": bool(disp.get("default")),
            })
            a_rel += 1
        elif ctype == "subtitle":
            codec = (st.get("codec_name") or "").lower()
            subtitles.append({
                "rel_index": s_rel,
                "codec": codec,
                "text_based": codec in _TEXT_SUB_CODECS,
                "bitmap": codec in _BITMAP_SUB_CODECS,
                "label": _lang_label(tags, f"Subtitle {s_rel + 1}"),
                "default": bool(disp.get("default")),
            })
            s_rel += 1

    duration = None
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        duration = None

    ext = Path(abs_path).suffix.lower()
    v_ok = bool(video) and (video.get("codec") or "").lower() in _BROWSER_VIDEO_CODECS
    default_audio = next((a for a in audio if a["default"]), audio[0] if audio else None)
    a_ok = (default_audio is None) or ((default_audio.get("codec") or "").lower() in _BROWSER_AUDIO_CODECS)
    direct_play = (ext in _BROWSER_CONTAINERS) and v_ok and a_ok

    return {
        "ok": True,
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
        "duration": duration,
        "direct_play": direct_play,
    }


def find_external_subs(abs_path: str) -> List[Dict[str, str]]:
    """Find sidecar subtitle files that share the video's stem.

    Matches ``movie.srt`` as well as language-tagged ``movie.en.srt`` /
    ``movie.eng.vtt`` variants sitting in the same folder.
    """
    p = Path(abs_path)
    parent = p.parent
    stem = p.stem  # filename without final extension
    found: List[Dict[str, str]] = []
    if not parent.is_dir():
        return found
    try:
        for f in parent.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in _EXTERNAL_SUB_EXTS:
                continue
            fstem = f.stem
            # exact stem, or "stem.lang" form
            if fstem == stem or fstem.startswith(stem + "."):
                suffix_part = fstem[len(stem):].lstrip(".")
                label = suffix_part if suffix_part else "External"
                found.append({
                    "file": f.name,          # returned relative to the video's dir
                    "label": f"{label} · {f.suffix.lstrip('.').upper()}",
                })
    except OSError:
        pass
    return found


# ---- subtitle conversion --------------------------------------------------

def extract_subtitle_vtt(abs_path: str, rel_index: int) -> Optional[bytes]:
    """Extract embedded subtitle stream ``rel_index`` as WebVTT bytes."""
    if not has_ffmpeg():
        return None
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", abs_path,
        "-map", f"0:s:{rel_index}",
        "-f", "webvtt", "pipe:1",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return out.stdout


def convert_external_sub_vtt(sub_abs_path: str) -> Optional[bytes]:
    """Return a sidecar subtitle file as WebVTT (passthrough if already .vtt)."""
    p = Path(sub_abs_path)
    if not p.is_file():
        return None
    if p.suffix.lower() == ".vtt":
        try:
            return p.read_bytes()
        except OSError:
            return None
    if not has_ffmpeg():
        return None
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", sub_abs_path,
        "-f", "webvtt", "pipe:1",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return out.stdout


# ---- VTT time-shifting (keeps subs aligned on seeked transcode segments) ---

def _parse_vtt_ts(s: str) -> int:
    """Parse a WebVTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to milliseconds."""
    s = s.strip().replace(",", ".")  # tolerate SRT-style commas
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
        elif len(parts) == 2:
            h, m, sec = 0, int(parts[0]), float(parts[1])
        else:
            return 0
    except ValueError:
        return 0
    return int(round((h * 3600 + m * 60 + sec) * 1000))


def _fmt_vtt_ts(total_ms: int) -> str:
    if total_ms < 0:
        total_ms = 0
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def shift_vtt(data: bytes, offset_seconds: float) -> bytes:
    """Move every cue in a WebVTT payload earlier by ``offset_seconds``.

    When a transcoded stream is (re)started partway through the film with
    ffmpeg ``-ss``, the browser's media timeline is 0-based for that segment,
    so the absolute subtitle cues must be shifted back by the same amount to
    stay in sync. Cues ending before the new zero are dropped; a cue straddling
    it is clamped to start at 0.
    """
    if not offset_seconds or offset_seconds <= 0:
        return data
    off_ms = int(round(offset_seconds * 1000))
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = text.split("\n\n")
    out_blocks: List[str] = []

    for block in blocks:
        lines = block.split("\n")
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if timing_idx < 0:
            # WEBVTT header, NOTE, STYLE, REGION, or stray block — keep verbatim.
            out_blocks.append(block)
            continue

        left, _, right = lines[timing_idx].partition("-->")
        end_ts, _, settings = right.strip().partition(" ")
        start_ms = _parse_vtt_ts(left) - off_ms
        end_ms = _parse_vtt_ts(end_ts) - off_ms
        if end_ms <= 0:
            continue  # entirely before the new zero → drop the cue
        if start_ms < 0:
            start_ms = 0
        timing = f"{_fmt_vtt_ts(start_ms)} --> {_fmt_vtt_ts(end_ms)}"
        if settings:
            timing += f" {settings}"
        lines[timing_idx] = timing
        out_blocks.append("\n".join(lines))

    result = "\n\n".join(out_blocks)
    if not result.lstrip().startswith("WEBVTT"):
        result = "WEBVTT\n\n" + result
    return result.encode("utf-8")


# ---- scrub-bar thumbnail previews ------------------------------------------

def grab_thumbnail(abs_path: str, t: float, width: int = 200) -> Optional[bytes]:
    """Extract a single JPEG frame at ``t`` seconds for seek-bar hover previews.

    Uses input-side seeking (``-ss`` before ``-i``) so the grab is fast even on
    long films, and scales down to ``width`` (height keeps aspect, rounded to an
    even number) so each preview is only a few KB.
    """
    if not has_ffmpeg():
        return None
    width = max(96, min(int(width or 200), 480))
    start = max(0.0, float(t or 0.0))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", abs_path,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "5",
        "-f", "image2", "-c:v", "mjpeg",
        "pipe:1",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return out.stdout


# ---- transcoding / remuxing ----------------------------------------------

def build_transcode_cmd(
    abs_path: str,
    audio_rel_index: int = 0,
    start_time: float = 0.0,
    video_codec: Optional[str] = None,
    max_height: Optional[int] = None,
) -> List[str]:
    """Assemble the ffmpeg command that streams a fragmented MP4 to stdout.

    Video is *copied* when it is already H.264 **and** we are starting from the
    beginning (cheap remux — the usual "just play this dual-audio MKV" case);
    anything else, including any seeked/resumed segment (``start_time > 0``), is
    transcoded to H.264 so it begins exactly on the requested time. The chosen
    audio track is always re-encoded to stereo AAC so it plays regardless of the
    source codec (AC3/DTS/TrueHD/etc.). ``start_time`` uses fast input seeking so
    the transcoded stream stays seekable via re-requests.

    When ``max_height`` is provided the video is scaled to that height (width
    auto-calculated, rounded to an even number) so the player can offer YouTube-
    style quality switching (360p … 4K).
    """
    vcodec = (video_codec or "").lower()
    seeking = bool(start_time and start_time > 0)
    needs_reencode = vcodec not in ("h264", "avc1") or seeking or bool(max_height)

    cmd: List[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if seeking:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += ["-i", abs_path]
    cmd += ["-map", "0:v:0", "-map", f"0:a:{audio_rel_index}"]

    if needs_reencode:
        vf_parts: List[str] = []
        if max_height and max_height > 0:
            vf_parts.append(f"scale=-2:{int(max_height)}")
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p"]
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
    else:
        cmd += ["-c:v", "copy"]

    cmd += [
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-sn",  # subtitles handled separately as <track>s
        "-max_muxing_queue_size", "1024",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    return cmd


# --- Live ffmpeg process registry --------------------------------------------
# Every transcode subprocess is tracked here (with the absolute source path it
# is reading) so it can be killed from outside the streaming task:
#   * terminate_all_streams()  — server shutdown (Ctrl+C) releases every file
#     and lets the in-flight streaming responses finish so uvicorn can exit.
#   * stop_streams_for_path()  — file operations (rename/move/delete) release
#     a specific file first; ffmpeg opens it with a sharing mode that makes
#     Windows refuse those operations ("file is in use by ffmpeg") while a
#     stream is alive, and a stream can outlive the player view that started
#     it (another tab, a paused video keeping its buffer connection open).
_ACTIVE_PROCS: "Dict[asyncio.subprocess.Process, str]" = {}
_PROCS_LOCK = threading.Lock()


def _register_proc(proc: "asyncio.subprocess.Process", abs_path: str) -> None:
    with _PROCS_LOCK:
        _ACTIVE_PROCS[proc] = os.path.normcase(os.path.abspath(abs_path))


def _unregister_proc(proc: "asyncio.subprocess.Process") -> None:
    with _PROCS_LOCK:
        _ACTIVE_PROCS.pop(proc, None)


def _kill_procs(procs: List["asyncio.subprocess.Process"], wait_seconds: float = 0.0) -> int:
    """Kill the given processes; optionally block until they have actually
    exited so the OS drops their file handles before the caller touches the
    files. Safe to call from any thread. Returns how many were still running."""
    killed: List["asyncio.subprocess.Process"] = []
    for proc in procs:
        try:
            if proc.returncode is None:
                proc.kill()
                killed.append(proc)
        except Exception:
            pass
    if wait_seconds > 0 and killed:
        deadline = time.monotonic() + wait_seconds
        for proc in killed:
            # returncode is filled in by the event loop's process watcher once
            # the kill takes effect; poll it briefly from this thread.
            while proc.returncode is None and time.monotonic() < deadline:
                time.sleep(0.05)
    return len(killed)


def terminate_all_streams() -> int:
    """Kill every live transcode process. Returns how many were still running.

    Called on server shutdown so ffmpeg releases the source files and the
    streaming responses' ``await read()`` returns EOF, letting their generators
    finish and uvicorn exit within the graceful-shutdown timeout."""
    with _PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
        _ACTIVE_PROCS.clear()
    return _kill_procs(procs)


def stop_streams_for_path(abs_path: str) -> int:
    """Kill any transcode reading ``abs_path`` (or anything inside it, when it
    is a folder). Returns how many processes were killed.

    Waits briefly for the kills to take effect so a rename/move/delete issued
    right after doesn't still hit the Windows file lock."""
    norm = os.path.normcase(os.path.abspath(abs_path))
    with _PROCS_LOCK:
        victims = [proc for proc, src in _ACTIVE_PROCS.items()
                   if src == norm or src.startswith(norm + os.sep)]
    return _kill_procs(victims, wait_seconds=2.0)


async def stream_transcode(
    abs_path: str,
    audio_rel_index: int = 0,
    start_time: float = 0.0,
    video_codec: Optional[str] = None,
    max_height: Optional[int] = None,
    chunk_size: int = 256 * 1024,
) -> AsyncIterator[bytes]:
    """Yield fragmented-MP4 bytes from ffmpeg for StreamingResponse.

    This is an *async* generator on purpose: the ffmpeg read is an awaitable,
    so when the client goes away (seek to a new offset, player closed, tab
    closed, server shutdown) the framework's cancellation lands directly inside
    the ``await read()`` and the ``finally`` block kills ffmpeg immediately —
    releasing the source file. The old sync-generator version did a blocking
    ``read()`` on a threadpool thread that cancellation could not interrupt,
    which leaked ffmpeg processes that kept the file locked ("in use by
    ffmpeg") and made uvicorn's Ctrl+C shutdown time out. Every process is
    also registered so shutdown / file operations can kill it from outside.
    """
    cmd = build_transcode_cmd(abs_path, audio_rel_index, start_time, video_codec, max_height)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
    _register_proc(proc, abs_path)
    try:
        assert proc.stdout is not None
        while True:
            data = await proc.stdout.read(chunk_size)
            if not data:
                break
            yield data
    finally:
        _unregister_proc(proc)
        if proc.returncode is None:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
        try:
            await proc.wait()
        except (asyncio.CancelledError, OSError, ProcessLookupError):
            # A second cancellation can interrupt the reap itself; the process
            # is already killed above, so there is nothing left to do.
            pass
