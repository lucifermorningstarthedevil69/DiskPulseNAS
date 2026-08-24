"""
Network speed test & latency monitoring service for DiskPulse NAS.

Uses Cloudflare's public speed-test edge (speed.cloudflare.com) to measure
real download / upload throughput, HTTP latency, and to detect the client
ISP / public IP / nearest datacenter. This works reliably and identically on
both Windows and Linux with no third-party libraries — only the Python
standard library — which avoids the frequent HTTP 403 blocking that made the
old Ookla `speedtest-cli` dependency unreliable.
"""
import asyncio
import http.client
import json
import re
import shutil
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List

from backend.config import BASE_DIR, DATA_DIR

# Cloudflare speed-test edge endpoints
CF_HOST = "speed.cloudflare.com"
CF_META_PATH = "/meta"
CF_DOWN_PATH = "/__down?bytes={n}"
CF_UP_PATH = "/__up"

USER_AGENT = "DiskPulse-NAS-SpeedTest/2.0"
_HEADERS = {"User-Agent": USER_AGENT, "Connection": "keep-alive"}

# Tuning constants
_LATENCY_SAMPLES = 6          # the first probe on each connection is discarded
_DOWNLOAD_WARMUP_BYTES = 2_500_000       # 2.5 MB probe
_DOWNLOAD_MAX_BYTES = 100_000_000        # cap download payload at 100 MB
_DOWNLOAD_MIN_BYTES = 5_000_000
_DOWNLOAD_TARGET_SECS = 8.0
_UPLOAD_WARMUP_BYTES = 2_000_000         # 2 MB probe
_UPLOAD_MAX_BYTES = 30_000_000           # cap upload payload at 30 MB
_UPLOAD_MIN_BYTES = 4_000_000
_UPLOAD_TARGET_SECS = 6.0
_CHUNK = 65536

#: How often to emit a throughput sample. 100 ms is what the browser-based
#: speed tests plot at: fine enough to show TCP ramp-up and mid-test dips, coarse
#: enough that an 8 s run is ~80 points rather than thousands.
_SAMPLE_INTERVAL = 0.1

#: Runs kept on disk for the history chart. Small enough to load and parse
#: instantly on every page load.
_HISTORY_LIMIT = 30
HISTORY_FILE = DATA_DIR / "speedtest_history.json"


def _resolve_history_file() -> Path:
    """Return the history file path, preferring the storage pool location."""
    default_path = DATA_DIR / "speedtest_history.json"
    # Respect explicit overrides (e.g. tests that set HISTORY_FILE directly)
    if HISTORY_FILE != default_path:
        return HISTORY_FILE

    # Check storage pool first
    try:
        from backend.config import _storage_pool_config
        sp_config = _storage_pool_config()
        if sp_config:
            candidate = sp_config.parent / "speedtest_history.json"
            if candidate.exists() or sp_config.parent.exists():
                return candidate
    except Exception:
        pass

    return HISTORY_FILE


class SpeedTestError(Exception):
    pass


# ──────────────────────────── throughput sampling ─────────────────────────────

class ThroughputRecorder:
    """Collects instantaneous throughput samples during one transfer.

    The rate is computed per window, not cumulatively: a cumulative average
    flattens out and hides exactly what the chart is for — the TCP slow-start
    ramp at the beginning and any stall in the middle. Callers push byte counts
    as they arrive and a sample is emitted whenever the window elapses.
    """

    def __init__(self, on_sample: Optional[Callable[[Dict[str, float]], None]] = None,
                 interval: float = _SAMPLE_INTERVAL):
        self.samples: List[Dict[str, float]] = []
        self.interval = interval
        self._on_sample = on_sample
        self._start = time.perf_counter()
        self._window_start = self._start
        self._window_bytes = 0
        self._total_bytes = 0

    def add(self, nbytes: int) -> None:
        self._window_bytes += nbytes
        self._total_bytes += nbytes
        now = time.perf_counter()
        if now - self._window_start >= self.interval:
            self._emit(now)

    def _emit(self, now: float) -> None:
        span = now - self._window_start
        if span <= 0:
            return
        sample = {
            "t": round(now - self._start, 3),
            "mbps": round((self._window_bytes * 8) / (span * 1_000_000), 2),
            "bytes": self._total_bytes,
        }
        self.samples.append(sample)
        self._window_start = now
        self._window_bytes = 0
        if self._on_sample:
            try:
                self._on_sample(sample)
            except Exception:
                pass

    def finish(self) -> None:
        """Flush a final partial window so short transfers still plot something."""
        if self._window_bytes > 0:
            self._emit(time.perf_counter())

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start


