"""
Cross-platform real drive health / S.M.A.R.T. telemetry for DiskPulse NAS.

Replaces the old hardcoded mock drive table with live data from the machine
DiskPulse is actually running on:

  • Windows : PowerShell  Get-PhysicalDisk  +  Get-StorageReliabilityCounter
              (temperature / power-on-hours / wear need Administrator; without
               it we still report model, size, media type and health status)
  • Linux   : smartctl (smartmontools) with JSON output, falling back to
              lsblk + /sys + psutil sensors when smartctl is missing or the
              process lacks root

Results are cached with a TTL and refreshed on a background thread, because the
telemetry WebSocket ticks once per second and shelling out to PowerShell /
smartctl on every tick would be far too expensive (and would block the event
loop). Callers just get the most recent snapshot instantly.
"""
import concurrent.futures as futures
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, NamedTuple, Optional

import psutil
from backend.config import format_bytes

_SYSTEM = platform.system()
_IS_WINDOWS = _SYSTEM == "Windows"
_IS_LINUX = _SYSTEM == "Linux"

# How long a snapshot stays fresh before a background refresh is triggered.
_TTL_SECONDS = 30.0

# Windows: suppress the console window a PowerShell child might otherwise flash.
_CREATE_NO_WINDOW = 0x08000000 if _IS_WINDOWS else 0


# ────────────────────────────── small helpers ─────────────────────────────────

def _to_int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _human_size(nbytes: Optional[int]) -> str:
    if not nbytes:
        return "—"
    return format_bytes(nbytes)


def _temp_status(temp: Optional[float], is_ssd: bool) -> str:
    """Temperature banding. SSD/NVMe tolerate higher temps than spinning disks."""
    if temp is None:
        return "Unknown"
    if is_ssd:
        if temp >= 75:
            return "Critical"
        if temp >= 65:
            return "Warning"
    else:
        if temp >= 60:
            return "Critical"
        if temp >= 50:
            return "Warning"
    return "Normal"


def _ata_surface_penalty(reallocated: Optional[int], pending: Optional[int],
                         uncorrectable: Optional[int]) -> int:
    """Health deduction for ATA drives with surface damage.

    Scales with the order of magnitude of each counter, so a drive with a
    handful of reallocated sectors loses less health than one with thousands —
    but any non-zero count always costs something, so a drive flagged
    "Warning" never also shows 100% health."""
    def decade_penalty(count: Optional[int], per_decade: int, cap: int) -> int:
        if not count or count <= 0:
            return 0
        decades = len(str(int(count)))  # 1-9 -> 1, 10-99 -> 2, 100-999 -> 3, ...
        return min(cap, per_decade * decades)

    penalty = decade_penalty(reallocated, 8, 40)
    penalty += decade_penalty(pending, 10, 30)
    penalty += decade_penalty(uncorrectable, 10, 30)
    return min(penalty, 90)


def _run(cmd: List[str], timeout: float = 15.0) -> Optional[str]:
    """Run a command and return stdout, or None on any failure."""
    return _run_full(cmd, timeout).stdout or None


class _ProcResult(NamedTuple):
    """What actually happened when we shelled out — not just the stdout.

    ``smartctl`` communicates the *reason* a read failed through its exit code
    and stderr, so throwing those away is what left the dashboard unable to tell
    "this drive is asleep" apart from "this drive has no S.M.A.R.T. at all".
    """
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool


def _run_full(cmd: List[str], timeout: float = 15.0) -> _ProcResult:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return _ProcResult(proc.stdout or "", proc.stderr or "", proc.returncode, False)
    except subprocess.TimeoutExpired as exc:
        # A killed process can still have written useful JSON before the deadline.
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return _ProcResult(out, err, None, True)
    except Exception:
        return _ProcResult("", "", None, False)


# ──────────────────── why a S.M.A.R.T. read produced nothing ──────────────────

# smartctl's exit code is a bitmask (see RETURN VALUES in smartctl(8)). Bits 0-2
# tell us whether the failure was our command line, opening the device, or the
# device refusing the S.M.A.R.T. command itself.
_SMART_BIT_CMDLINE = 0x01
_SMART_BIT_OPEN_FAILED = 0x02
_SMART_BIT_SMART_FAILED = 0x04

REASON_OK = "ok"
REASON_ASLEEP = "asleep"
REASON_NO_PERMISSION = "no_permission"
REASON_UNSUPPORTED = "unsupported"
REASON_USB_BRIDGE = "usb_bridge"
REASON_TIMEOUT = "timeout"
REASON_UNREADABLE = "unreadable"
REASON_NO_TOOL = "no_tool"

