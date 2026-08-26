"""
yt-dlp hardening, quality selection and format probing for DiskPulse NAS.

Why this module exists
──────────────────────
YouTube actively fingerprints automated clients. The symptom is:

    ERROR: [youtube] <id>: Sign in to confirm you're not a bot.

There is no better *library* to switch to — yt-dlp is the most actively
maintained extractor by a wide margin (pytube and youtube-dl are both far more
frequently broken). The real fixes are operational:

  1. Keep yt-dlp fresh. A months-old yt-dlp is the single most common cause of
     this error, because YouTube's player/challenge format has already moved on.
  2. Rotate the *player client*. YouTube applies different bot checks per
     client, so `tv` / `android_vr` / `ios` often succeed where `web` is
     challenged. We walk a ladder of clients instead of trusting just one.
  3. Send real browser cookies. A signed-in session is the most reliable way
     past the challenge. We auto-detect which installed browser actually holds
     youtube.com cookies and reuse it.
  4. Back off politely (retries + small sleeps) rather than hammering.

This module also builds the yt-dlp format selector for an explicit quality
choice (2160p…360p) or audio-only extraction at a chosen MP3 bitrate.
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from backend.config import format_bytes

# ─────────────────────────────── constants ────────────────────────────────────

#: Browsers probed for a signed-in youtube.com session, best-first.
#: Firefox leads because Chromium-family cookie stores on Windows are often
#: locked (and app-bound-encrypted since Chrome 127), so they frequently fail.
COOKIE_BROWSERS = ("firefox", "chrome", "edge", "brave", "chromium", "opera", "vivaldi")

#: Quality presets exposed in the UI (max height in pixels; None == best).
QUALITY_PRESETS = ("best", "2160", "1440", "1080", "720", "480", "360")

#: Audio containers we can produce, and the bitrates offered for lossy ones.
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
AUDIO_BITRATES = ("320", "256", "192", "128", "96")
LOSSLESS_AUDIO = ("flac", "wav")

#: yt-dlp releases are date-versioned (YYYY.MM.DD); warn past this age.
STALE_AFTER_DAYS = 45

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


class YtdlpUnavailable(RuntimeError):
    pass


# ───────────────────────────── module loading ─────────────────────────────────

def get_ytdlp():
    """Import yt_dlp lazily so the rest of DiskPulse still runs without it."""
    try:
        import yt_dlp  # noqa: WPS433 (runtime import is intentional)
        return yt_dlp
    except ImportError:
        return None


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def ytdlp_version_info() -> Dict[str, Any]:
    """Report the installed yt-dlp version and whether it looks too old.

    yt-dlp uses date-based versions (e.g. 2025.08.27), so we can derive the
    release age directly and warn before YouTube breakage is even reported.
    """
    mod = get_ytdlp()
    info: Dict[str, Any] = {
        "installed": mod is not None,
        "version": None,
        "age_days": None,
        "stale": False,
        "ffmpeg": has_ffmpeg(),
        "message": "",
    }
    if mod is None:
        info["message"] = "yt-dlp is not installed. Run: pip install -U yt-dlp"
        return info

    version = getattr(mod, "__version__", None) or getattr(
        getattr(mod, "version", None), "__version__", None
    )
    info["version"] = version

    if version:
        parts = version.split(".")[:3]
        try:
            released = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            age = (datetime.date.today() - released).days
            info["age_days"] = age
            if age > STALE_AFTER_DAYS:
                info["stale"] = True
                info["message"] = (
                    f"yt-dlp {version} is about {age} days old. YouTube changes often — "
                    "updating usually fixes 'Sign in to confirm you're not a bot' errors."
                )
        except (ValueError, IndexError):
            # Nightly/dev builds don't always parse; not an error.
            pass

    if not info["message"]:
        info["message"] = f"yt-dlp {version or 'unknown'} looks current."
    return info


def update_ytdlp() -> Dict[str, Any]:
    """Run `pip install -U yt-dlp` in-process and report the outcome."""
    cmd = [sys.executable, "-m", "pip", "install", "-U", "--no-input", "yt-dlp[default]"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, creationflags=_CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "pip timed out after 5 minutes.", "output": ""}
    except Exception as exc:
        return {"success": False, "message": f"Could not run pip: {exc}", "output": ""}

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    tail = "\n".join(output.splitlines()[-12:])
    if proc.returncode != 0:
        return {"success": False, "message": "pip failed to update yt-dlp.", "output": tail}

    already = "already satisfied" in output.lower() and "installing collected" not in output.lower()
    return {
        "success": True,
        "already_latest": already,
        "message": (
            "yt-dlp is already at the latest version."
            if already
            else "yt-dlp updated. Restart DiskPulse for the new version to take effect."
        ),
        "output": tail,
        "restart_required": not already,
    }


# ────────────────────────────── cookie detection ──────────────────────────────

_cookie_lock = threading.Lock()
_cookie_browser_cached: Optional[str] = None
_cookie_probe_done = False


class _QuietLogger:
    """Swallow yt-dlp cookie-extraction chatter (missing browsers are normal)."""

    def debug(self, msg): pass

    def info(self, msg): pass

    def warning(self, msg, **kw): pass

    def error(self, msg, **kw): pass


def _extract_jar(browser: str):
    """Load a browser's cookie jar, tolerating yt-dlp signature differences."""
    from yt_dlp.cookies import extract_cookies_from_browser

    try:
        return extract_cookies_from_browser(browser, logger=_QuietLogger())
    except TypeError:
        # Older/newer signature without a logger kwarg.
        return extract_cookies_from_browser(browser)