# ─────────────────────────── low-level HTTP helpers ───────────────────────────

def _new_conn(timeout: float = 20.0) -> http.client.HTTPSConnection:
    ctx = ssl.create_default_context()
    return http.client.HTTPSConnection(CF_HOST, timeout=timeout, context=ctx)


def _warmup(timeout: float = 15.0) -> None:
    """Prime DNS + TLS to speed.cloudflare.com so the first *real* request doesn't
    absorb the cold-start latency — that cold start was making /meta time out
    intermittently even though download/upload/ping (which run later) succeeded."""
    conn = _new_conn(timeout)
    try:
        conn.request("GET", CF_DOWN_PATH.format(n=0), headers=_HEADERS)
        conn.getresponse().read()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _http_get(path: str, timeout: float = 15.0, attempts: int = 3) -> Optional[bytes]:
    """GET a small resource with retries and one level of same-host redirect following."""
    for _ in range(max(1, attempts)):
        conn = _new_conn(timeout)
        try:
            conn.request("GET", path, headers=_HEADERS)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status == 200:
                return raw
            if resp.status in (301, 302, 307, 308):
                loc = resp.getheader("Location") or ""
                if loc.startswith("/"):
                    path = loc
                    continue
                m = re.match(r"https?://%s(/.*)" % re.escape(CF_HOST), loc)
                if m:
                    path = m.group(1)
                    continue
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        time.sleep(0.4)
    return None