#: Operator-facing explanation per reason. Deliberately says what to *do*, and
#: distinguishes "expected, nothing to fix" from "fixable".
REASON_NOTES = {
    REASON_ASLEEP: (
        "Drive is spun down (standby). DiskPulse deliberately does not wake it — "
        "polling would keep it awake permanently and add needless wear. "
        "Temperature will appear once something reads from the drive."
    ),
    REASON_NO_PERMISSION: (
        "S.M.A.R.T. pass-through was refused. Run DiskPulse as Administrator "
        "(Windows) or with sudo (Linux) to read temperature and health."
    ),
    REASON_UNSUPPORTED: (
        "This device exposes no S.M.A.R.T. data at all. Normal for USB flash "
        "drives and card readers — there is nothing to fix."
    ),
    REASON_USB_BRIDGE: (
        "The USB bridge in this enclosure did not pass S.M.A.R.T. through. "
        "DiskPulse tried the common bridge types; this one needs an explicit "
        "smartctl -d setting, or simply does not support it."
    ),
    REASON_TIMEOUT: (
        "The drive did not answer in time — usually a spun-down disk that is "
        "slow to wake. It should appear on a later refresh."
    ),
    REASON_UNREADABLE: (
        "S.M.A.R.T. data could not be read from this drive."
    ),
    REASON_NO_TOOL: (
        "Install smartmontools (e.g. `winget install smartmontools` on Windows, "
        "`apt install smartmontools` on Linux) to show temperature, power-on "
        "hours and detailed S.M.A.R.T. health on SATA and USB drives."
    ),
}


def _classify_smart_read(proc: _ProcResult, info: Dict[str, Any]) -> str:
    """Work out why a smartctl run yielded no usable S.M.A.R.T. data.

    Reads smartctl's own self-report first (the JSON carries ``smartctl.messages``
    and ``smartctl.exit_status``), then falls back to the exit bitmask and finally
    to string matching on stderr.
    """
    if proc.timed_out:
        return REASON_TIMEOUT

    meta = (info.get("smartctl", {}) or {}) if isinstance(info, dict) else {}
    messages = " ".join(
        str((m or {}).get("string", "")) for m in (meta.get("messages") or [])
        if isinstance(m, dict)
    )
    blob = f"{messages}\n{proc.stderr}".lower()

    # Order matters: a standby drive also reports a failed S.M.A.R.T. command,
    # so the specific causes have to be tested before the generic ones.
    if "standby" in blob or "sleep mode" in blob or "spun down" in blob:
        return REASON_ASLEEP
    if ("permission denied" in blob or "operation not permitted" in blob
            or "access is denied" in blob or "requires administrator" in blob
            or "administrator privileges" in blob):
        return REASON_NO_PERMISSION
    if "unknown usb bridge" in blob or "unsupported usb bridge" in blob \
            or "please specify device type" in blob:
        return REASON_USB_BRIDGE
    if ("smart support is: unavailable" in blob or "device lacks smart" in blob
            or "not supported" in blob or "unavailable - device lacks" in blob):
        return REASON_UNSUPPORTED

    status = meta.get("exit_status")
    if status is None:
        status = proc.returncode
    status = _to_int(status)
    if status:
        if status & _SMART_BIT_OPEN_FAILED:
            # Opening failed with no clearer message: on Windows this is almost
            # always elevation, on Linux a missing device node.
            return REASON_NO_PERMISSION if _IS_WINDOWS else REASON_UNREADABLE
        if status & _SMART_BIT_SMART_FAILED:
            return REASON_UNSUPPORTED
        if status & _SMART_BIT_CMDLINE:
            return REASON_UNREADABLE

    return REASON_UNREADABLE


def _is_elevated() -> bool:
    """Whether this process can actually issue S.M.A.R.T. pass-through commands."""
    if _IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _reason_note(reason: Optional[str]) -> str:
    """The operator-facing note for a reason, adjusted for our actual privileges.

    Telling somebody who is *already* running as Administrator to run as
    Administrator is the single most misleading thing this card used to do — it
    sends them chasing a permission problem that doesn't exist. When we know we're
    elevated, a refused pass-through means the controller or bridge won't do it.
    """
    reason = reason or REASON_UNREADABLE
    if reason == REASON_NO_PERMISSION and _is_elevated():
        return ("The disk controller refused the S.M.A.R.T. pass-through even though "
                "DiskPulse is running elevated — typically a RAID/RST controller or a "
                "USB bridge that doesn't forward ATA commands. Switching the SATA "
                "controller to AHCI mode usually fixes it.")
    return REASON_NOTES.get(reason, REASON_NOTES[REASON_UNREADABLE])



def _find_smartctl() -> Optional[str]:
    """Locate smartctl on PATH, or in the default smartmontools install dir on Windows."""
    exe = shutil.which("smartctl")
    if exe:
        return exe
    if _IS_WINDOWS:
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            cand = os.path.join(base, "smartmontools", "bin", "smartctl.exe")
            if os.path.exists(cand):
                return cand
    return None


def _norm_serial(serial: Optional[str]) -> Optional[str]:
    """Normalize a serial number for cross-tool matching (strip spaces/punctuation)."""
    if not serial:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", serial).upper()
    return cleaned or None


def _base_drive(drive_id: str, name: str) -> Dict[str, Any]:
    return {
        "id": drive_id,
        "name": name,
        "model": name,
        "capacity_bytes": None,
        "capacity_human": "—",
        "media_type": "Unknown",
        "interface": "—",
        "health_percent": None,
        "temperature_c": None,
        "temp_status": "Unknown",
        "power_on_hours": None,
        "reallocated_sectors": None,
        "status": "Unknown",
        "serial": None,
        "data_source": "unknown",
    }