def _jar_has_youtube_login(jar) -> bool:
    """True only if the jar carries a *signed-in* youtube.com session.

    Merely finding a cookie DB isn't enough — an installed-but-logged-out
    browser yields cookies that don't help with the bot check at all.
    """
    auth_names = {"SID", "__Secure-3PSID", "__Secure-1PSID", "SAPISID", "LOGIN_INFO", "SSID"}
    try:
        for cookie in jar:
            domain = (getattr(cookie, "domain", "") or "").lower()
            if "youtube.com" in domain and cookie.name in auth_names:
                return True
    except Exception:
        return False
    return False


def detect_cookie_browser(force: bool = False) -> Optional[str]:
    """Find the first installed browser holding a signed-in YouTube session.

    Result is cached — walking every browser's cookie DB is slow, and doing it
    on each download would add seconds per task.
    """
    global _cookie_browser_cached, _cookie_probe_done

    with _cookie_lock:
        if _cookie_probe_done and not force:
            return _cookie_browser_cached

        found: Optional[str] = None
        if get_ytdlp() is not None:
            for browser in COOKIE_BROWSERS:
                try:
                    jar = _extract_jar(browser)
                except Exception:
                    continue  # browser absent, DB locked, or encrypted
                if jar is not None and _jar_has_youtube_login(jar):
                    found = browser
                    break

        _cookie_browser_cached = found
        _cookie_probe_done = True
        return found


def cookie_status() -> Dict[str, Any]:
    browser = detect_cookie_browser()
    return {
        "browser": browser,
        "available": browser is not None,
        "message": (
            f"Using signed-in YouTube cookies from {browser.title()}."
            if browser
            else "No signed-in YouTube session found in any installed browser."
        ),
    }


# ───────────────────────────── format selection ───────────────────────────────

def build_format_selector(
    mode: str = "video",
    max_height: str = "best",
    ffmpeg: Optional[bool] = None,
    format_id: Optional[str] = None,
    progressive: bool = False,
) -> str:
    """Compose a yt-dlp format expression for the requested quality.

    Every branch ends in a height-capped ``…/best`` tail so a quality that has
    since disappeared (or that a different player client doesn't expose) falls
    back to the *closest available* stream instead of erroring with
    "Requested format is not available".

    Without ffmpeg we must stick to *progressive* (pre-muxed) streams, which
    YouTube only publishes up to 720p — anything higher is video-only DASH and
    would need muxing.
    """
    if ffmpeg is None:
        ffmpeg = has_ffmpeg()

    if mode == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"

    height = None if max_height in ("best", "", None) else str(max_height)

    # Height-capped "closest available" tail, reused as the fail-safe fallback
    # in every branch below.
    if height:
        tail = f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
    else:
        tail = "best[ext=mp4]/best"

    # An exact stream was chosen from the probed format list.
    if format_id:
        fid = str(format_id)
        if progressive:
            # Already muxed (video+audio) — single file, works without ffmpeg.
            return f"{fid}/{tail}"
        if ffmpeg:
            # Video-only DASH stream — mux it with the best audio track.
            return f"{fid}+bestaudio[ext=m4a]/{fid}+bestaudio/{tail}"
        # Video-only but no ffmpeg to merge → ignore the id, grab closest progressive.
        return tail

    if not ffmpeg:
        # Single-file only. Cap the request so we never pick an unmuxable stream.
        if height:
            return f"best[ext=mp4][height<={height}]/best[height<={height}]/best"
        return "best[ext=mp4][height<=720]/best[height<=720]/best"

    if height:
        return (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
        )
    return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"