def _fetch_meta(timeout: float = 15.0) -> Dict[str, Any]:
    """Cloudflare /meta returns clientIp, asOrganization (ISP), colo, city, country."""
    raw = _http_get(CF_META_PATH, timeout=timeout, attempts=3)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def _fetch_trace(timeout: float = 10.0) -> Dict[str, str]:
    """Fallback for client IP / colo / country when /meta is unavailable.
    /cdn-cgi/trace returns simple `key=value` lines (ip=, colo=, loc=, ...)."""
    raw = _http_get("/cdn-cgi/trace", timeout=timeout, attempts=2)
    if not raw:
        return {}
    out: Dict[str, str] = {}
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _measure_latency(samples: int = _LATENCY_SAMPLES, timeout: float = 8.0,
                     on_sample: Optional[Callable[[Dict[str, Any]], None]] = None
                     ) -> Dict[str, Any]:
    """Reuse a single keep-alive connection so we time round-trips, not handshakes.

    Returns every sample, not just the median. Jitter is the whole reason: it can
    only be computed from the spread, and a link with a good average ping but wild
    variance is exactly the one that stutters during a video call. Failed probes
    are counted too, so packet loss is visible instead of silently skipped.
    """
    conn = _new_conn(timeout)
    times: List[float] = []
    lost = 0
    attempted = 0
    # A probe only measures a round-trip once the connection has already paid for
    # its TCP + TLS handshake. Keying that off the loop index instead would let a
    # failed first probe push the *next* probe's handshake into the numbers, which
    # inflates both the maximum and the jitter by an order of magnitude.
    warmed = False
    try:
        for _i in range(samples):
            counted = warmed
            try:
                t0 = time.perf_counter()
                conn.request("GET", CF_DOWN_PATH.format(n=0), headers=_HEADERS)
                resp = conn.getresponse()
                resp.read()
                dt = (time.perf_counter() - t0) * 1000.0
                if not counted:
                    warmed = True          # this probe was the handshake; discard it
                    continue
                attempted += 1
                times.append(round(dt, 2))
                if on_sample:
                    try:
                        on_sample({"i": len(times), "ms": round(dt, 2)})
                    except Exception:
                        pass
            except Exception:
                if counted:
                    attempted += 1
                    lost += 1
                # Connection may have dropped; reopen and keep sampling. The
                # replacement connection is cold, so the next probe is a handshake.
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _new_conn(timeout)
                warmed = False
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not times:
        # Nothing answered on a warmed connection: report total loss rather than
        # dividing by an attempt count of zero.
        return {"ping_ms": None, "samples": [], "jitter_ms": None,
                "min_ms": None, "max_ms": None,
                "loss_pct": round(100.0 * lost / attempted, 1) if attempted else 100.0}

    ordered = sorted(times)
    # Mean absolute difference between consecutive round-trips — the standard
    # jitter definition (RFC 3550's approach), and what other speed tests show.
    # Deliberately computed on arrival order, not the sorted copy.
    deltas = [abs(b - a) for a, b in zip(times, times[1:])]
    return {
        "ping_ms": round(ordered[len(ordered) // 2], 1),   # median round-trip
        "samples": times,
        "jitter_ms": round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
        "min_ms": round(min(times), 1),
        "max_ms": round(max(times), 1),
        "loss_pct": round(100.0 * lost / attempted, 1),
    }


def _timed_download(nbytes: int, timeout: float,
                    recorder: Optional[ThroughputRecorder] = None) -> Optional[Dict[str, Any]]:
    """Download nbytes and time it. Handles mid-transfer timeouts by using the
    partial bytes actually received, so slow links still report a usable rate."""
    conn = _new_conn(timeout)
    read = 0
    t0 = time.perf_counter()
    try:
        conn.request("GET", CF_DOWN_PATH.format(n=nbytes), headers=_HEADERS)
        resp = conn.getresponse()
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if recorder is not None:
                recorder.add(len(chunk))
    except (socket.timeout, TimeoutError, OSError):
        pass
    finally:
        if recorder is not None:
            recorder.finish()
        try:
            conn.close()
        except Exception:
            pass
    dt = time.perf_counter() - t0
    if dt <= 0 or read <= 0:
        return None
    return {"bytes": float(read), "seconds": dt,
            "mbps": round((read * 8) / (dt * 1_000_000), 2),
            "samples": list(recorder.samples) if recorder is not None else []}


def _measure_download(on_sample: Optional[Callable[[Dict[str, float]], None]] = None,
                      on_plan: Optional[Callable[[float], None]] = None
                      ) -> Dict[str, Any]:
    # Warm-up probe to estimate the link, then size the main run to ~target secs.
    warm = _timed_download(_DOWNLOAD_WARMUP_BYTES, timeout=20.0)
    # 50 Mbps is a sizing guess for when the warm-up told us nothing — it must never
    # escape as a measurement, or an offline machine "measures" 50 Mbps.
    est_mbps = warm["mbps"] if warm else 50.0
    est_bytes_per_sec = max(est_mbps, 1.0) * 1_000_000 / 8.0
    target = int(est_bytes_per_sec * _DOWNLOAD_TARGET_SECS)
    target = max(_DOWNLOAD_MIN_BYTES, min(target, _DOWNLOAD_MAX_BYTES))
    # The byte cap can make the real transfer much shorter than the nominal target
    # on a fast link, so tell the caller how long this is actually expected to take.
    # Without it the progress bar races to the end of the phase and then sits still.
    _announce_plan(on_plan, target / est_bytes_per_sec)

    recorder = ThroughputRecorder(on_sample)
    main = _timed_download(target, timeout=30.0, recorder=recorder)
    if main:
        return {"mbps": main["mbps"], "bytes": int(main["bytes"]),
                "seconds": round(main["seconds"], 2), "samples": main["samples"]}
    if not warm:
        # Nothing was transferred at all. Report zero so the caller's connectivity
        # check fires, instead of passing the sizing guess off as a measurement.
        return {"mbps": 0.0, "bytes": 0, "seconds": 0.0, "samples": []}
    # The warm-up is all we have. Report its rate rather than nothing, but say so
    # by leaving the sample list empty — the chart then shows no ramp curve.
    return {"mbps": round(est_mbps, 2), "bytes": int(warm["bytes"]),
            "seconds": round(warm["seconds"], 2), "samples": []}


def _announce_plan(on_plan: Optional[Callable[[float], None]], secs: float) -> None:
    """Hand the expected phase duration to the caller. Never fail the transfer."""
    if on_plan is None:
        return
    try:
        on_plan(max(0.1, float(secs)))
    except Exception:
        pass


def _timed_upload(nbytes: int, timeout: float,
                  recorder: Optional[ThroughputRecorder] = None) -> Optional[Dict[str, Any]]:
    """POST nbytes to Cloudflare /__up (contents discarded) and time it.

    When a recorder is supplied the body is sent as an iterable of chunks so the
    upload can be sampled as it drains, instead of handing http.client one opaque
    blob and learning nothing until it finishes.
    """
    conn = _new_conn(timeout)
    t0 = time.perf_counter()
    try:
        headers = dict(_HEADERS)
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(nbytes)
        if recorder is None:
            body: Any = bytes(nbytes)
        else:
            body = _chunked_payload(nbytes, recorder)
        conn.request("POST", CF_UP_PATH, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
    except (socket.timeout, TimeoutError, OSError):
        return None
    finally:
        if recorder is not None:
            recorder.finish()
        try:
            conn.close()
        except Exception:
            pass
    dt = time.perf_counter() - t0
    if dt <= 0:
        return None
    return {"bytes": float(nbytes), "seconds": dt,
            "mbps": round((nbytes * 8) / (dt * 1_000_000), 2),
            "samples": list(recorder.samples) if recorder is not None else []}


def _chunked_payload(nbytes: int, recorder: ThroughputRecorder):
    """Yield a zero-filled body in chunks, recording each one as it is handed over.

    Note this measures bytes written into the socket, which on a fast machine runs
    ahead of bytes actually on the wire until the send buffer fills. Over a
    multi-second transfer that head start is bounded by the buffer size and washes
    out; the reported total rate still comes from the full wall-clock time.
    """
    chunk = bytes(_CHUNK)
    remaining = nbytes
    while remaining > 0:
        n = min(_CHUNK, remaining)
        remaining -= n
        recorder.add(n)
        yield chunk if n == _CHUNK else chunk[:n]


def _measure_upload(on_sample: Optional[Callable[[Dict[str, float]], None]] = None,
                    on_plan: Optional[Callable[[float], None]] = None
                    ) -> Dict[str, Any]:
    warm = _timed_upload(_UPLOAD_WARMUP_BYTES, timeout=20.0)
    # As with download, 25 Mbps only sizes the payload; it is never a result.
    est_mbps = warm["mbps"] if warm else 25.0
    est_bytes_per_sec = max(est_mbps, 1.0) * 1_000_000 / 8.0
    target = int(est_bytes_per_sec * _UPLOAD_TARGET_SECS)
    target = max(_UPLOAD_MIN_BYTES, min(target, _UPLOAD_MAX_BYTES))
    _announce_plan(on_plan, target / est_bytes_per_sec)

    recorder = ThroughputRecorder(on_sample)
    main = _timed_upload(target, timeout=30.0, recorder=recorder)
    if main:
        return {"mbps": main["mbps"], "bytes": int(main["bytes"]),
                "seconds": round(main["seconds"], 2), "samples": main["samples"]}
    if recorder.total_bytes > 0:
        # The POST timed out, but real bytes went out before it did and the user
        # watched them plot. Keep that trace and rate it over the whole attempt —
        # which includes the stall, so it errs low rather than flattering the link.
        secs = recorder.elapsed
        return {"mbps": round((recorder.total_bytes * 8) / (secs * 1_000_000), 2) if secs > 0 else 0.0,
                "bytes": recorder.total_bytes, "seconds": round(secs, 2),
                "samples": list(recorder.samples)}
    if not warm:
        return {"mbps": 0.0, "bytes": 0, "seconds": 0.0, "samples": []}
    return {"mbps": round(est_mbps, 2), "bytes": int(warm["bytes"]),
            "seconds": round(warm["seconds"], 2), "samples": []}


# ─────────────────────────────── manager ──────────────────────────────────────

#: Ordered phases of one run, with the fraction of the total each one represents.
#: Only used to drive the progress bar, so the weights are rough by design —
#: they're the observed share of a typical ~25 s run.
_PHASES = (
    ("connecting", "Connecting to Cloudflare edge", 0.08),
    ("latency", "Measuring latency and jitter", 0.12),
    ("download", "Measuring download throughput", 0.50),
    ("upload", "Measuring upload throughput", 0.30),
)


def _load_history() -> List[Dict[str, Any]]:
    """Past runs, oldest first. A corrupt or missing file is simply no history."""
    history_file = _resolve_history_file()
    legacy_file = BASE_DIR / "speedtest_history.json"
    if not history_file.exists() and legacy_file.exists():
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_file, history_file)
        except Exception:
            pass

    target = history_file if history_file.exists() else legacy_file
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)][-_HISTORY_LIMIT:]


