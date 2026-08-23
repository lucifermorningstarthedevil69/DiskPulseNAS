/**
 * DiskPulse API & WebSocket Client Service
 */

// ── Canonical human formatting ────────────────────────────────────────────────
// These MIRROR backend/config.py (format_bytes / format_uptime) byte for byte.
// Some numbers are formatted server-side (*_human in the telemetry payload) and
// some client-side (live upload progress, chart tooltips); if the two drifted,
// the same drive would read differently in two places on one screen.
// Change one, change the other.
const DP_BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

/** 1503238553 -> "1.4 GB". 1024-based, labelled KB/MB/GB/TB like Windows. */
function formatSize(bytes) {
  let size = Number(bytes);
  if (!isFinite(size) || size <= 0) return '0 B';

  let i = 0;
  while (size >= 1024 && i < DP_BYTE_UNITS.length - 1) {
    size /= 1024;
    i++;
  }
  // A decimal only below 10, so "4.0 KB" stays precise and "932 GB" stays short.
  const text = (i === 0 || size >= 10) ? String(Math.round(size)) : size.toFixed(1);
  return `${text} ${DP_BYTE_UNITS[i]}`;
}

/** 15129 -> "4h 12m"; 188400 -> "2d 4h 20m". Zero-padded so the width is stable. */
function formatUptime(seconds) {
  let total = Math.floor(Number(seconds));
  if (!isFinite(total) || total < 0) total = 0;

  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const mm = String(mins).padStart(2, '0');

  return days ? `${days}d ${hours}h ${mm}m` : `${hours}h ${mm}m`;
}

class DiskPulseAPI {
  constructor() {
    this.baseUrl = window.location.origin;
    this.wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.wsHost = window.location.host;
    
    this.telemetryWs = null;
    this.terminalWs = null;
    this.listeners = {};
  }