def build_postprocessors(
    mode: str,
    audio_format: str = "mp3",
    audio_bitrate: str = "192",
    ffmpeg: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    if ffmpeg is None:
        ffmpeg = has_ffmpeg()
    if mode != "audio" or not ffmpeg:
        return []

    fmt = audio_format if audio_format in AUDIO_FORMATS else "mp3"
    pps: List[Dict[str, Any]] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            # Bitrate is meaningless for lossless containers.
            "preferredquality": None if fmt in LOSSLESS_AUDIO else str(audio_bitrate),
        },
        {"key": "FFmpegMetadata", "add_metadata": True},
    ]
    if fmt in ("mp3", "m4a", "flac"):
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return pps


# ─────────────────────── bot-check evasion strategy ladder ────────────────────

def _strategy_ladder(cookie_browser: Optional[str]) -> List[Dict[str, Any]]:
    """Ordered attempts, cheapest & most-likely-to-work first.

    YouTube gates each player client differently, so a client that is refused
    today may work tomorrow (and vice-versa). Walking a ladder makes the
    downloader resilient to that churn instead of pinning one guess.
    """
    ladder: List[Dict[str, Any]] = [
        {"name": "tv client", "clients": ["tv"], "cookies": False},
    ]
    if cookie_browser:
        ladder.append(
            {"name": f"{cookie_browser} cookies + web_safari",
             "clients": ["web_safari"], "cookies": True}
        )
    ladder += [
        {"name": "android_vr client", "clients": ["android_vr"], "cookies": False},
        {"name": "ios client", "clients": ["ios"], "cookies": False},
        {"name": "tv_embedded client", "clients": ["tv_embedded"], "cookies": False},
    ]
    if cookie_browser:
        ladder.append(
            {"name": f"{cookie_browser} cookies + mweb", "clients": ["mweb"], "cookies": True}
        )
    ladder.append({"name": "yt-dlp defaults", "clients": None, "cookies": bool(cookie_browser)})
    return ladder


def build_ydl_opts(
    outtmpl: str,
    mode: str = "video",
    max_height: str = "best",
    audio_format: str = "mp3",
    audio_bitrate: str = "192",
    progress_hooks: Optional[List[Any]] = None,
    clients: Optional[List[str]] = None,
    cookie_browser: Optional[str] = None,
    ffmpeg: Optional[bool] = None,
    for_probe: bool = False,
    format_id: Optional[str] = None,
    progressive: bool = False,
) -> Dict[str, Any]:
    """Assemble yt-dlp options for one attempt."""
    if ffmpeg is None:
        ffmpeg = has_ffmpeg()

    opts: Dict[str, Any] = {
        "outtmpl": outtmpl,
        "format": build_format_selector(mode, max_height, ffmpeg, format_id, progressive),
        "quiet": True,
        "no_warnings": True,
        "no_color": True,  # never emit ANSI colour codes into captured error text
        "noprogress": True,
        "noplaylist": True,
        "continuedl": True,
        # Politeness / resilience: back off instead of hammering the endpoint,
        # which itself can trigger throttling and bot challenges.
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "sleep_interval_requests": 1,
        # Never let one bad video abort a batch.
        "ignoreerrors": False,
    }

    if progress_hooks:
        opts["progress_hooks"] = progress_hooks

    extractor_args: Dict[str, Any] = {}
    if clients:
        extractor_args["youtube"] = {"player_client": list(clients)}
    if extractor_args:
        opts["extractor_args"] = extractor_args

    # NOTE: deliberately *not* overriding http_headers/User-Agent. yt-dlp sends
    # the UA that matches the chosen player client; a mismatched UA is itself a
    # bot signal and makes these errors more likely, not less.

    if cookie_browser:
        opts["cookiesfrombrowser"] = (cookie_browser,)

    if for_probe:
        opts["skip_download"] = True
        opts["format"] = "all"
        return opts

    if mode == "audio":
        pps = build_postprocessors(mode, audio_format, audio_bitrate, ffmpeg)
        if pps:
            opts["postprocessors"] = pps
            opts["writethumbnail"] = True
    elif ffmpeg:
        opts["merge_output_format"] = "mp4"

    return opts


