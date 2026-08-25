# 🚀 DiskPulse NAS

> Full-stack, self-hosted HDD/SSD storage monitor, remote file manager, download manager, multi-device uploader, web media player, and embedded Linux terminal shell. Built for home servers, NAS units (Synology, TrueNAS, Ubuntu Server, Debian, Windows), and desktop monitoring.

---

## 🌟 Key Features

- 📊 **Real-Time System & Drive Telemetry**: Live storage consumption, read/write IOPS, MB/s bandwidth, per-core CPU load, RAM allocation, CPU temperature, and S.M.A.R.T. temperature health watchdog over WebSockets. Drives that can't report a temperature say [why](#-why-a-drive-says-no-smart-instead-of-a-temperature) — spun down, no S.M.A.R.T. at all, or a controller refusing pass-through — instead of a blank reading. CPU temperature is read from OS sensors where available; on Windows it may show **N/A** unless a hardware monitor like [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) is installed.
- 🗂️ **Interactive Web File Manager**: Full-featured file browser with breadcrumb navigation, dual view (Grid & List), file creation, search, rename, move, copy, deletion, and batch ZIP archive downloads.
- 💻 **Embedded Web Terminal Shell Widget**: Execute safe Linux/Unix file management commands (`ls`, `ll`, `cd`, `mkdir`, `mv`, `cp`, `rm`, `cat`, `echo`, `touch`, `du`, `stat`, `df`, `top`, `free`, `diskpulse`) directly from your browser with ANSI color output.
- ⚡ **High-Speed Multi-Engine Downloader**: Download HTTP/HTTPS URLs, video & media links from **1,800+ sites** — YouTube, Instagram, X/Twitter, Facebook, Vimeo, Dailymotion, TikTok, Crunchyroll & more (via `yt-dlp`), and Magnet/Torrent links (natively powered by `libtorrent` on Windows & Linux or optional Aria2) with live speed monitoring, pause/resume, and automatic **type-folder organization** into a destination you choose (a **Browse** picker or the one-tap **Backup** shortcut). Pick exact **video quality** (up to 4K) or extract **audio** (MP3/M4A/Opus/FLAC/WAV) with a "Fetch formats" preview, resilient anti-bot handling (player-client rotation + browser-cookie auth), and a one-click in-app **yt-dlp updater**. Sites that require a login (Instagram, X, Facebook) reuse your signed-in browser's cookies; DRM-protected streams (e.g. Crunchyroll premium) can't be saved.
- 🚀 **NAS Network & Internet Speed Test**: Real-time throughput benchmark for Download Mbps, Upload Mbps, Ping latency, and ISP / datacenter detection — one-click, powered by Cloudflare's global speed edge (no external CLI required). Every run also [charts](#-what-the-speed-test-charts-show) the live transfer curve, per-probe latency against its median with jitter and packet loss, and the last 30 runs so you can see a link degrade over time.
- 📤 **Drag-and-Drop Multi-Device Uploader**: Upload individual files **or entire folders** (drag a folder in, or use **Add folder**) with real-time queue tracking and instant Mobile QR Pairing for phone-to-NAS uploading. Choose any destination with a **Browse** folder-picker or the one-tap **Backup** shortcut. Loose files can auto-sort into type folders; whole folders always upload intact with their structure preserved.
- 🎬 **In-Browser Web Media Player**: High-fidelity audio player with animated canvas waveform visualizer, plus a streaming video player with **audio-track switching** (dual-audio MKV), **embedded & external subtitles** (SRT/ASS/VTT sidecars), **playback-speed** controls, and **YouTube-style quality switching** (Auto / 4K / 2K / 1080p / 720p / 480p / 360p). Powered by `ffmpeg`/`ffprobe` on the server (see [install notes](#web-media-player--dual-audio--subtitles-ffmpeg) below).
- 📄 **In-Browser PDF Preview**: Click any PDF to read it in place — page navigation, zoom, fit-to-width, and open-in-new-tab, rendered to a canvas by [PDF.js](https://mozilla.github.io/pdf.js/). The library is fetched lazily on first use and can be [vendored locally](#offline-pdf-previews) for offline installs.
- 📜 **Transfer History**: Every completed download and upload is recorded with timestamp, size, status, destination, and duration. Filter by type, set auto-removal retention (1 day to 1 year, or never), and clear history — all persisted in `.diskpulse/transfer_history.json`.
- 📱 **Mobile-Ready Download List**: Download cards adapt to phone screens — thumbnails go full-width, action buttons compact, long titles truncate cleanly, and progress bars hide on narrow viewports so the queue stays readable without horizontal scrolling.
- 🐍 **Python FastAPI Standalone Server**: Built-in 1-click NAS package generator for Docker Compose, TrueNAS SCALE, Synology DSM 7, and Systemd services.
- 🐧 **Linux Launch Script**: `run.sh` handles virtualenv creation, dependency installation, background service management (`start`/`stop`/`restart`/`status`/`logs`), systemd service generation, and cross-distro package manager detection (apt, dnf, yum, pacman, apk).

---

## 🗃️ Automatic Type-Folder Organization

Both the **uploader** and the **download manager** share one organizing scheme, so files land in the same place no matter how they arrive.

- **Pick a destination.** Use the **Browse** button to walk your storage tree and select (or create) any folder, or tap **Backup** for a one-click `Backup/` destination. Leave it empty to use the storage root (uploads) or the `Downloads/` bucket (downloads).
- **Sort into type folders** (on by default). Individual files are dropped into a subfolder by kind — **Images, Video, Audio, Documents, Archives, Disk Images, Programs**, and **Other** for anything unmatched — nested inside the destination you chose. Uncheck the toggle to drop them in as-is.
- **Whole folders are never split up.** Drag a folder onto the drop zone (or use **Add folder**) and it uploads intact — every subfolder and file keeps its original layout, with no type classification inside. Folders land in `Backup/` unless you pick another destination. A review dialog first shows the folder name, file count, total size and exact destination, with an expandable file list, so nothing is queued until you confirm.

Torrents (usually multi-file bundles) stay together in the destination rather than being split across type folders, and video/audio downloads bucket into **Video** / **Audio**.

> **Tip:** using **Add folder** makes the browser show its own *"Upload N files to this site?"* prompt — that one is drawn by Chrome/Edge and can't be styled or skipped by a web page. **Dragging** the folder onto the drop zone bypasses it entirely and goes straight to DiskPulse's own review dialog.

> **Note:** with sorting on by default, finished downloads now land in `Downloads/<Type>/` (e.g. `Downloads/Video/`) rather than the older `downloads/<category>/` layout.

---

## 📄 PDF Preview

Click a PDF in the file manager and it opens in an in-app viewer — page navigation, a page-number box, zoom in/out, fit-to-width, download, and a button that hands the file to the browser's own viewer in a new tab. Nothing is converted or rasterized on the server: the file is streamed from `/api/files/raw` (served inline as `application/pdf`) and rendered to a canvas in the browser by [PDF.js](https://mozilla.github.io/pdf.js/).

### Offline PDF previews

PDF.js is fetched lazily the first time you open a PDF — it's roughly 1 MB, so it costs nothing until you need it — from the first source that answers:

1. a local copy in `frontend/vendor/pdfjs/`, which needs no internet at all
2. jsDelivr
3. cdnjs

A source that 404s or stalls is abandoned and the next one is tried. If every source fails, the viewer falls back to a button that opens the PDF in the browser's built-in viewer, which needs no JavaScript at all — so previews degrade rather than break on an offline box.

For an air-gapped install, drop `pdf.min.js` and `pdf.worker.min.js` into `frontend/vendor/pdfjs/`, pinned to version **3.11.174** — the last PDF.js release that ships a classic `<script>` build (4.x is ESM-only and won't load here). Full instructions, including `npm pack` and `curl` recipes, are in [`frontend/vendor/pdfjs/README.md`](frontend/vendor/pdfjs/README.md). No code changes are needed; `[pdf] PDF.js loaded from vendored` in the browser console confirms the local copy won.

---

## 📏 How Sizes and Uptime Are Displayed

Every byte count in DiskPulse picks its own unit from its magnitude, so a 64 MB folder and an 8 TB pool each read naturally instead of one being padded out in the other's unit. Sizes are labelled **B / KB / MB / GB / TB** and computed in binary (1024 per step), which is what Windows Explorer, most NAS appliances and `ls -lh` all report — a 1 TB drive therefore shows as roughly `932 GB`, matching what your operating system tells you. A decimal place is kept only below 10, so `4.0 KB` stays precise while `932 GB` stays short.

Host uptime reads as hours and minutes — `3h 42m` — and folds in days once it passes 24 hours, as in `2d 4h 09m`. The sidebar shows used and total pool space beneath the usage bar; hover **Used** for the exact byte count, or **Total** for remaining free space and the storage root.

The embedded terminal is the one deliberate exception: `ls -lh`, `du -h`, `df -h` and `free -h` print single-letter suffixes there (`1.4G`, `932G`, `4.0K`) because that is what the real coreutils do, and an emulated shell that disagreed with the real one would look broken.

> **For contributors:** the formatting lives in exactly two places that mirror each other — `format_bytes` / `format_bytes_short` / `format_uptime` in [`backend/config.py`](backend/config.py), and `formatSize` / `formatUptime` in [`frontend/js/api.js`](frontend/js/api.js). Some numbers are rendered server-side (the `*_human` fields in the telemetry payload) and some client-side (live upload progress, chart tooltips), so if the two implementations drifted the same drive could read differently in two places on one screen. Change one, change the other, and route new size strings through them rather than writing a fresh unit loop.

---

## 🌡️ Why a Drive Says "No S.M.A.R.T." Instead of a Temperature

Not every disk will tell you how hot it is, and the reasons are unrelated to each other. A drive that has parked its heads to save power, a USB enclosure whose bridge chip won't forward ATA commands, a plain flash stick that has no S.M.A.R.T. data to forward in the first place, a SATA controller left in RAID mode, and a machine with no `smartmontools` installed all used to collapse into the same unhelpful `Temp N/A`. That single label sent people hunting for a hardware fault when nothing was wrong — and, worse, suggested running as Administrator to somebody who already was.

Each drive card now says which of those it is, so you can tell at a glance whether there is anything to fix:

| Badge | What it means | Anything to do? |
| --- | --- | --- |
| `Asleep` | The disk is spun down and DiskPulse chose not to wake it | No — this is working as intended |
| `No S.M.A.R.T.` | The device genuinely exposes no health data (most USB sticks and card readers), or its enclosure bridge won't pass the commands through | No, unless you expected pass-through from that enclosure |
| `Needs admin` | The controller refused the pass-through. When DiskPulse is *not* elevated, restart it elevated; when it already is, this is a RAID/RST-mode controller or a bridge that won't forward ATA — switching the SATA controller to AHCI usually fixes it | Yes, fixable |
| `No response` | The drive accepted the command but never answered before the timeout | Sometimes — a failing or badly bridged disk |
| `Needs smartmontools` | The `smartctl` binary isn't installed, so capacity and model come from the OS but health cannot | Yes — install `smartmontools` |

Sleeping disks are left asleep by default. Reading S.M.A.R.T. wakes a parked drive, and the dashboard refreshes every 30 seconds, so polling temperatures would stop an idle archive disk ever sleeping and add start/stop cycles it doesn't need. Set the environment variable `DISKPULSE_WAKE_DRIVES=1` before launching if you would rather have the temperature than the spin-down; those drives then read normally instead of `Asleep`.

Temperature itself comes from whichever source the drive actually populates: `smartctl`'s decoded temperature block when present, otherwise S.M.A.R.T. attribute 194 (`Temperature_Celsius`) or 190 (`Airflow_Temperature_Cel`), which is where several Seagate and WD firmwares keep it. Warning thresholds follow the media type, because the two genuinely differ — spinning disks are flagged from 50 °C and solid-state from 65 °C.

## 🌡️ CPU Temperature Notes

CPU temperature is exposed very differently across operating systems:

- **Linux** — `psutil.sensors_temperatures()` reads `coretemp` / `k10temp` / `acpitz` directly from `/sys/class/hwmon`. Works out of the box on most desktops, laptops and servers.
- **Windows** — the OS does **not** expose CPU package temperature through standard WMI on most consumer hardware. DiskPulse bundles [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) inside the standalone EXE build, so CPU temperature works without any extra installation.

**If you are running from source** (`python run.py`) on Windows and see **N/A**:

1. Download the vendor binaries once:
   ```powershell
   python build_exe.py --download-vendors
   ```
   This fetches ffmpeg, smartmontools and LibreHardwareMonitor into `vendor/` without building the EXE.

2. Restart DiskPulse. On startup it launches `LibreHardwareMonitor.exe` hidden so its WMI sensors become available. The first temperature reading may take a few seconds while the WMI namespace registers.

> **For contributors:** every failure path in [`backend/drive_health.py`](backend/drive_health.py) sets a `smart_reason` (`ok / asleep / no_permission / unsupported / usb_bridge / timeout / unreadable / no_tool`) and `smartctl`'s own `exit_status` and `messages[]` are what classify it — not guesswork on stderr text. A device that is known to exist is never dropped from the card just because it couldn't be read; it is kept and explained, which is why Windows enumerates from `Get-PhysicalDisk` (`/dev/pdN`) rather than `smartctl --scan-open`, whose output only includes devices it managed to open. Run **`python diagnose_drives.py`** on the affected machine for the per-device evidence — exact command, exit status, `smartctl`'s messages and which fields came back — and `python test_drive_health_smart.py` for the 108-check replay of the reference six-drive setup.

---

## 📶 What the Speed Test Charts Show

A single "94 Mbps" tells you almost nothing about a link. The same average can come from a connection that ramps up cleanly and holds, or from one that spikes and stalls halfway through — and only the second one will ruin a video call. So alongside the download, upload, ping and ISP tiles, a run plots three things.

**Throughput over time** traces the transfer itself, download then upload on one timeline. Each point is the rate over a 100 ms window rather than the running average, which matters: a cumulative average flattens out within a second or two and hides both the TCP slow-start ramp at the beginning and any dip in the middle. Watching the line while a test runs is also the quickest way to spot a link that only *looks* fast for its first megabyte.

**Latency and jitter** draws every round-trip as a bar against a dashed median line. The gap between the two is jitter, calculated the standard way as the mean difference between consecutive round-trips in the order they arrived. A flat row of bars is a stable link; a ragged one is the connection that stutters on calls even though its average ping looks fine. Failed probes are counted rather than skipped, so packet loss appears in the summary instead of quietly improving the numbers.

**Recent runs** keeps the last 30 results on disk and charts the most recent 12, with throughput as bars and ping as a line on its own axis. The useful question over time is whether a drop in speed arrived together with a rise in latency — congestion — or without one, which points at the link's capacity instead. Because history is written to disk, it survives restarts and is often the fastest way to answer "was it always this slow?".

While a test runs, the badge and progress bar name the current stage — connecting, ping, download, upload — so a ~25 second run doesn't look like one unexplained wait. A run that can't reach the edge says so instead of reporting a number: the charts and tiles go blank and the badge reads `TEST ERROR`, rather than leaving the previous run's figures on screen where they'd read as a fresh measurement.

> **For contributors:** the measurement lives in [`backend/speedtest_service.py`](backend/speedtest_service.py). `ThroughputRecorder` emits per-window samples during both transfers (upload is sent as a chunked body generator so it can be sampled mid-flight), `_measure_latency` returns every round-trip rather than just the median, and `get_status()` exposes `live` (phase, progress, in-flight samples) next to `latest` and `history` — all three as copies, since the worker thread is still writing to them. History is persisted to `speedtest_history.json` beside the config file, capped at 30 runs, and per-sample arrays are deliberately *not* stored there. Two rules are easy to break by accident: the 50/25 Mbps figures in the sizing code are only used to pick a payload size and must never be reported as a measurement, and a probe on a connection that hasn't yet paid for its TCP+TLS handshake is discarded — including the probe right after a reconnect, or one lost packet inflates jitter by an order of magnitude. The dashboard re-reads a running test every 500 ms as a self-rescheduling chain (not `setInterval`, which lets slow responses land out of order and rewind the chart); charts are updated in place with `update('none')`, never rebuilt. Verify with `python test_speedtest_charts.py` (151 checks, fakes the network seam) and `node test_speedtest_ui.js` (80 checks, runs the real renderers against idle / mid-run / completed / failed / hostile payloads).

---

## 📸 Screenshots

A quick tour of DiskPulse in action — running against **real hardware** on both Linux and Windows.

### 🧭 First-Run Setup Wizard

A guided, four-step wizard detects your real drives and partitions, confirms the storage path, sets your data preferences, and provisions everything on launch.

![Setup wizard — drive selection](Screenshots/setup-1-select-drive.png)

*Step 1 — DiskPulse auto-detects every drive and partition on the host; pick one to back your storage pool.*

![Setup wizard — drive selected](Screenshots/setup-2-drive-selected.png)

*Step 1 — a selected drive shows used / free / total capacity before you continue.*

![Setup wizard — confirm storage path](Screenshots/setup-3-confirm-storage-path.png)

*Step 2 — confirm (or rename) the storage sub-folder; a write-access test runs before proceeding.*

![Setup wizard — data preferences](Screenshots/setup-4-data-preferences.png)

*Step 3 — start with an empty pool, or pre-populate with demo media, documents and ISOs.*

![Setup wizard — review and launch](Screenshots/setup-5-review-launch.png)

*Step 4 — review the full configuration, then launch the server.*

![Setup wizard — launching](Screenshots/setup-6-launching.png)

*Step 4 — live provisioning: writing config, creating storage directories, and seeding data.*

![Setup wizard on Windows](Screenshots/setup-select-drive-windows.png)

*The same wizard on Windows — real NTFS/FAT32 volumes detected, complete with a "drive almost full" warning.*

### 📊 Real-Time System & Drive Telemetry

Live storage, I/O, CPU, RAM and S.M.A.R.T. health streamed over WebSockets, plus a one-click network speed test.

![Dashboard telemetry overview](Screenshots/dashboard-telemetry-overview.png)

*Storage pool, disk throughput, per-core CPU, RAM, a rolling disk-I/O chart, and the storage-category donut — all updating live.*

![S.M.A.R.T. health on Linux](Screenshots/dashboard-smart-health-linux.png)

*S.M.A.R.T. drive-health cards and the active mount-points table (Linux; virtual disks report temperature as N/A).*

![S.M.A.R.T. health on Windows hardware](Screenshots/dashboard-smart-health-windows.png)

*Real S.M.A.R.T. data on Windows — per-drive health, temperature and power-on hours for SATA & USB disks (via smartmontools).*

![NAS speed test on Windows](Screenshots/dashboard-speed-test-windows.png)

*One-click NAS network & internet speed test — download / upload / latency / ISP — above the drive-health cards.*

![Mount points and partitions on Windows](Screenshots/dashboard-mount-points-windows.png)

*Active mount points & partitions with per-volume usage bars and free space.*

### 🗂️ Interactive Web File Manager

Browse, search, and manage files with breadcrumb navigation and both grid and list views.

![File manager — grid view](Screenshots/file-manager-grid-view.png)

*Interactive Storage Explorer — grid view with breadcrumb navigation.*

![File manager — media folder](Screenshots/file-manager-media-folder.png)

*List view inside a media folder, showing size, type, modified time and permissions.*

![File manager — list view](Screenshots/file-manager-list-view.png)

*List view with per-file actions: preview, download, rename, move and delete.*

![File manager — move item](Screenshots/file-manager-move-item.png)

*Move or copy items with a destination folder-picker.*

### ⚡ High-Speed Download Manager

Fetch HTTP links, YouTube/video URLs and magnet/torrent links with live speed monitoring.

![Download manager — add download](Screenshots/download-manager-add-download.png)

*Add a download from a URL or magnet link — with a "Fetch formats" quality picker and one-click yt-dlp updater.*

![Download manager — active transfer](Screenshots/download-manager-active.png)

*Live aggregate speed, per-item progress, pause/resume and automatic category tagging.*

### 💻 Embedded Web Terminal Shell

![Embedded NAS terminal shell](Screenshots/terminal-shell.png)

*A sandboxed NAS terminal with ANSI-colored output for safe file-management commands.*

### 📤 Multi-Device Uploader

![Uploader — drag and drop](Screenshots/uploader-drag-and-drop.png)

*Drag & drop from your desktop, or scan the QR code to upload straight from your phone.*

![Uploader — active upload](Screenshots/uploader-active-upload.png)

*Live upload queue with per-file progress and speed.*

### 🎬 In-Browser Web Media Player

Powered by `ffmpeg`/`ffprobe` for audio-track switching, subtitles and on-the-fly transcoding.

![Media player — audio & subtitle controls](Screenshots/media-player-audio-subtitle-controls.png)

*Audio-track and subtitle selectors plus playback-speed control, alongside the media library.*

![Media player — video playback](Screenshots/media-player-video-playback.png)

*Streaming video playback with a frame-accurate scrub bar for large MKV movies.*

---

## ⚡ Quick Start

### 1. Requirements
- **Python 3.10+** (Python 3.11 / 3.12 / 3.13 supported on Windows & Linux)
- **No Node.js / npm required!**
- **Internet access** for the network speed test — it uses Cloudflare's speed edge via the Python standard library, so no `speedtest-cli` (or any other package) is needed.

#### Real drive health (S.M.A.R.T.) requirements

DiskPulse reports your machine's **actual** drives. Model, capacity, media type and overall health status work out of the box. Temperature, power-on hours and wear-based health require **elevated access**:

**Linux** — install `smartmontools`, then run with `sudo`:

```bash
sudo apt install smartmontools      # Debian / Ubuntu
# sudo dnf install smartmontools    # Fedora / RHEL / CentOS
# sudo pacman -S smartmontools      # Arch

sudo python run.py
```

Or use the bundled launcher (recommended):

```bash
chmod +x run.sh
./run.sh install    # installs ffmpeg, smartmontools, build tools
./run.sh start      # starts the server
```

**Windows** — install `smartmontools`, then run DiskPulse **as Administrator**:

```powershell
winget install smartmontools     # or:  choco install smartmontools

# then launch from an *elevated* PowerShell / Windows Terminal
python run.py
```

> Windows' built-in storage cmdlets only report temperature & power-on hours for **NVMe** drives, so `smartmontools` is what unlocks those metrics on **SATA and USB** disks. Without it (or without Administrator), the cards still show the real model, capacity and media type — temperature and power-on hours simply display `N/A`.

> ✅ **Confirmed on Windows:** after running `winget install smartmontools` and launching DiskPulse from an **elevated** PowerShell, the drive cards populate **temperature, power-on hours and wear** for SATA and USB disks — not just NVMe. If a drive still shows no temperature after that, the card now names the reason on the drive itself — spun down, no S.M.A.R.T. to read, or a controller refusing pass-through — rather than leaving you to guess; see [Why a drive says "No S.M.A.R.T." instead of a temperature](#-why-a-drive-says-no-smart-instead-of-a-temperature).

#### Video / media downloads — YouTube, Dailymotion & 1,800+ sites (optional)

DiskPulse hands video/media links to `yt-dlp`, which supports **~1,800 sites** (YouTube, Instagram, X/Twitter, Facebook, Vimeo, Dailymotion, TikTok, Crunchyroll, …). A few notes:

- **ffmpeg** is needed to merge 1080p+ video and to convert audio to MP3/FLAC/WAV. Without it, video tops out at 720p (pre-muxed) and audio can only be saved as the original M4A/Opus stream. See [Web media player — dual audio & subtitles (ffmpeg)](#web-media-player--dual-audio--subtitles-ffmpeg) below for install commands — the same `ffmpeg` install covers both features.
- **Browser impersonation** — some sites (e.g. **Dailymotion**) require yt-dlp to mimic a real browser's TLS fingerprint, which needs the optional [`curl_cffi`](https://github.com/yt-dlp/yt-dlp#impersonation) package. It ships with `yt-dlp[default]` (already pinned in `requirements.txt`). If you see *"attempting impersonation, but none of these impersonate targets are available"*, run `pip install -U "yt-dlp[default]"` — or just click **Update yt-dlp** in the app — then restart.
- **"Sign in to confirm you're not a bot"** from YouTube is almost always a stale `yt-dlp`. DiskPulse mitigates this automatically by rotating player clients and reusing a signed-in browser session's cookies, but the reliable cure is to keep `yt-dlp` current:
  - Click **Update yt-dlp** in the Add Download dialog, or run `pip install -U "yt-dlp[default]"`, then restart DiskPulse.
  - For stubborn videos (age-restricted / members-only) or login-only sites (Instagram, X, Facebook), stay signed into the site in Chrome, Edge or Firefox on the same machine — DiskPulse auto-detects and uses those cookies.

#### Web media player — dual audio & subtitles (ffmpeg)

The video player uses **`ffmpeg` and `ffprobe`** on the server to switch audio tracks (dual-audio MKV), extract embedded subtitles, load external `.srt`/`.ass`/`.vtt` sidecars, and remux/transcode non-browser-native formats (MKV, HEVC, AC3/DTS audio, etc.) on the fly.

Both tools ship together in the `ffmpeg` package and must be on the server's **PATH**. Without them, the player silently falls back to plain direct playback — the **Audio** and **Subtitles** dropdowns stay hidden and an in-app hint reads *"Install ffmpeg on the server to enable audio-track switching and subtitles."*

**Windows** — any one of:

```powershell
winget install ffmpeg           # Windows Package Manager
choco install ffmpeg            # Chocolatey
scoop install ffmpeg            # Scoop
```

Or install manually: download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases), unzip it, and add the extracted `bin` folder (the one containing `ffmpeg.exe` and `ffprobe.exe`) to your **PATH** (System Properties → Environment Variables), then open a new terminal.

**Linux** — use your distro's package manager:

```bash
sudo apt install ffmpeg         # Debian / Ubuntu / Raspberry Pi OS
sudo dnf install ffmpeg         # Fedora (RHEL/CentOS: enable RPM Fusion first)
sudo pacman -S ffmpeg           # Arch / Manjaro
sudo zypper install ffmpeg      # openSUSE
apk add ffmpeg                  # Alpine (also for slim Docker images)
```

**Verify** the server can see both binaries, then restart DiskPulse:

```bash
ffmpeg -version
ffprobe -version
```

> **Docker:** the `python:3.13-slim` image in the Compose example below does **not** include ffmpeg. Add it to the startup command — e.g. change the `command:` to `bash -c "apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt && python run.py"` — or bake `RUN apt-get update && apt-get install -y ffmpeg` into a custom image.

### 2. Install & Run

#### Linux — use the bundled launcher (recommended)

```bash
chmod +x run.sh
./run.sh install    # one-time: installs ffmpeg, smartmontools, build tools
./run.sh start      # starts the server on 0.0.0.0:8000
```

Or manually:

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python run.py
```

Open your browser at **[http://localhost:8000](http://localhost:8000)** (press `Ctrl+C` to terminate).

#### Windows

```powershell
# Install dependencies
pip install -r requirements.txt

# Run
python run.py
```

Open your browser at **[http://localhost:8000](http://localhost:8000)** (press `Ctrl+C` to terminate).

#### B. Desktop GUI & Standalone App (No Terminal Required)
For end-users who prefer a graphical interface with clean one-click exit (no `Ctrl+C` or command line needed):

**Windows Quick Start:** Double-click **`start_gui.bat`** (or run `python gui_launcher.py`).

| Mode | Command | Description |
| :--- | :--- | :--- |
| **1. Native Desktop Window** *(Recommended)* | `python gui_launcher.py --mode window` | Opens DiskPulse in a dedicated desktop application window (powered by Windows WebView2). Closing the window cleanly shuts down the server. |
| **2. System Tray App** | `python gui_launcher.py --mode tray` | Runs silently in the Windows taskbar tray (near the clock) and auto-opens your default browser. Right-click tray icon to open dashboard, storage folder, or exit. |
| **3. Hybrid Window + Tray** | `python gui_launcher.py --mode hybrid` | Native desktop window with minimize-to-tray background support. |
| **4. Desktop Control Panel** | `python gui_launcher.py --mode control-panel` | Sleek dark-themed desktop dashboard showing live server status, *Open Dashboard*, *Open Storage Folder*, *Diagnostics*, *Start/Stop*, and *Exit*. |

#### C. Standalone Portable Executable (`DiskPulse.exe`)

**Download from GitHub Releases (recommended):**

Pre-built Windows executables are attached to every GitHub release. No Python or build tools needed — just download, unzip, and run.

1. Open the [**DiskPulseNAS Releases**](https://github.com/jackhallloween21/DiskPulseNAS/releases) page.
2. Download the latest `DiskPulse-Windows-x64.zip` (or `DiskPulse.exe` directly).
3. Unzip anywhere and double-click `DiskPulse.exe`.
4. On first launch, Windows SmartScreen may warn — click **More info** → **Run anyway** (the app is self-signed; see build notes below).

**Local compilation:**

```powershell
python build_exe.py
```

The build script automatically downloads ffmpeg, smartmontools and LibreHardwareMonitor into `vendor/` before running PyInstaller. It retries transient download failures and falls back to alternate mirrors, so a brief 503 from one host won't abort the build. If a vendor binary can't be fetched, the build still completes and prints a warning — the missing tool simply won't be embedded in the EXE.

The executable is generated at **`dist/DiskPulse.exe`**.

**Automated GitHub Releases:**

A GitHub Actions workflow is included in [`.github/workflows/release.yml`](.github/workflows/release.yml). Whenever you create a new GitHub release or push a tag (e.g. `v1.0.0`), GitHub automatically builds `DiskPulse.exe` and `DiskPulse-Windows-x64.zip` and attaches them directly to the release assets. You can also trigger the build manually from the **Actions** tab on GitHub.

---

## 🐳 Docker Deployment

Run DiskPulse with Docker Compose:

```yaml
version: '3.8'

services:
  diskpulse-nas:
    image: python:3.13-slim
    container_name: diskpulse-nas
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - DISKPULSE_HOST=0.0.0.0
      - DISKPULSE_PORT=8000
      - DISKPULSE_STORAGE_ROOT=/storage
    volumes:
      - /mnt/storage:/storage
      - ./:/app
    working_dir: /app
    command: >
      bash -c "pip install -r requirements.txt && python run.py"
```

```bash
docker compose up -d
```

---

## 🧰 Tech Stack — Libraries, Tools & Frameworks

DiskPulse is a **Python + vanilla-JavaScript** project with **no frontend build step and no Node.js requirement**. Everything below is either pinned in [`requirements.txt`](requirements.txt), loaded from a CDN at runtime, or invoked as an external binary.

### Backend — Python 3.10+ (3.11 / 3.12 / 3.13 supported)

| Library | Used for |
| --- | --- |
| **FastAPI** | Async web framework — every REST endpoint and WebSocket route in [`backend/main.py`](backend/main.py) |
| **Uvicorn** | ASGI server that runs the app; [`run.py`](run.py) subclasses it to kill live ffmpeg streams on Ctrl+C before a graceful shutdown |
| **Pydantic** | Request/response model validation |
| **websockets** | WebSocket protocol for live telemetry and the interactive terminal |
| **aiohttp** | Async HTTP client — direct HTTP downloads and the Aria2 JSON-RPC client |
| **aiofiles** | Non-blocking file writes in the download engine |
| **psutil** | Cross-platform CPU, RAM, disk I/O and partition telemetry |
| **humanize** | Human-readable byte sizes |
| **python-multipart** | Multipart form parsing for file uploads |
| **yt-dlp[default]** | Video/media downloader for ~1,800 sites; the `[default]` extra bundles **curl_cffi** (browser TLS impersonation), **mutagen**, **pycryptodomex**, **brotli** and **websockets** |
| **libtorrent** | Native BitTorrent / magnet engine on Windows & Linux (the active torrent backend) |
| **torrentp** | Declared in `requirements.txt`; the native engine itself is `libtorrent` |

> The **speed test** deliberately uses **no third-party package** — it speaks to Cloudflare's speed edge over the Python standard library (`http.client`, `ssl`, `socket`, `threading`).

### External system tools (invoked as subprocesses)

| Tool | Feature it powers | Required? |
| --- | --- | --- |
| **ffmpeg / ffprobe** | Media player — audio-track switching, embedded/external subtitles, thumbnails, on-the-fly remux/transcode | Optional — falls back to direct playback |
| **smartmontools (`smartctl`)** | S.M.A.R.T. temperature, power-on hours and wear health | Optional — cards degrade to model/capacity only |
| **Aria2** | Alternative torrent backend over JSON-RPC ([`backend/aria2_client.py`](backend/aria2_client.py)) | Optional — libtorrent is the default |
| **Cloudflare speed edge** | Speed-test target (no `speedtest-cli` needed) | Internet access required |

### Frontend — vanilla web platform (no framework, no bundler)

| Library / asset | Source | Used for |
| --- | --- | --- |
| **Vanilla HTML5 / CSS3 / ES6+** | Hand-written SPA | The entire UI — no React/Vue/Angular, no build step |
| **Chart.js v4** | jsDelivr CDN (pinned to major v4) | All telemetry, speed-test and history charts |
| **Lucide Icons** | unpkg CDN | Icon set |
| **QRCode.js 1.0.0** | cdnjs CDN | Mobile QR pairing for the uploader |
| **PDF.js 3.11.174** | Lazy-loaded from jsDelivr/cdnjs, or vendored in [`frontend/vendor/pdfjs/`](frontend/vendor/pdfjs/) | In-browser PDF preview |
| **Google Fonts** | fonts.googleapis.com | Inter + JetBrains Mono typefaces |
| **Native browser APIs** | — | WebSocket, Canvas (audio waveform visualizer), Fetch, Drag & Drop, File API |

### Deployment & packaging

- **Docker** — `python:3.13-slim` base image ([`Dockerfile`](Dockerfile)) with a `curl` healthcheck; a **Docker Compose** example is included above.
- **Built-in NAS Deployer** — generates ready-to-run packages for **Docker Compose, TrueNAS SCALE, Synology DSM 7** and **systemd** ([`backend/nas_generator.py`](backend/nas_generator.py)).

### Testing

- **Python `unittest`** (standard library) — `test_download_types.py`, `test_drive_health_smart.py`, `test_media_stream_cleanup.py`, `test_speedtest_charts.py`, `test_transfer_progress.py`.
- **Plain Node.js script** — `test_speedtest_ui.js` runs the real frontend chart renderers against idle / mid-run / completed / failed / hostile payloads.
- **`diagnose_drives.py`** — per-drive S.M.A.R.T. diagnostic CLI for troubleshooting health readings.

---

## 📐 Architecture

```
DiskPulseNAS/
├── backend/
│   ├── config.py              # Configuration, storage pool & byte formatting
│   ├── setup_manager.py       # First-run wizard state & drive detection
│   ├── telemetry.py           # Real-time hardware & system metrics (psutil)
│   ├── drive_health.py        # Cross-platform S.M.A.R.T. reader (smartctl)
│   ├── speedtest_service.py   # Cloudflare speed-test engine (stdlib only)
│   ├── file_manager.py        # Safe asynchronous filesystem operations
│   ├── download_engine.py     # Multi-engine download worker (HTTP/yt-dlp/torrent)
│   ├── ytdlp_service.py       # yt-dlp subprocess wrapper & format probing
│   ├── aria2_client.py        # Optional Aria2 JSON-RPC torrent client
│   ├── media_service.py       # ffmpeg/ffprobe streaming, tracks & subtitles
│   ├── history_service.py     # Transfer history persistence & TTL pruning
│   ├── terminal_emulator.py   # Sandboxed NAS terminal shell
│   ├── nas_generator.py       # Docker/TrueNAS/Synology/systemd packager
│   └── main.py                # FastAPI REST API & WebSocket endpoints
├── frontend/
│   ├── index.html             # Main single-page application shell
│   ├── setup.html             # First-run setup wizard
│   ├── css/
│   │   └── styles.css         # Glassmorphic dark design system
│   ├── vendor/
│   │   └── pdfjs/             # Optional local PDF.js copy (offline previews)
│   └── js/
│       ├── api.js             # REST client & WebSocket manager
│       ├── app.js             # Core application shell & navigation
│       ├── dashboard.js       # Chart.js telemetry charts & gauges
│       ├── file_manager.js    # Interactive file manager controller
│       ├── folder_picker.js   # Reusable storage folder-picker (uploads + downloads)
│       ├── pdf_viewer.js      # PDF.js canvas preview (lazy-loaded, offline-capable)
│       ├── download_manager.js# Download manager & speed rate visualizer
│       ├── terminal.js        # Terminal UI & ANSI renderer
│       ├── media_player.js    # Audio/Video player & visualizer
│       ├── uploader.js        # Drag & drop and mobile QR uploader
│       └── nas_generator.js   # 1-click NAS exporter UI
├── run.py                     # Primary launcher (Uvicorn + graceful shutdown)
├── run.sh                     # Linux launcher (venv, deps, systemd, start/stop/restart)
├── generate_demo_data.py      # Demo seed files generator
├── diagnose_drives.py         # Per-drive S.M.A.R.T. diagnostic CLI
├── test_*.py / test_*.js      # unittest + Node.js test suites
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container image build
└── storage_pool/              # Server storage directories & media
```

### Request & data flow

1. **Browser → FastAPI.** The SPA in `frontend/` talks to `backend/main.py` over plain **REST** (`/api/...`) for commands and file operations, and over two **WebSockets** — `/ws/telemetry` for the live metrics stream and `/ws/terminal` for the interactive shell.
2. **Telemetry path.** `telemetry.py` samples `psutil` and merges in `drive_health.py`'s `smartctl` results, then pushes the combined payload down the telemetry WebSocket on an interval.
3. **Download path.** `download_engine.py` routes each URL to one of three engines — `aiohttp`/`aiofiles` for direct HTTP, `ytdlp_service.py` (a `yt-dlp` subprocess) for media sites, and `libtorrent` (or the optional `aria2_client.py`) for magnets/torrents — and reports progress back over REST.
4. **Media path.** `media_service.py` shells out to `ffprobe` for stream inspection and `ffmpeg` for remux/transcode, piping the result back as an HTTP streaming response.
5. **External tools are isolated.** `ffmpeg`, `smartctl` and `yt-dlp` are all invoked as subprocesses, so each is optional — the feature it powers degrades gracefully when the binary is absent.

---

## 📜 License
MIT License. Built for home servers and NAS enthusiasts.