def _placeholder() -> Dict[str, Any]:
    d = _base_drive("none", "No drives detected")
    d["model"] = "—"
    d["note"] = (
        "No drive data available. On Windows run DiskPulse as Administrator; "
        "on Linux install smartmontools and run with sudo for full S.M.A.R.T. metrics."
    )
    d["data_source"] = "none"
    return d


# ─────────────────────────────── Windows ──────────────────────────────────────

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$disks = Get-PhysicalDisk
$out = foreach ($d in $disks) {
  $rc = $null
  try { $rc = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch { $rc = $null }
  [PSCustomObject]@{
    DeviceId     = "$($d.DeviceId)"
    FriendlyName = "$($d.FriendlyName)"
    MediaType    = "$($d.MediaType)"
    BusType      = "$($d.BusType)"
    Size         = [int64]$d.Size
    HealthStatus = "$($d.HealthStatus)"
    SerialNumber = "$($d.SerialNumber)"
    Temperature  = $rc.Temperature
    PowerOnHours = $rc.PowerOnHours
    Wear         = $rc.Wear
    ReadErrors   = $rc.ReadErrorsTotal
    WriteErrors  = $rc.WriteErrorsTotal
  }
}
$out | ConvertTo-Json -Depth 3 -Compress
"""


def _collect_windows_powershell() -> List[Dict[str, Any]]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return []
    raw = _run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
        timeout=25.0,
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    drives: List[Dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        media_raw = (item.get("MediaType") or "").strip()
        bus = (item.get("BusType") or "").strip()
        is_nvme = bus.upper() == "NVME"
        if is_nvme:
            media_type = "NVMe"
        elif media_raw.upper() == "SSD":
            media_type = "SSD"
        elif media_raw.upper() == "HDD":
            media_type = "HDD"
        else:
            media_type = "Unknown"
        is_ssd = media_type in ("SSD", "NVMe")

        friendly = (item.get("FriendlyName") or "").strip() or f"Physical Disk {item.get('DeviceId', i)}"
        size = _to_int(item.get("Size"))
        wear = _to_float(item.get("Wear"))
        health_status = (item.get("HealthStatus") or "").strip()
        temp = _to_float(item.get("Temperature"))
        if temp is not None and temp <= 0:
            temp = None  # 0 means "not reported"
        poh = _to_int(item.get("PowerOnHours"))
        if poh is not None and poh <= 0:
            poh = None

        # Health %: prefer SSD wear indicator, else map Windows HealthStatus.
        if wear is not None:
            health_percent = max(0, min(100, int(round(100 - wear))))
        elif health_status.lower() == "healthy":
            health_percent = 100
        elif health_status.lower() == "warning":
            health_percent = 50
        elif health_status.lower() in ("unhealthy", "failed"):
            health_percent = 10
        else:
            health_percent = 100

        if health_status.lower() == "healthy":
            status = "Optimal"
        elif health_status.lower() == "warning":
            status = "Warning"
        elif health_status.lower() in ("unhealthy", "failed"):
            status = "Failing"
        else:
            status = "OK"

        d = _base_drive(f"drive_{item.get('DeviceId', i)}", friendly)
        d.update({
            "capacity_bytes": size,
            "capacity_human": _human_size(size),
            "media_type": media_type,
            "interface": bus or "—",
            "health_percent": health_percent,
            "temperature_c": round(temp, 1) if temp is not None else None,
            "temp_status": _temp_status(temp, is_ssd),
            "power_on_hours": poh,
            "reallocated_sectors": None,
            "status": status,
            "serial": (item.get("SerialNumber") or "").strip() or None,
            # Windows' own physical-disk index. smartctl addresses the same disk as
            # /dev/pdN, so this is an exact key between the two tools.
            "device_id": _to_int(item.get("DeviceId")),
            "data_source": "powershell",
        })
        drives.append(d)
    return drives


def _match_smart(base: Dict[str, Any], smart_drives: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the smartctl reading that corresponds to a PowerShell drive entry."""
    # Exact match first: when we enumerated by /dev/pdN, both records carry the
    # same Windows physical-disk index, so there is nothing to infer. This also
    # matches drives whose SMART read failed — the serial/model fallbacks below
    # can't, because a failed read has neither.
    bid = _to_int(base.get("device_id"))
    if bid is not None:
        for s in smart_drives:
            if _to_int(s.get("device_id")) == bid:
                return s

    bser = _norm_serial(base.get("serial"))
    if bser:
        for s in smart_drives:
            if _norm_serial(s.get("serial")) == bser:
                return s
    # Fallback: match on model + capacity (within ~2 GB to allow unit rounding).
    bmodel = (base.get("model") or "").strip().lower()
    bcap = base.get("capacity_bytes")
    for s in smart_drives:
        smodel = (s.get("model") or "").strip().lower()
        if not smodel or smodel != bmodel:
            continue
        scap = s.get("capacity_bytes")
        if bcap and scap and abs(int(bcap) - int(scap)) > 2 * 1024 * 1024 * 1024:
            continue
        return s
    return None


def _collect_windows() -> List[Dict[str, Any]]:
    """
    Base drive list comes from PowerShell (so every disk, including USB flash
    that has no SMART, still shows). If smartmontools is installed we enrich each
    matching drive with real temperature / power-on hours / reallocated sectors —
    Windows' own Get-StorageReliabilityCounter only reports those for NVMe, so
    SATA & USB-SATA drives need smartctl to expose them.
    """
    base = _collect_windows_powershell()
    smartctl_exe = _find_smartctl()

    if not smartctl_exe:
        # No smartmontools: leave a hint on drives that are missing thermal data.
        for d in base:
            if d.get("temperature_c") is None and not d.get("note"):
                d["smart_reason"] = REASON_NO_TOOL
                d["note"] = REASON_NOTES[REASON_NO_TOOL]
        return base

    # Enumerate by Windows disk number rather than trusting smartctl's own scan,
    # which skips anything it couldn't open at scan time.
    smart_drives = _collect_smartctl_devices(smartctl_exe,
                                             _windows_scan_devices(smartctl_exe, base))
    if not base:
        return smart_drives
    if not smart_drives:
        return base

    for d in base:
        match = _match_smart(d, smart_drives)
        if not match:
            # smartmontools is installed but this disk didn't match any SMART
            # device at all. Don't guess a health number; say what's missing.
            if d.get("status") in ("Optimal", "OK"):
                d["status"] = "Unknown"
                d["health_percent"] = None
            if not d.get("note"):
                d["smart_reason"] = REASON_UNREADABLE
                d["note"] = _reason_note(REASON_UNREADABLE)
            continue

        reason = match.get("smart_reason") or REASON_UNREADABLE
        enriched = False
        if match.get("temperature_c") is not None:
            d["temperature_c"] = match["temperature_c"]
            enriched = True
        if match.get("power_on_hours") is not None:
            d["power_on_hours"] = match["power_on_hours"]
            enriched = True
        if match.get("reallocated_sectors") is not None:
            d["reallocated_sectors"] = match["reallocated_sectors"]
            enriched = True
        if match.get("device"):
            d["device"] = match["device"]
        d["smart_reason"] = reason

        # Not every SSD reports its rotation rate, so smartctl can call a drive
        # "Unknown" that Windows correctly knows is an SSD. Merge the two, then
        # re-band the temperature — otherwise a card can read "SSD · 51 °C
        # Warning" while using the stricter HDD thresholds it isn't subject to.
        if d.get("media_type") in (None, "Unknown") and match.get("media_type") not in (None, "Unknown"):
            d["media_type"] = match["media_type"]
        if d.get("temperature_c") is not None:
            d["temp_status"] = _temp_status(d["temperature_c"],
                                            d.get("media_type") in ("SSD", "NVMe"))

        if match.get("status") in ("Unknown", "Asleep"):
            # smartctl reached the drive but couldn't read its SMART log. The
            # reason from the probe says whether that's expected (a parked disk,
            # a stick with no S.M.A.R.T.) or fixable, so pass it straight
            # through instead of always blaming permissions.
            d["health_percent"] = None
            if d.get("status") in ("Optimal", "OK"):
                d["status"] = match["status"]
            if match.get("note"):
                d["note"] = match["note"]
        else:
            # Prefer smartctl's health/status when it actually read the SMART log.
            if match.get("health_percent") is not None:
                d["health_percent"] = match["health_percent"]
            if match.get("status") and match["status"] in ("Warning", "Failing"):
                d["status"] = match["status"]

        if enriched:
            d["data_source"] = "powershell+smartctl"
            if reason == REASON_OK:
                d.pop("note", None)
    return base


# ──────────────────────────────── Linux ───────────────────────────────────────

def _collect_linux() -> List[Dict[str, Any]]:
    drives: List[Dict[str, Any]] = []
    exe = _find_smartctl()
    if exe:
        # Anything --scan reported is a real device, so keep a stub with the
        # failure reason rather than dropping it from the card silently.
        devices = [dict(d, keep_unreadable=True) for d in _smartctl_scan(exe)]
        drives = _collect_smartctl_devices(exe, devices)
    if not drives:
        drives = _collect_linux_lsblk()
    return drives


def _smartctl_scan(exe: str = "smartctl") -> List[Dict[str, str]]:
    for args in ([exe, "--scan-open", "-j"], [exe, "--scan", "-j"]):
        raw = _run(args, timeout=10.0)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        devs = data.get("devices") or []
        if devs:
            return [{"name": d.get("name"), "type": d.get("type")} for d in devs if d.get("name")]
    return []


def _windows_scan_devices(exe: str, base: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Device list for Windows, keyed to PowerShell's physical-disk numbering.

    `smartctl --scan` on Windows only lists devices it managed to open, so a disk
    that was asleep or behind an unrecognized USB bridge at scan time never gets
    probed at all — it just silently disappears from the health card. But smartctl
    also accepts `/dev/pdN`, where N is the Windows physical drive number, which is
    exactly `Get-PhysicalDisk`'s DeviceId. Enumerating from PowerShell therefore
    gives an authoritative, complete list and an exact identity for each device,
    with no serial/model guesswork needed to match the two tools up afterwards.

    Anything smartctl's own scan found that PowerShell didn't cover is appended, so
    this can only ever add devices.
    """
    devices: List[Dict[str, Any]] = []
    seen: set = set()
    for d in base:
        did = _to_int(d.get("device_id"))
        if did is None:
            continue
        name = f"/dev/pd{did}"
        if name in seen:
            continue
        seen.add(name)
        devices.append({
            "name": name,
            "type": None,
            "device_id": did,
            "model": d.get("model"),
            # A device we know exists must never be dropped for being unreadable:
            # the whole point is to report *why* it couldn't be read.
            "keep_unreadable": True,
        })

    for dev in _smartctl_scan(exe):
        if dev.get("name") and dev["name"] not in seen:
            seen.add(dev["name"])
            devices.append(dict(dev))
    return devices


# ────────────────────── reading one device with smartctl ──────────────────────

#: How long to let smartctl work on one device. A parked HDD can need 15-20s to
#: spin up and answer, and the old 12s ceiling sat below that — which is how an
#: idle disk got dropped from the list entirely while its busy neighbour reported
#: fine. Probes run concurrently, so this ceiling costs wall time once overall
#: rather than once per drive.
_SMART_TIMEOUT = 40.0

#: Tighter ceiling for the speculative USB bridge-type guesses, where a wrong
#: guess should fail fast instead of eating the whole budget.
_SMART_PROBE_TIMEOUT = 12.0

#: Concurrent smartctl invocations — each is a separate short-lived subprocess.
_SMART_WORKERS = 6

#: Leave a spun-down disk spun down. smartd defaults the same way: waking a drive
#: on every refresh would stop it ever sleeping and add pointless start/stop
#: cycles. The cost is that a sleeping drive reports no live temperature, so we
#: label it "Asleep" and show its last known reading instead of inventing one.
#: Set DISKPULSE_WAKE_DRIVES=1 to always wake drives and get live temperatures.
_WAKE_DRIVES = os.environ.get("DISKPULSE_WAKE_DRIVES", "").lower() in ("1", "true", "yes")

#: USB enclosures need to be told how to pass S.M.A.R.T. through. These are the
#: bridge types worth trying, most common first; plain USB flash sticks match
#: none of them because they genuinely have no S.M.A.R.T. to expose.
_USB_BRIDGE_TYPES = ("sat", "sat,12", "usbjmicron", "usbsunplus", "usbprolific", "scsi")

#: Reasons where trying a different -d device type could plausibly help. A drive
#: that is merely asleep or needs elevation will fail identically every time, so
#: retrying it is wasted seconds.
_RETRYABLE_REASONS = (REASON_USB_BRIDGE, REASON_UNSUPPORTED, REASON_UNREADABLE)


class _SmartRead(NamedTuple):
    info: Dict[str, Any]
    reason: str
    device_type: Optional[str]


def _smart_read_once(exe: str, name: str, dtype: Optional[str],
                     timeout: float, wake: bool) -> _SmartRead:
    """One `smartctl -a -j` invocation, classified."""
    cmd = [exe, "-a", "-j"]
    if not wake:
        # Exit rather than spinning an idle disk up just to read a sensor.
        cmd += ["-n", "standby"]
    if dtype:
        cmd += ["-d", dtype]
    cmd.append(name)

    proc = _run_full(cmd, timeout)
    info: Dict[str, Any] = {}
    if proc.stdout:
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                info = parsed
        except (ValueError, json.JSONDecodeError):
            info = {}

    if _smart_payload_usable(info):
        return _SmartRead(info, REASON_OK, dtype)

    reason = _classify_smart_read(proc, info)

    # `-n standby` is not honoured by every smartctl build/transport; if the
    # option itself was rejected, retry once without it rather than reporting a
    # perfectly healthy drive as unreadable.
    if not wake and reason == REASON_UNREADABLE:
        meta = (info.get("smartctl", {}) or {})
        status = _to_int(meta.get("exit_status"))
        if status is not None and status & _SMART_BIT_CMDLINE:
            return _smart_read_once(exe, name, dtype, timeout, wake=True)

    return _SmartRead(info, reason, dtype)


def _smart_payload_usable(info: Dict[str, Any]) -> bool:
    """Did this response actually carry S.M.A.R.T. data (not just an identity)?"""
    if not info:
        return False
    if info.get("ata_smart_attributes") or info.get("nvme_smart_health_information_log"):
        return True
    if (info.get("smart_status", {}) or {}).get("passed") is not None:
        return True
    if (info.get("temperature", {}) or {}).get("current") is not None:
        return True
    return False


def _smart_read_device(exe: str, name: str, dtype: Optional[str] = None) -> _SmartRead:
    """Read one device, escalating through USB bridge types when it makes sense."""
    first = _smart_read_once(exe, name, dtype, _SMART_TIMEOUT, _WAKE_DRIVES)
    if first.reason == REASON_OK or first.reason not in _RETRYABLE_REASONS:
        return first

    for cand in _USB_BRIDGE_TYPES:
        if cand == dtype:
            continue
        attempt = _smart_read_once(exe, name, cand, _SMART_PROBE_TIMEOUT, _WAKE_DRIVES)
        if attempt.reason == REASON_OK:
            return attempt

    # Nothing worked. Prefer the first attempt's reason: it came from the device's
    # own declared type, so it describes the drive rather than a wrong guess.
    return first


def _temp_from_attributes(ata_attrs: List[Dict[str, Any]]) -> Optional[float]:
    """Temperature from ATA attribute 194/190 when `temperature.current` is absent.

    Not every drive populates smartctl's normalized temperature field, but almost
    every ATA drive carries attribute 194 (Temperature_Celsius) or 190
    (Airflow_Temperature_Cel). The raw value is awkward: some drives store the
    plain reading, others pack min/max into the upper bytes, so we prefer the
    string smartctl already decoded ("31 (Min/Max 20/45)") and fall back to
    masking. Anything outside a plausible range is rejected rather than shown.
    """
    def plausible(val: Optional[float]) -> Optional[float]:
        if val is None:
            return None
        return val if 1.0 <= val <= 120.0 else None

    by_id: Dict[int, Dict[str, Any]] = {}
    for attr in ata_attrs or []:
        aid = _to_int(attr.get("id"))
        aname = (attr.get("name") or "").lower()
        if aid in (194, 190) or "temperature" in aname or "airflow_temp" in aname:
            if aid is not None and aid not in by_id:
                by_id[aid] = attr

    for aid in (194, 190):
        attr = by_id.get(aid)
        if not attr:
            continue
        raw = attr.get("raw", {}) or {}

        # smartctl's decoded string leads with the current reading.
        match = re.match(r"\s*(\d+)", str(raw.get("string") or ""))
        if match:
            found = plausible(_to_float(match.group(1)))
            if found is not None:
                return found

        value = _to_int(raw.get("value"))
        if value is None:
            continue
        for candidate in (value, value & 0xFFFF, value & 0xFF):
            found = plausible(float(candidate))
            if found is not None:
                return found
    return None



def _collect_smartctl(exe: str = "smartctl") -> List[Dict[str, Any]]:
    return _collect_smartctl_devices(exe, _smartctl_scan(exe))


def _collect_smartctl_devices(exe: str, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Read every device concurrently and parse each result.

    Concurrency matters for correctness, not just speed: giving a parked HDD the
    40s it may need to answer would otherwise serialize into minutes across six
    drives, far beyond the cache TTL.
    """
    devices = [d for d in devices if d.get("name")][:16]
    if not devices:
        return []

    reads: List[Optional[_SmartRead]] = [None] * len(devices)
    if len(devices) == 1:
        reads[0] = _smart_read_device(exe, devices[0]["name"], devices[0].get("type"))
    else:
        with futures.ThreadPoolExecutor(max_workers=min(_SMART_WORKERS, len(devices)),
                                        thread_name_prefix="smartctl") as pool:
            pending = {
                pool.submit(_smart_read_device, exe, d["name"], d.get("type")): idx
                for idx, d in enumerate(devices)
            }
            for fut in futures.as_completed(pending):
                idx = pending[fut]
                try:
                    reads[idx] = fut.result()
                except Exception:
                    reads[idx] = _SmartRead({}, REASON_UNREADABLE, devices[idx].get("type"))

    drives: List[Dict[str, Any]] = []
    for i, dev in enumerate(devices):
        read = reads[i] or _SmartRead({}, REASON_UNREADABLE, dev.get("type"))
        parsed = _parse_smart_device(i, dev["name"], read, hint=dev)
        if parsed:
            drives.append(parsed)
    return drives


def _parse_smart_device(i: int, name: str, read: _SmartRead,
                        hint: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Turn one smartctl response into a drive record.

    A failed read still produces a record. Dropping it (as this used to) meant the
    drive silently lost its S.M.A.R.T. entry and the dashboard fell back to a
    generic "run as Administrator" guess — even when the real cause was a
    sleeping disk or a device with no S.M.A.R.T. at all.
    """
    info = read.info or {}
    hint = hint or {}

    model = info.get("model_name") or info.get("scsi_model_name")
    serial = info.get("serial_number")

    if read.reason != REASON_OK and not model and not serial:
        # Nothing readable. Keep a stub so the drive can still be matched to its
        # PowerShell entry and explained, rather than vanishing.
        if not hint.get("keep_unreadable"):
            return None
        d = _base_drive(f"drive_{i}_{name.replace('/', '_')}",
                        hint.get("model") or name)
        d.update({
            "device": name,
            "device_id": hint.get("device_id"),
            "status": "Asleep" if read.reason == REASON_ASLEEP else "Unknown",
            "health_percent": None,
            "smart_reason": read.reason,
            "data_source": "smartctl",
            "note": _reason_note(read.reason),
        })
        return d

    if not model:
        # Serial alone is still enough to match the drive later.
        model = f"Unknown device ({serial})" if serial else (hint.get("model") or name)

    protocol = (info.get("device", {}) or {}).get("protocol", "") or ""
    is_nvme = "nvme" in protocol.lower() or (info.get("device", {}) or {}).get("type") == "nvme"
    rotation = _to_int(info.get("rotation_rate"))
    if is_nvme:
        media_type = "NVMe"
    elif rotation == 0:
        media_type = "SSD"
    elif rotation:
        media_type = "HDD"
    else:
        media_type = "Unknown"
    is_ssd = media_type in ("SSD", "NVMe")

    capacity = _to_int((info.get("user_capacity", {}) or {}).get("bytes")) or _to_int(info.get("nvme_total_capacity"))

    temp = _to_float((info.get("temperature", {}) or {}).get("current"))
    poh = _to_int((info.get("power_on_time", {}) or {}).get("hours"))
    smart_status = info.get("smart_status", {}) or {}
    passed = smart_status.get("passed")

    reallocated = None
    pending = None
    uncorrectable = None
    health_percent = None

    nvme_log = info.get("nvme_smart_health_information_log")
    if nvme_log:
        if temp is None:
            temp = _to_float(nvme_log.get("temperature"))
        if poh is None:
            poh = _to_int(nvme_log.get("power_on_hours"))
        used = _to_float(nvme_log.get("percentage_used"))
        if used is not None:
            health_percent = max(0, min(100, int(round(100 - used))))

    ata_attrs = ((info.get("ata_smart_attributes", {}) or {}).get("table")) or []
    for attr in ata_attrs:
        aid = attr.get("id")
        aname = (attr.get("name") or "").lower()
        raw_val = _to_int((attr.get("raw", {}) or {}).get("value"))
        if aid == 5 or "reallocated_sector" in aname:
            reallocated = raw_val
        elif aid == 197 or "current_pending" in aname:
            pending = raw_val
        elif aid == 198 or "uncorrectable" in aname:
            uncorrectable = raw_val
        # SSD lifetime / wear indicators — normalized value ≈ % life remaining
        if health_percent is None and (
            aid in (177, 202, 231, 233) or "wear_leveling" in aname or "life" in aname
        ):
            nv = _to_int(attr.get("value"))
            if nv is not None and 0 <= nv <= 100:
                health_percent = nv

    # Older drives (and several Seagate/WD firmwares) don't populate smartctl's
    # decoded `temperature.current` block at all, but still carry attribute 194
    # or 190. Without this fallback those drives showed "Temp N/A" next to a
    # perfectly healthy SMART log.
    if temp is None:
        temp = _temp_from_attributes(ata_attrs)
    if temp is None:
        temp = _to_float((info.get("scsi_environmental_reports", {}) or {})
                         .get("temperature_1", {}).get("current"))

    surface_damage = bool((reallocated or 0) > 0 or (pending or 0) > 0
                          or (uncorrectable or 0) > 0)

    # Identity was readable but the SMART log itself was not. That has several
    # very different causes (a parked disk, a USB bridge with no pass-through, a
    # stick with no S.M.A.R.T. at all), so the reason from the probe decides the
    # wording. Reporting "Optimal / 100%" here would be a guess — the drive must
    # show as Unknown until the data can actually be read.
    smart_unreadable = passed is None and not ata_attrs and not nvme_log

    if smart_unreadable:
        health_percent = None
        status = "Asleep" if read.reason == REASON_ASLEEP else "Unknown"
        note = _reason_note(
            read.reason if read.reason != REASON_OK else REASON_UNSUPPORTED)
    else:
        note = None
        if health_percent is None:
            if passed is False:
                health_percent = 20
            else:
                # HDDs (and SSDs with no readable lifetime attribute) have
                # no wear figure. Derive health from the surface-damage
                # counters instead of claiming a flat 100% — a drive with
                # reallocated or pending sectors is exactly the one the
                # status line flags as "Warning", and the two numbers must
                # agree.
                penalty = _ata_surface_penalty(reallocated, pending, uncorrectable)
                health_percent = max(10, 100 - penalty)

        if passed is True:
            status = "Warning" if surface_damage else "Optimal"
        elif passed is False:
            status = "Failing"
        else:
            status = "OK"

    d = _base_drive(f"drive_{i}_{name.replace('/', '_')}", model)
    d.update({
        "capacity_bytes": capacity,
        "capacity_human": _human_size(capacity),
        "media_type": media_type,
        "interface": (protocol or _interface_from_type(read.device_type) or "—"),
        "health_percent": health_percent,
        "temperature_c": round(temp, 1) if temp is not None else None,
        "temp_status": _temp_status(temp, is_ssd),
        "power_on_hours": poh,
        "reallocated_sectors": reallocated,
        "status": status,
        "serial": serial,
        "device": name,
        "device_id": hint.get("device_id"),
        "smart_reason": read.reason,
        "data_source": "smartctl",
    })
    if note:
        d["note"] = note
    return d


def _interface_from_type(device_type: Optional[str]) -> Optional[str]:
    """Describe the transport when smartctl's `device.protocol` is missing.

    The `-d` type we had to use is itself evidence: if `sat` or `usbjmicron` got
    us in, the drive is behind a USB bridge.
    """
    if not device_type:
        return None
    dt = device_type.lower()
    if dt.startswith("usb") or dt.startswith("sat"):
        return "USB" if dt.startswith("usb") else "SATA"
    if dt.startswith("nvme"):
        return "NVMe"
    if dt in ("scsi", "sas"):
        return dt.upper()
    return None


def _linux_sensor_temps() -> Dict[str, List[float]]:
    """Group drive-related temperatures from psutil by rough category."""
    out: Dict[str, List[float]] = {"nvme": [], "sata": []}
    if not hasattr(psutil, "sensors_temperatures"):
        return out
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return out
    for chip, entries in (temps or {}).items():
        cl = chip.lower()
        for e in entries:
            val = getattr(e, "current", None)
            if val is None or val <= 0:
                continue
            if "nvme" in cl:
                out["nvme"].append(float(val))
            elif "drivetemp" in cl or "sata" in cl or "ata" in cl:
                out["sata"].append(float(val))
    return out


def _collect_linux_lsblk() -> List[Dict[str, Any]]:
    raw = _run(
        ["lsblk", "-dbJ", "-o", "NAME,MODEL,SERIAL,SIZE,ROTA,TYPE,TRAN"],
        timeout=8.0,
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []

    sensor_temps = _linux_sensor_temps()
    nvme_i = 0
    sata_i = 0

    drives: List[Dict[str, Any]] = []
    for i, bd in enumerate(data.get("blockdevices", []) or []):
        if bd.get("type") != "disk":
            continue
        name = bd.get("name", "")
        if name.startswith(("loop", "ram", "sr", "zram", "dm-")):
            continue
        size = _to_int(bd.get("size"))
        if size is not None and size < 256 * 1024 * 1024:
            continue  # skip tiny pseudo/config disks (cloud config volumes, etc.)
        tran = (bd.get("tran") or "").lower()
        rota = bd.get("rota")
        if tran == "nvme":
            media_type = "NVMe"
        elif rota in (True, 1, "1"):
            media_type = "HDD"
        elif rota in (False, 0, "0"):
            media_type = "SSD"
        else:
            media_type = "Unknown"
        is_ssd = media_type in ("SSD", "NVMe")

        # Best-effort temperature from psutil sensors, assigned by category order.
        temp = None
        if media_type == "NVMe" and nvme_i < len(sensor_temps["nvme"]):
            temp = sensor_temps["nvme"][nvme_i]
            nvme_i += 1
        elif sata_i < len(sensor_temps["sata"]):
            temp = sensor_temps["sata"][sata_i]
            sata_i += 1

        model = (bd.get("model") or "").strip() or f"/dev/{name}"

        d = _base_drive(f"drive_{i}_{name}", model)
        d.update({
            "capacity_bytes": size,
            "capacity_human": _human_size(size),
            "media_type": media_type,
            "interface": (tran.upper() if tran else "—"),
            "health_percent": None,
            "temperature_c": round(temp, 1) if temp is not None else None,
            "temp_status": _temp_status(temp, is_ssd),
            "power_on_hours": None,
            "reallocated_sectors": None,
            "status": "OK",
            "serial": (bd.get("serial") or "").strip() or None,
            "device": f"/dev/{name}",
            "smart_reason": REASON_NO_TOOL,
            "data_source": "lsblk",
            "note": _reason_note(REASON_NO_TOOL),
        })
        drives.append(d)
    return drives


# ─────────────────────────── dispatch + caching ───────────────────────────────

# Health % and S.M.A.R.T. status come from different sources that can disagree
# (PowerShell HealthStatus vs smartctl, wear attribute vs reallocated sectors),
# which is how the dashboard ended up showing "Warning" next to a 100% health
# bar. One reconciliation pass enforces that they always line up.
_STATUS_HEALTH_CAPS = {"Warning": 85, "Failing": 30}


def _reconcile_drive(d: Dict[str, Any]) -> Dict[str, Any]:
    hp = d.get("health_percent")
    status = d.get("status")
    # A drive whose computed health is already poor must not be labelled OK.
    if hp is not None and status in ("Optimal", "OK") and hp <= 40:
        status = "Warning"
        d["status"] = status
    cap = _STATUS_HEALTH_CAPS.get(status)
    if hp is not None and cap is not None and hp > cap:
        d["health_percent"] = cap
    return d


def _collect_all() -> List[Dict[str, Any]]:
    try:
        if _IS_WINDOWS:
            drives = _collect_windows()
        elif _IS_LINUX:
            drives = _collect_linux()
        else:
            drives = []
    except Exception:
        drives = []
    drives = [_reconcile_drive(d) for d in drives]
    if not drives:
        drives = [_placeholder()]
    return drives


class DriveHealthMonitor:
    """Non-blocking, TTL-cached, background-refreshed drive health provider."""

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._last: float = 0.0
        self._lock = threading.Lock()
        self._refreshing = False

    def get(self) -> List[Dict[str, Any]]:
        now = time.time()
        start_refresh = False
        with self._lock:
            never_run = self._last == 0.0
            stale = (now - self._last) > _TTL_SECONDS
            if (never_run or stale) and not self._refreshing:
                self._refreshing = True
                start_refresh = True
            snapshot = list(self._cache)
        if start_refresh:
            threading.Thread(target=self._refresh, name="drive-health-refresh", daemon=True).start()
        return snapshot

    def _refresh(self) -> None:
        try:
            data = _collect_all()
        except Exception:
            data = None
        with self._lock:
            if data is not None:
                self._cache = data
            self._last = time.time()
            self._refreshing = False

    def refresh_blocking(self) -> List[Dict[str, Any]]:
        """Force a synchronous refresh (used for testing / diagnostics)."""
        data = _collect_all()
        with self._lock:
            self._cache = data
            self._last = time.time()
            self._refreshing = False
        return list(data)


drive_health_monitor = DriveHealthMonitor()

# Warm the cache at startup (non-blocking) so the first telemetry frame that
# reaches the browser already carries real drive data instead of an empty grid.
try:
    drive_health_monitor.get()
except Exception:
    pass


def get_drive_health() -> List[Dict[str, Any]]:
    """Public entry point — returns the most recent drive-health snapshot."""
    return drive_health_monitor.get()