  on(event, callback) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(callback);
  }

  emit(event, data) {
    if (this.listeners[event]) {
      this.listeners[event].forEach(cb => {
        try { cb(data); } catch(e) { console.error(e); }
      });
    }
  }

  // HTTP Helper
  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    try {
      const response = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers || {})
        },
        ...options
      });
      if (!response.ok) {
        let errMessage = `HTTP error ${response.status}`;
        try {
          const errData = await response.json();
          if (errData.detail) errMessage = errData.detail;
        } catch (_) {}
        throw new Error(errMessage);
      }
      return await response.json();
    } catch (err) {
      console.error(`API Error on ${endpoint}:`, err);
      throw err;
    }
  }

  // Telemetry WebSocket
  initTelemetryWebSocket() {
    const wsUrl = `${this.wsProtocol}//${this.wsHost}/ws/telemetry`;
    const connect = () => {
      this.telemetryWs = new WebSocket(wsUrl);
      this.telemetryWs.onopen = () => {
        this.emit('telemetry:status', { connected: true });
      };
      this.telemetryWs.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.emit('telemetry:data', data);
        } catch (e) {
          console.error("Telemetry JSON parse error:", e);
        }
      };
      this.telemetryWs.onclose = () => {
        this.emit('telemetry:status', { connected: false });
        setTimeout(connect, 3000); // Auto reconnect
      };
      this.telemetryWs.onerror = () => {
        this.telemetryWs.close();
      };
    };
    connect();
  }

  // Server Endpoints
  async getServerInfo() {
    return this.request('/api/server/info');
  }

  // File Manager Endpoints
  async listFiles(path = "") {
    return this.request(`/api/files/list?path=${encodeURIComponent(path)}`);
  }

  async searchFiles(query = "", path = "") {
    return this.request(`/api/files/search?query=${encodeURIComponent(query)}&path=${encodeURIComponent(path)}`);
  }

  async createDirectory(parentPath, folderName) {
    return this.request('/api/files/mkdir', {
      method: 'POST',
      body: JSON.stringify({ parent_path: parentPath, folder_name: folderName })
    });
  }

  async createFile(parentPath, fileName, content = "") {
    return this.request('/api/files/create', {
      method: 'POST',
      body: JSON.stringify({ parent_path: parentPath, file_name: fileName, content: content })
    });
  }

  async readFile(path) {
    return this.request(`/api/files/read?path=${encodeURIComponent(path)}`);
  }

  async writeFile(path, content) {
    return this.request('/api/files/write', {
      method: 'POST',
      body: JSON.stringify({ path: path, content: content })
    });
  }

  async renameFile(path, newName) {
    return this.request('/api/files/rename', {
      method: 'POST',
      body: JSON.stringify({ path: path, new_name: newName })
    });
  }

  async moveFiles(sourcePaths, targetFolder) {
    return this.request('/api/files/move', {
      method: 'POST',
      body: JSON.stringify({ source_paths: sourcePaths, target_folder: targetFolder })
    });
  }

  async copyFiles(sourcePaths, targetFolder) {
    return this.request('/api/files/copy', {
      method: 'POST',
      body: JSON.stringify({ source_paths: sourcePaths, target_folder: targetFolder })
    });
  }

  // Live progress for a background move/copy (returns {status, total_bytes,
  // transferred_bytes, ...}); poll it until status is no longer "running".
  async getFileOperation(opId) {
    return this.request(`/api/files/operation/${opId}`);
  }

  async deleteFiles(paths) {
    return this.request('/api/files/delete', {
      method: 'POST',
      body: JSON.stringify({ paths: paths })
    });
  }

  getDownloadZipUrl(paths) {
    return `${this.baseUrl}/api/files/zip`;
  }

  getRawFileUrl(path) {
    return `${this.baseUrl}/api/files/raw?path=${encodeURIComponent(path)}`;
  }

  // Web Media Player (ffprobe/ffmpeg-backed) Endpoints
  async getMediaInfo(path) {
    return this.request(`/api/media/info?path=${encodeURIComponent(path)}`);
  }

  getMediaStreamUrl(path, audioIdx = 0, t = 0, vcodec = "") {
    const params = new URLSearchParams({
      path, audio: String(audioIdx), t: String(t || 0)
    });
    if (vcodec) params.set('vcodec', vcodec);
    return `${this.baseUrl}/api/media/stream?${params.toString()}`;
  }

  getSubtitleUrl(path, { kind = 'embedded', track = 0, file = '', offset = 0 } = {}) {
    const params = new URLSearchParams({ path, kind });
    if (kind === 'external') params.set('file', file);
    else params.set('track', String(track));
    if (offset && offset > 0) params.set('offset', String(offset));
    return `${this.baseUrl}/api/media/subtitle?${params.toString()}`;
  }

  getMediaThumbUrl(path, t = 0, w = 200) {
    const params = new URLSearchParams({ path, t: String(t || 0), w: String(w || 200) });
    return `${this.baseUrl}/api/media/thumb?${params.toString()}`;
  }

  // Download Manager Endpoints
  async getDownloads() {
    return this.request('/api/downloads');
  }

  async addDownload(url, category = null, customFolder = "", customFilename = null, opts = {}) {
    return this.request('/api/downloads/add', {
      method: 'POST',
      body: JSON.stringify({
        url,
        category,
        custom_folder: customFolder,
        custom_filename: customFilename,
        backend: opts.backend || 'auto',
        mode: opts.mode || 'video',
        max_height: opts.maxHeight || 'best',
        audio_format: opts.audioFormat || 'mp3',
        audio_bitrate: opts.audioBitrate || '192',
        format_id: opts.formatId || '',
        progressive: opts.progressive || false,
        sort_by_type: opts.sortByType !== false,
        meta_title: opts.metaTitle || '',
        meta_thumbnail: opts.metaThumbnail || ''
      })
    });
  }

  async probeMedia(url) {
    return this.request('/api/downloads/probe', {
      method: 'POST',
      body: JSON.stringify({ url })
    });
  }

  async getYtdlpVersion() {
    return this.request('/api/downloads/ytdlp-version');
  }

  async updateYtdlp() {
    return this.request('/api/downloads/ytdlp-update', { method: 'POST' });
  }

  async pauseDownload(taskId) {
    return this.request(`/api/downloads/pause/${taskId}`, { method: 'POST' });
  }

  async resumeDownload(taskId) {
    return this.request(`/api/downloads/resume/${taskId}`, { method: 'POST' });
  }

  async cancelDownload(taskId) {
    return this.request(`/api/downloads/cancel/${taskId}`, { method: 'POST' });
  }

  async retryDownload(taskId) {
    return this.request(`/api/downloads/retry/${taskId}`, { method: 'POST' });
  }

  async deleteDownload(taskId, deleteFile = false) {
    return this.request(`/api/downloads/${taskId}?delete_file=${deleteFile}`, { method: 'DELETE' });
  }

  // Speed Test Endpoints
  async getSpeedTestLatest() {
    return this.request('/api/speedtest/latest');
  }

  async runSpeedTest() {
    return this.request('/api/speedtest/run', { method: 'POST' });
  }

  async getSpeedTestPing() {
    return this.request('/api/speedtest/ping');
  }

  // Terminal API
  async execTerminalCommand(command, sessionId = "default") {
    return this.request('/api/terminal/exec', {
      method: 'POST',
      body: JSON.stringify({ command, session_id: sessionId })
    });
  }
}

const api = new DiskPulseAPI();