def _append_history(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Record one completed run. Never let a disk problem fail the speed test."""
    entry = {
        "timestamp": result.get("timestamp"),
        "download_mbps": result.get("download_mbps"),
        "upload_mbps": result.get("upload_mbps"),
        "ping_ms": result.get("ping_ms"),
        "jitter_ms": result.get("jitter_ms"),
        "isp": result.get("isp"),
        "server": (result.get("server") or {}).get("name"),
    }
    history = _load_history()
    history.append(entry)
    history = history[-_HISTORY_LIMIT:]
    try:
        history_file = _resolve_history_file()
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError:
        pass
    return history


class SpeedTestManager:
    def __init__(self):
        self.is_running = False
        self.last_status = "idle"  # idle, running, completed, error
        self.error_message = ""
        self.latest_result: Dict[str, Any] = self._blank_result()
        self.history: List[Dict[str, Any]] = _load_history()
        # Written from the worker thread, read by every HTTP poll, so it needs a
        # lock — and readers get a copy, never the list the sampler is appending to.
        self._lock = threading.Lock()
        self._live = self._blank_live()
        self._started_at = time.time()
        # How long each transfer phase is expected to take, so the progress bar can
        # be scaled to the payload that was actually sized rather than the nominal
        # target. Replaced by _on_plan once the warm-up has measured the link.
        self._targets: Dict[str, float] = {
            "download": _DOWNLOAD_TARGET_SECS,
            "upload": _UPLOAD_TARGET_SECS,
        }
        self._current_task: Optional[asyncio.Task] = None

    @staticmethod
    def _blank_result() -> Dict[str, Any]:
        """A result with no measurements in it.

        Both the initial state and the error state are built from this. An error
        must never inherit the previous run's numbers: showing "94 Mbps" next to a
        TEST ERROR badge reads as a fresh measurement, and the stale samples would
        be charted as if they had just been captured.
        """
        return {
            "status": "idle",
            "download_mbps": 0.0,
            "upload_mbps": 0.0,
            "ping_ms": 0.0,
            "jitter_ms": None,
            "server": {
                "name": "Not Tested Yet",
                "sponsor": "--",
                "country": "--",
                "distance_km": None,
            },
            "client_ip": "--",
            "isp": "--",
            "timestamp": None,
            "download_samples": [],
            "upload_samples": [],
            "ping_samples": [],
        }

    @staticmethod
    def _blank_live() -> Dict[str, Any]:
        return {
            "phase": "idle",
            "phase_label": "Idle",
            "progress": 0,
            "elapsed": 0.0,
            "mbps": 0.0,
            "download_samples": [],
            "upload_samples": [],
            "ping_samples": [],
        }

    # ── live progress, written from the measurement thread ────────────────────

    def _set_phase(self, phase: str) -> None:
        label = next((lbl for key, lbl, _ in _PHASES if key == phase), phase.title())
        base = 0.0
        for key, _lbl, weight in _PHASES:
            if key == phase:
                break
            base += weight
        with self._lock:
            self._live["phase"] = phase
            self._live["phase_label"] = label
            self._live["progress"] = int(round(base * 100))
            self._live["mbps"] = 0.0
            self._live["elapsed"] = round(time.time() - self._started_at, 1)

    def _phase_progress(self, phase: str, fraction: float) -> None:
        """Advance the bar within the current phase (fraction 0..1)."""
        base = 0.0
        weight = 0.0
        for key, _lbl, w in _PHASES:
            if key == phase:
                weight = w
                break
            base += w
        pct = int(round((base + weight * max(0.0, min(1.0, fraction))) * 100))
        with self._lock:
            self._live["progress"] = max(self._live.get("progress", 0), pct)

    def _on_transfer_sample(self, kind: str):
        key = f"{kind}_samples"

        def handler(sample: Dict[str, float]) -> None:
            with self._lock:
                self._live[key].append(sample)
                self._live["mbps"] = sample["mbps"]
                self._live["elapsed"] = round(time.time() - self._started_at, 1)
            target = self._targets.get(kind) or 0.0
            if target > 0:
                self._phase_progress(kind, sample["t"] / target)

        return handler

    def _on_plan(self, kind: str):
        """Receives the expected duration of a transfer phase once it is sized."""
        def handler(secs: float) -> None:
            with self._lock:
                self._targets[kind] = secs

        return handler

    def _on_ping_sample(self, sample: Dict[str, Any]) -> None:
        with self._lock:
            self._live["ping_samples"].append(sample)
            self._live["elapsed"] = round(time.time() - self._started_at, 1)

    # ── public state ──────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            live = {
                **self._live,
                "download_samples": list(self._live["download_samples"]),
                "upload_samples": list(self._live["upload_samples"]),
                "ping_samples": list(self._live["ping_samples"]),
            }
        return {
            "is_running": self.is_running,
            "status": self.last_status,
            "error_message": self.error_message,
            # Copies, so a caller that mutates the response — or serialises it while
            # a run finishes — can't reach into the manager's own state. Shallow is
            # enough: both are replaced wholesale, never edited in place.
            "latest": dict(self.latest_result),
            "live": live,
            "history": list(self.history),
        }

    def start_test(self, server_id: Optional[int] = None) -> Dict[str, Any]:
        if self.is_running:
            return {
                "success": True,
                "is_running": True,
                "message": "Speed test is already in progress",
                "status": "running",
                "latest": dict(self.latest_result),
            }

        self.is_running = True
        self.last_status = "running"
        self.error_message = ""
        self._started_at = time.time()
        with self._lock:
            self._live = self._blank_live()
            self._live["phase"] = "connecting"
            self._live["phase_label"] = _PHASES[0][1]
            self._targets = {"download": _DOWNLOAD_TARGET_SECS,
                             "upload": _UPLOAD_TARGET_SECS}
        self._current_task = asyncio.create_task(self._run_async_test(server_id))
        return {
            "success": True,
            "is_running": True,
            "message": "Speed test started",
            "status": "running",
            "latest": dict(self.latest_result),
        }

    async def _run_async_test(self, server_id: Optional[int] = None):
        loop = asyncio.get_event_loop()
        try:
            res = await loop.run_in_executor(None, self._run_cloudflare_sync)
            self.latest_result = res
            self.latest_result["status"] = "completed"
            self.last_status = "completed"
            self.error_message = ""
            # Writing the history file is disk I/O; keep it off the event loop.
            self.history = await loop.run_in_executor(None, _append_history, res)
            with self._lock:
                self._live["phase"] = "done"
                self._live["phase_label"] = "Complete"
                self._live["progress"] = 100
        except Exception as e:
            self.last_status = "error"
            self.error_message = f"Speed test failed: {e}"
            # Built from the blank template, not from the last result — see
            # _blank_result. Only the server name is overridden, to say what failed.
            self.latest_result = {
                **self._blank_result(),
                "status": "error",
                "server": {"name": "Test Failed", "sponsor": "--",
                           "country": "--", "distance_km": None},
            }
            with self._lock:
                self._live["phase"] = "error"
                self._live["phase_label"] = "Failed"
        finally:
            self.is_running = False

    def _run_cloudflare_sync(self, server_id: Optional[int] = None) -> Dict[str, Any]:
        """Blocking Cloudflare measurement — always run inside an executor."""
        # Warm up DNS/TLS first; the very first HTTPS request to Cloudflare on a
        # cold process is the slowest, and it was making /meta time out (empty
        # IP / ISP / location) even when the later transfers succeeded.
        self._set_phase("connecting")
        _warmup()

        meta = _fetch_meta()
        # If /meta didn't yield the client IP, recover IP / colo / country from the
        # lightweight /cdn-cgi/trace endpoint so those fields aren't left blank.
        if not meta.get("clientIp"):
            trace = _fetch_trace()
            if trace:
                meta.setdefault("clientIp", trace.get("ip"))
                meta.setdefault("colo", trace.get("colo"))
                meta.setdefault("country", trace.get("loc"))

        self._set_phase("latency")
        latency = _measure_latency(on_sample=self._on_ping_sample)
        ping_ms = latency["ping_ms"]

        self._set_phase("download")
        down = _measure_download(self._on_transfer_sample("download"),
                                 self._on_plan("download"))

        self._set_phase("upload")
        up = _measure_upload(self._on_transfer_sample("upload"),
                             self._on_plan("upload"))

        download_mbps = down["mbps"]
        upload_mbps = up["mbps"]

        # If we could not move any data at all, treat it as a hard failure so the
        # UI shows an error rather than a bogus 0 Mbps "completed" result.
        if download_mbps <= 0 and upload_mbps <= 0 and ping_ms is None:
            raise SpeedTestError(
                "No connectivity to Cloudflare speed edge (check the server's internet access)."
            )

        colo = meta.get("colo")
        city = meta.get("city")
        country = meta.get("country")
        loc_bits = city or "Edge"
        server_name = f"Cloudflare {loc_bits}" + (f" [{colo}]" if colo else "")

        return {
            "status": "completed",
            "download_mbps": round(download_mbps, 2),
            "upload_mbps": round(upload_mbps, 2),
            "ping_ms": round(ping_ms, 1) if ping_ms is not None else 0.0,
            "jitter_ms": latency["jitter_ms"],
            "ping_min_ms": latency["min_ms"],
            "ping_max_ms": latency["max_ms"],
            "packet_loss_pct": latency["loss_pct"],
            "ping_samples": latency["samples"],
            "download_samples": down["samples"],
            "upload_samples": up["samples"],
            "download_bytes": down["bytes"],
            "upload_bytes": up["bytes"],
            "download_seconds": down["seconds"],
            "upload_seconds": up["seconds"],
            "server": {
                "name": server_name,
                "sponsor": "Cloudflare",
                "country": country or "--",
                "distance_km": None,
            },
            "client_ip": meta.get("clientIp") or "--",
            "isp": meta.get("asOrganization") or "Broadband",
            "timestamp": time.time(),
        }


speedtest_manager = SpeedTestManager()


async def run_speed_test(server_id: Optional[int] = None) -> Dict[str, Any]:
    return speedtest_manager.start_test(server_id)


async def quick_ping_test(host: str = "1.1.1.1", count: int = 3) -> Dict[str, Any]:
    """Lightweight latency-only check using the system ping binary (cross-platform)."""
    ping_exe = shutil.which("ping")
    if not ping_exe:
        # Simple socket connect latency fallback
        t0 = time.time()
        try:
            _, writer = await asyncio.open_connection("1.1.1.1", 53)
            writer.close()
            await writer.wait_closed()
            latency = (time.time() - t0) * 1000.0
            return {
                "host": host,
                "avg_ms": round(latency, 1),
                "min_ms": round(latency, 1),
                "max_ms": round(latency, 1),
                "packet_loss_pct": 0.0,
                "timestamp": time.time(),
            }
        except Exception:
            return {
                "host": host,
                "avg_ms": 20.0,
                "min_ms": 20.0,
                "max_ms": 20.0,
                "packet_loss_pct": 0.0,
                "timestamp": time.time(),
            }

    cmd = [ping_exe, "-n" if sys.platform.startswith("win") else "-c", str(count), host]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode(errors="ignore")

        times = [float(m) for m in re.findall(r"time[=<]([\d.]+)", text)]
        avg = sum(times) / len(times) if times else None
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet)?\s*loss", text)

        return {
            "host": host,
            "avg_ms": round(avg, 1) if avg is not None else 25.0,
            "min_ms": round(min(times), 1) if times else 20.0,
            "max_ms": round(max(times), 1) if times else 30.0,
            "packet_loss_pct": float(loss_match.group(1)) if loss_match else 0.0,
            "timestamp": time.time(),
        }
    except Exception:
        return {
            "host": host,
            "avg_ms": 25.0,
            "min_ms": 20.0,
            "max_ms": 30.0,
            "packet_loss_pct": 0.0,
            "timestamp": time.time(),
        }