def friendly_error(exc: Exception) -> str:
    """Turn yt-dlp's raw failure into something actionable in the UI."""
    # Strip any ANSI colour codes yt-dlp embedded (e.g. "\x1b[0;31mERROR:\x1b[0m …").
    raw = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    low = raw.lower()

    if "impersonat" in low:
        return (
            "This site needs browser impersonation, which requires the optional "
            "'curl_cffi' package. Install it with:  pip install -U \"yt-dlp[default]\"  "
            "(or  pip install curl_cffi ) — or click 'Update yt-dlp' in the Add Download "
            "dialog — then restart DiskPulse and retry."
        )
    if "not a bot" in low or "sign in to confirm" in low:
        cookies = detect_cookie_browser()
        hint = (
            f"Signed-in cookies from {cookies.title()} were tried and still refused."
            if cookies
            else "No signed-in browser session was found — log into YouTube in Firefox, "
                 "Chrome or Edge on this machine and retry."
        )
        return (
            "YouTube blocked this download with its bot check. All player clients were "
            f"tried. {hint} Updating yt-dlp (button in the Add Download dialog) fixes "
            "this most of the time."
        )
    if "requested format is not available" in low:
        return (
            "That exact quality isn't published for this video. Use 'Fetch formats' to "
            "see what's actually available, or pick Best."
        )
    if "private video" in low:
        return "This is a private video — an account with access is required."
    if "members-only" in low or "join this channel" in low:
        return "This video is members-only and needs a subscribed account's cookies."
    if "age" in low and ("restrict" in low or "confirm your age" in low):
        return "This video is age-restricted; a signed-in browser session is required."
    if "video unavailable" in low:
        return "YouTube reports this video as unavailable (removed, or region-locked)."
    if "is not available in your country" in low or "geo" in low and "block" in low:
        return "This video is geo-blocked in your region."
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return (
            "ffmpeg is required for this quality/format. Install it "
            "(winget install ffmpeg, or apt install ffmpeg) and retry."
        )
    if "http error 403" in low:
        return "YouTube returned 403 Forbidden — usually a stale yt-dlp. Try updating it."
    if "unsupported url" in low:
        return "That URL isn't supported by yt-dlp."
    if "cancelled by user" in low:
        return "Cancelled."
    # Strip yt-dlp's noisy prefix for anything unrecognised.
    return raw.replace("ERROR: ", "").strip() or "Download failed."


