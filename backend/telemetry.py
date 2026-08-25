import time
import os
import socket
import platform
import subprocess
import shutil
import psutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from backend.config import STORAGE_ROOT, PORT, format_bytes, format_uptime
from backend.drive_health import get_drive_health, _run


def get_primary_local_ip() -> str:
    """Find the host's actual LAN IP on 192.168.x.x / 10.x.x.x / 172.16-31.x.x."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        addrs = psutil.net_if_addrs()
        for iface, if_addrs in addrs.items():
            for addr in if_addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    if addr.address.startswith(("192.168.", "10.", "172.")):
                        return addr.address
    except Exception:
        pass

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


class TelemetryEngine:
    def __init__(self):
        self.last_disk_io = psutil.disk_io_counters()
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()
        self.boot_time = psutil.boot_time()
        self._cached_ip = None
        self._last_ip_check = 0
        self._lhm_process: Optional[subprocess.Popen] = None
        self._launch_librehardwaremonitor()

    def _launch_librehardwaremonitor(self) -> None:
        """On Windows, start LibreHardwareMonitor.exe hidden so its WMI
        sensors become available for CPU temperature queries."""
        if os.name != "nt":
            return
        try:
            from backend.embedded_tools import setup_embedded_tools
            setup_embedded_tools()
            lhm_exe = shutil.which("LibreHardwareMonitor.exe")
            if not lhm_exe:
                print("[LHM] LibreHardwareMonitor.exe not found on PATH; CPU temp will be N/A")
                return
            # Launch hidden/minimized so it registers WMI without popping a window.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            self._lhm_process = subprocess.Popen(
                [lhm_exe],
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[LHM] Launched LibreHardwareMonitor.exe (PID {self._lhm_process.pid})")
        except Exception as e:
            print(f"[LHM] Failed to launch: {e}")
            self._lhm_process = None

    def cleanup(self) -> None:
        """Terminate the embedded LibreHardwareMonitor process, if any."""
        proc = self._lhm_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._lhm_process = None

    def get_local_ip(self) -> str:
        now = time.time()
        if self._cached_ip is None or (now - self._last_ip_check) > 30:
            self._cached_ip = get_primary_local_ip()
            self._last_ip_check = now
        return self._cached_ip

    def get_system_overview(self):
        current_time = time.time()
        time_delta = max(current_time - self.last_time, 0.001)

        # CPU Metrics
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        cpu_freq = psutil.cpu_freq()
        cpu_count_logical = psutil.cpu_count(logical=True) or 1
        cpu_count_physical = psutil.cpu_count(logical=False) or 1

        # CPU Temperature (cross-platform via psutil / WMI / ACPI)
        cpu_temp_c, cpu_temp_reason = self._get_cpu_temperature()

        # Memory Metrics
        virtual_mem = psutil.virtual_memory()
        swap_mem = psutil.swap_memory()

        # Disk I/O Deltas
        curr_disk_io = psutil.disk_io_counters()
        read_bytes_sec = 0
        write_bytes_sec = 0
        read_iops = 0
        write_iops = 0

        if curr_disk_io and self.last_disk_io:
            read_bytes_sec = max(0, (curr_disk_io.read_bytes - self.last_disk_io.read_bytes) / time_delta)
            write_bytes_sec = max(0, (curr_disk_io.write_bytes - self.last_disk_io.write_bytes) / time_delta)
            read_iops = max(0, (curr_disk_io.read_count - self.last_disk_io.read_count) / time_delta)
            write_iops = max(0, (curr_disk_io.write_count - self.last_disk_io.write_count) / time_delta)
        self.last_disk_io = curr_disk_io

        # Network I/O Deltas
        curr_net_io = psutil.net_io_counters()
        net_recv_sec = 0
        net_sent_sec = 0
        if curr_net_io and self.last_net_io:
            net_recv_sec = max(0, (curr_net_io.bytes_recv - self.last_net_io.bytes_recv) / time_delta)
            net_sent_sec = max(0, (curr_net_io.bytes_sent - self.last_net_io.bytes_sent) / time_delta)
        self.last_net_io = curr_net_io
        self.last_time = current_time

        # Partitions & Mounts
        partitions_data = []
        try:
            partitions = psutil.disk_partitions(all=False)
            for part in partitions:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    partitions_data.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                        "total_human": format_bytes(usage.total),
                        "used_human": format_bytes(usage.used),
                        "free_human": format_bytes(usage.free),
                    })
                except (PermissionError, OSError):
                    continue
        except Exception:
            pass

        # Storage Pool Folder Breakdown
        pool_categories = self.get_storage_pool_breakdown()

        # Real / Simulated Temperatures & SMART
        temperatures = self.get_temperatures()

        # System info
        uptime_seconds = int(time.time() - self.boot_time)
        local_ip = self.get_local_ip()

        return {
            "timestamp": current_time,
            "system": {
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}",
                "architecture": platform.machine(),
                "python_version": platform.python_version(),
                "uptime_seconds": uptime_seconds,
                "uptime_human": format_uptime(uptime_seconds),
                "local_ip": local_ip,
                "local_port": PORT,
                "local_url": f"http://{local_ip}:{PORT}",
            },
            "cpu": {
                "percent_total": cpu_percent,
                "per_core": cpu_per_core,
                "cores_logical": cpu_count_logical,
                "cores_physical": cpu_count_physical,
                "freq_current_mhz": round(cpu_freq.current, 1) if cpu_freq else 0,
                "freq_max_mhz": round(cpu_freq.max, 1) if cpu_freq and cpu_freq.max else 0,
                "temp_c": cpu_temp_c,
                "temp_reason": cpu_temp_reason,
            },
            "memory": {
                "total": virtual_mem.total,
                "used": virtual_mem.used,
                "available": virtual_mem.available,
                "percent": virtual_mem.percent,
                "total_human": format_bytes(virtual_mem.total),
                "used_human": format_bytes(virtual_mem.used),
                "available_human": format_bytes(virtual_mem.available),
                "swap_total": swap_mem.total,
                "swap_used": swap_mem.used,
                "swap_percent": swap_mem.percent,
                "swap_human": format_bytes(swap_mem.used),
            },
            "disk_io": {
                "read_bytes_sec": read_bytes_sec,
                "write_bytes_sec": write_bytes_sec,
                "read_human_sec": f"{format_bytes(read_bytes_sec)}/s",
                "write_human_sec": f"{format_bytes(write_bytes_sec)}/s",
                "read_iops": round(read_iops, 1),
                "write_iops": round(write_iops, 1),
                "total_iops": round(read_iops + write_iops, 1),
            },
            "network_io": {
                "recv_bytes_sec": net_recv_sec,
                "sent_bytes_sec": net_sent_sec,
                "recv_human_sec": f"{format_bytes(net_recv_sec)}/s",
                "sent_human_sec": f"{format_bytes(net_sent_sec)}/s",
            },
            "partitions": partitions_data,
            "storage_pool": pool_categories,
            "smart_drives": temperatures,
        }

    def get_storage_pool_breakdown(self):
        root_path = Path(STORAGE_ROOT)
        categories = {
            "Media": 0,
            "ISOs": 0,
            "Documents": 0,
            "Software": 0,
            "Backups": 0,
            "Other": 0,
        }
        total_pool_bytes = 0
        file_count = 0
        dir_count = 0

        ext_map = {
            "mp4": "Media", "mkv": "Media", "webm": "Media", "mp3": "Media", "wav": "Media", "flac": "Media", "jpg": "Media", "png": "Media",
            "iso": "ISOs", "img": "ISOs", "vmdk": "ISOs",
            "pdf": "Documents", "docx": "Documents", "xlsx": "Documents", "txt": "Documents", "md": "Documents", "json": "Documents",
            "exe": "Software", "deb": "Software", "zip": "Software", "tar": "Software", "gz": "Software", "7z": "Software",
            "bak": "Backups", "dump": "Backups", "sql": "Backups"
        }

        try:
            for item in root_path.rglob("*"):
                if item.is_file():
                    file_count += 1
                    sz = item.stat().st_size
                    total_pool_bytes += sz
                    ext = item.suffix.lstrip(".").lower()
                    cat = ext_map.get(ext, "Other")
                    categories[cat] += sz
                elif item.is_dir():
                    dir_count += 1
        except Exception:
            pass

        # Try to get underlying disk stats for storage root
        try:
            usage = psutil.disk_usage(str(root_path))
            pool_total = usage.total
            pool_free = usage.free
            pool_used = usage.used
            pool_percent = usage.percent
        except Exception:
            pool_total = 1024 * 1024 * 1024 * 500  # 500 GB fallback
            pool_used = total_pool_bytes
            pool_free = pool_total - pool_used
            pool_percent = round((pool_used / pool_total) * 100, 1)

        category_list = []
        for name, size in categories.items():
            category_list.append({
                "name": name,
                "size_bytes": size,
                "size_human": format_bytes(size),
                "percent": round((size / max(total_pool_bytes, 1)) * 100, 1) if total_pool_bytes > 0 else 0
            })

        return {
            "root_path": str(root_path),
            "total_bytes": pool_total,
            "used_bytes": pool_used,
            "free_bytes": pool_free,
            "percent": pool_percent,
            "total_human": format_bytes(pool_total),
            "used_human": format_bytes(pool_used),
            "free_human": format_bytes(pool_free),
            "files_count": file_count,
            "dirs_count": dir_count,
            "categories": category_list,
        }

    def get_temperatures(self):
        """Real, cross-platform S.M.A.R.T. drive health & temperature.

        Delegates to the drive_health module which reads live data from the
        host (PowerShell on Windows, smartctl/lsblk on Linux) with a cached,
        background-refreshed snapshot so this stays cheap on every telemetry
        tick.
        """
        return get_drive_health()

    @staticmethod
    def _get_cpu_temperature() -> tuple[Optional[float], str]:
        """Best-effort CPU temperature from available sensors.

        Returns ``(temperature_celsius, reason)``.  When the temperature is
        ``None`` the reason explains why — useful for the UI so the user knows
        whether anything is fixable.
        """
        # 1. psutil.sensors_temperatures() (Linux / macOS)
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                cpu_labels = ("coretemp", "k10temp", "acpitz", "cpu_thermal",
                              "cpu_thermal_zone", "cpu")
                candidates: List[float] = []
                for chip, entries in (temps or {}).items():
                    cl = chip.lower()
                    if not any(label in cl for label in cpu_labels):
                        continue
                    for e in entries:
                        val = getattr(e, "current", None)
                        if val is not None and val > 0:
                            candidates.append(float(val))
                if candidates:
                    return max(candidates), ""
        except Exception:
            pass

        # 2. Windows: LibreHardwareMonitor / OpenHardwareMonitor WMI
        if os.name == "nt":
            try:
                import wmi  # type: ignore
                for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
                    for attempt in range(3):
                        try:
                            w = wmi.WMI(namespace=ns)
                            for sensor in w.Sensor():
                                name = getattr(sensor, "Name", "")
                                stype = getattr(sensor, "SensorType", "")
                                if "temperature" in str(stype).lower() and "cpu" in str(name).lower():
                                    val = getattr(sensor, "Value", None)
                                    if val is not None:
                                        return float(val), ""
                            break
                        except Exception:
                            if attempt < 2:
                                time.sleep(1.0)
                            continue
            except Exception:
                pass

            # 3. Windows ACPI thermal zones via WMI
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                for m in w.MSAcpi_ThermalZoneTemperature():
                    raw = float(m.CurrentTemperature)
                    c = (raw / 10.0) - 273.15
                    if 0 < c < 150:
                        return c, ""
            except Exception:
                pass

            # 4. PowerShell fallback: ACPI thermal zones
            try:
                ps_script = (
                    "Get-WmiObject -Namespace root/wmi -Class MSAcpi_ThermalZoneTemperature "
                    "| ForEach-Object { ($_.CurrentTemperature / 10) - 273.15 }"
                )
                exe = shutil.which("powershell") or shutil.which("pwsh")
                if exe:
                    raw = _run(
                        [exe, "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                        timeout=10.0,
                    )
                    if raw:
                        vals = [float(v) for v in raw.strip().splitlines() if v.strip()]
                        valid = [v for v in vals if 0 < v < 150]
                        if valid:
                            return max(valid), ""
            except Exception:
                pass

            return None, "No CPU temp sensor exposed by this system"

        return None, "Unsupported platform"

telemetry_engine = TelemetryEngine()