def extract_with_fallback(
    url: str,
    *,
    download: bool,
    outtmpl: str = "%(title)s.%(ext)s",
    mode: str = "video",
    max_height: str = "best",
    audio_format: str = "mp3",
    audio_bitrate: str = "192",
    progress_hooks: Optional[List[Any]] = None,
    for_probe: bool = False,
    on_attempt: Optional[Any] = None,
    format_id: Optional[str] = None,
    progressive: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """Walk the strategy ladder until one attempt succeeds.

    Returns (info_dict, strategy_name). Raises the last error if all fail, or
    immediately if the user cancelled.
    """
    yt_dlp = get_ytdlp()
    if yt_dlp is None:
        raise YtdlpUnavailable("yt-dlp is not installed. Run: pip install -U yt-dlp")

    cookie_browser = detect_cookie_browser()
    ffmpeg = has_ffmpeg()
    last_error: Optional[Exception] = None

    for strategy in _strategy_ladder(cookie_browser):
        if on_attempt:
            try:
                on_attempt(strategy["name"])
            except Exception:
                pass

        opts = build_ydl_opts(
            outtmpl=outtmpl,
            mode=mode,
            max_height=max_height,
            audio_format=audio_format,
            audio_bitrate=audio_bitrate,
            progress_hooks=progress_hooks,
            clients=strategy["clients"],
            cookie_browser=cookie_browser if strategy["cookies"] else None,
            ffmpeg=ffmpeg,
            for_probe=for_probe,
            format_id=format_id,
            progressive=progressive,
        )

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
                if info is None:
                    raise RuntimeError("yt-dlp returned no video information.")
                if download:
                    info = ydl.sanitize_info(info) if hasattr(ydl, "sanitize_info") else info
                return info, strategy["name"]
        except Exception as exc:  # noqa: BLE001 - we classify below
            msg = str(exc).lower()
            # A user cancel must not be retried through the whole ladder.
            if "cancelled by user" in msg:
                raise
            # Hard failures that no client rotation can fix — stop early.
            if any(k in msg for k in ("private video", "video unavailable", "unsupported url",
                                      "members-only", "is not a valid url")):
                raise
            last_error = exc
            continue

    raise last_error or RuntimeError("All yt-dlp strategies failed.")


# ──────────────────────────────── probing ─────────────────────────────────────

def _size_of(fmt: Dict[str, Any]) -> Optional[int]:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    try:
        return int(size) if size else None
    except (TypeError, ValueError):
        return None


def _fmt_size(nbytes: Optional[int]) -> str:
    """Format a probed format's size, or "" when yt-dlp didn't report one.

    The empty string is deliberate: an unknown size should show nothing in the
    quality picker rather than a misleading "0 B".
    """
    if not nbytes:
        return ""
    return format_bytes(nbytes)


def probe_formats(url: str) -> Dict[str, Any]:
    """Inspect a URL and report the qualities that genuinely exist for it.

    Powers the 'Fetch formats' button so the UI offers real choices (with real
    sizes) instead of guessing at presets the video may not publish.
    """
    info, strategy = extract_with_fallback(url, download=False, for_probe=True)

    formats = info.get("formats") or []
    ffmpeg = has_ffmpeg()

    # Best audio size is added to video-only streams for a realistic total.
    audio_only = [f for f in formats
                  if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    best_audio_size = max((_size_of(f) or 0 for f in audio_only), default=0)

    by_height: Dict[int, Dict[str, Any]] = {}
    for f in formats:
        if f.get("vcodec") in (None, "none"):
            continue
        height = f.get("height")
        if not height:
            continue
        progressive = f.get("acodec") not in (None, "none")
        size = _size_of(f) or 0
        total = size if progressive else (size + best_audio_size)
        existing = by_height.get(int(height))
        better = (
            existing is None
            or (f.get("ext") == "mp4" and existing.get("ext") != "mp4")
            or (total and not existing.get("size_bytes"))
        )
        if better:
            by_height[int(height)] = {
                "height": int(height),
                "label": f"{height}p",
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "fps": f.get("fps"),
                "vcodec": (f.get("vcodec") or "").split(".")[0],
                "progressive": progressive,
                "requires_ffmpeg": not progressive and ffmpeg,
                "size_bytes": total or None,
                "size_human": _fmt_size(total),
            }

    video_options = sorted(by_height.values(), key=lambda x: x["height"], reverse=True)
    for opt in video_options:
        fps = opt.get("fps")
        suffix = f" {int(fps)}fps" if fps and fps >= 50 else ""
        size = f" · ~{opt['size_human']}" if opt["size_human"] else ""
        opt["display"] = f"{opt['label']}{suffix}{size}"

    audio_options: List[Dict[str, Any]] = []
    for f in sorted(audio_only, key=lambda x: (x.get("abr") or 0), reverse=True):
        abr = f.get("abr")
        if not abr:
            continue
        size = _size_of(f)
        audio_options.append({
            "abr": int(abr),
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "acodec": (f.get("acodec") or "").split(".")[0],
            "size_bytes": size,
            "size_human": _fmt_size(size),
            "display": f"{int(abr)} kbps {f.get('ext') or ''}".strip()
                       + (f" · ~{_fmt_size(size)}" if size else ""),
        })

    duration = info.get("duration")
    return {
        "title": info.get("title") or "Unknown title",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": duration,
        "duration_human": (
            str(datetime.timedelta(seconds=int(duration))) if duration else ""
        ),
        "thumbnail": info.get("thumbnail"),
        "is_live": bool(info.get("is_live")),
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "video_options": video_options,
        "audio_options": audio_options,
        "ffmpeg": ffmpeg,
        "strategy": strategy,
        "cookie_browser": detect_cookie_browser(),
        "max_progressive_note": (
            "" if ffmpeg else
            "ffmpeg not found — DASH streams (1080p+) need muxing and will fall back "
            "to the best progressive stream. Install ffmpeg for exact quality."
        ),
    }


def resolve_output_path(info: Dict[str, Any], fallback: str) -> str:
    """Find the real on-disk path after post-processing.

    Audio extraction rewrites the container (e.g. .webm → .mp3), so
    prepare_filename() alone reports a file that no longer exists.
    """
    requested = info.get("requested_downloads") or []
    if requested:
        for key in ("filepath", "_filename", "filename"):
            path = requested[0].get(key)
            if path and os.path.exists(path):
                return path
        path = requested[0].get("filepath")
        if path:
            return path
    if info.get("filepath"):
        return info["filepath"]
    return fallback
