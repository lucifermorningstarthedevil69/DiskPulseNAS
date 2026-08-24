/**
 * Multi-Device Drag & Drop Uploader & Mobile QR Pairing
 * Fixed: progress bar, speed, ETA, pause/cancel controls, button state
 */
class MultiDeviceUploader {
  constructor() {
    this.dropzone = document.getElementById('uploader-dropzone');
    this.fileInput = document.getElementById('uploader-file-input');
    this.folderInput = document.getElementById('uploader-folder-input');
    this.queueContainer = document.getElementById('upload-queue-list');
    this.startBtn = document.getElementById('btn-start-upload');

    this.filesQueue = [];
    this.isUploading = false;
    this.cancelledItems = new Set(); // indexes of cancelled uploads
    // Folder entries waiting on the in-app review modal (never auto-queued).
    this.pendingFolderBatch = null;

    this.bindEvents();
  }

  bindEvents() {
    if (!this.dropzone) return;

    // Dropzone body click → pick files. Ignore clicks that came from the
    // buttons inside it (they have their own handlers).
    this.dropzone.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      this.fileInput.click();
    });

    // File input changes
    this.fileInput.addEventListener('change', (e) => {
      this.addFilesToQueue(Array.from(e.target.files));
      this.fileInput.value = '';
    });

    // Folder input changes (webkitdirectory — carries webkitRelativePath).
    // Goes through the review modal rather than straight into the queue.
    this.folderInput?.addEventListener('change', (e) => {
      this.addFilesToQueue(Array.from(e.target.files));
      this.folderInput.value = '';
    });

    // Explicit "Add files" / "Add folder" buttons inside the dropzone
    document.getElementById('uploader-pick-files')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.fileInput.click();
    });
    document.getElementById('uploader-pick-folder')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!this.folderInput) {
        alert("This browser can't pick whole folders — drag the folder onto the drop zone instead.");
        return;
      }
      this.folderInput.click();
    });

    // Destination chooser: Browse (folder picker) + Backup shortcut
    document.getElementById('upload-browse-btn')?.addEventListener('click', () => {
      const input = document.getElementById('upload-target-path');
      folderPicker.open({
        title: 'Choose upload folder',
        startPath: (input?.value || '').trim(),
        confirmLabel: 'Upload here',
        onPick: (relPath) => { if (input) input.value = relPath; }
      });
    });
    document.getElementById('upload-backup-btn')?.addEventListener('click', () => {
      const input = document.getElementById('upload-target-path');
      if (input) input.value = 'Backup';
    });

    // Folder-upload review modal: confirm queues the staged folder, dismissing
    // throws it away. (Escape / backdrop clicks are handled globally by app.js;
    // the stale batch is harmless because it is only ever read on confirm.)
    document.getElementById('ufc-upload')?.addEventListener('click', () => {
      this._confirmFolderBatch();
    });
    document.querySelectorAll('.ufc-dismiss').forEach(btn => {
      btn.addEventListener('click', () => { this.pendingFolderBatch = null; });
    });

    // Drag and Drop events
    ['dragenter', 'dragover'].forEach(name => {
      this.dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        this.dropzone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(name => {
      this.dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        this.dropzone.classList.remove('dragover');
      });
    });

    this.dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      this.handleDrop(e.dataTransfer);
    });

    // Start / Stop upload button
    this.startBtn?.addEventListener('click', () => {
      if (this.isUploading) {
        this.stopAll();
      } else {
        this.startUpload();
      }
    });

    // Mobile QR Modal Trigger
    document.getElementById('btn-open-qr-modal')?.addEventListener('click', () => {
      this.generateMobileQR();
      app.openModal('modal-mobile-qr');
    });

    document.getElementById('quick-btn-upload')?.addEventListener('click', () => {
      app.switchView('uploader');
    });
  }

  // ─── Drag & drop (supports whole folders) ──────────────────────────────────

  /**
   * A dropped folder shows up in dataTransfer.files as the directory itself,
   * which can't be read or uploaded. The Filesystem API (webkitGetAsEntry) is
   * the only way to walk into it, so we prefer that and keep dataTransfer.files
   * purely as a fallback for browsers without entry support.
   */
  async handleDrop(dt) {
    if (!dt) return;

    // dataTransfer.items is neutered once this handler returns, so grab every
    // entry synchronously BEFORE the first await.
    let entries = [];
    if (dt.items && dt.items.length) {
      entries = Array.from(dt.items)
        .map(it => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null))
        .filter(Boolean);
    }

    if (!entries.length) {
      // No entry support — plain files only (a dropped folder can't work here).
      if (dt.files && dt.files.length) this.addFilesToQueue(Array.from(dt.files));
      return;
    }

    const collected = [];
    for (const entry of entries) {
      await this._walkEntry(entry, '', collected);
    }

    if (!collected.length) {
      alert('Nothing to upload — that folder appears to be empty.');
      return;
    }

    // Loose files dropped alongside a folder keep the old behaviour (straight
    // into the queue, type-sorting honoured); folders go through the review
    // modal so the destination and file count are confirmed first.
    const loose = collected.filter(e => !e.relPath);
    const folderEntries = collected.filter(e => e.relPath);

    if (loose.length) this._enqueue(loose);
    if (folderEntries.length) this._reviewFolderBatch(folderEntries);
  }

  /**
   * Recursively collect {file, relPath} pairs from a FileSystemEntry.
   * `prefix` is the folder path accumulated so far ('' at the top level, so
   * loose files stay unprefixed and keep their type-sorting behaviour).
   */
  _walkEntry(entry, prefix, out) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file(
          (file) => {
            out.push({ file, relPath: prefix ? `${prefix}/${entry.name}` : '' });
            resolve();
          },
          () => resolve()   // unreadable file — skip it rather than abort
        );
        return;
      }

      if (!entry.isDirectory) return resolve();

      const dirPath = prefix ? `${prefix}/${entry.name}` : entry.name;
      const reader = entry.createReader();
      const children = [];

      // readEntries() returns results in batches and signals completion with an
      // empty array — a single call would silently truncate large folders.
      const readBatch = () => {
        reader.readEntries(
          (batch) => {
            if (!batch.length) {
              (async () => {
                for (const child of children) {
                  await this._walkEntry(child, dirPath, out);
                }
                resolve();
              })();
              return;
            }
            children.push(...batch);
            readBatch();
          },
          () => resolve()
        );
      };
      readBatch();
    });
  }

  // ─── Queue ─────────────────────────────────────────────────────────────────

  /** Add plain File objects (file picker, or a webkitdirectory pick). */
  addFilesToQueue(files) {
    const entries = files.map(f => ({ file: f, relPath: f.webkitRelativePath || '' }));
    const loose = entries.filter(e => !e.relPath);
    const folderEntries = entries.filter(e => e.relPath);

    if (loose.length) this._enqueue(loose);
    if (folderEntries.length) this._reviewFolderBatch(folderEntries);
  }

  // ─── Folder review modal ───────────────────────────────────────────────────

  /**
   * Chrome's own "Upload N files to this site?" prompt can't be restyled or
   * suppressed by a page, so this is the in-app equivalent: it stages the
   * collected folder and shows exactly what will be sent and where, before a
   * single byte moves. Nothing is queued until Upload is clicked.
   */
  _reviewFolderBatch(entries) {
    if (!entries || !entries.length) return;

    const modal = document.getElementById('modal-folder-confirm');
    if (!modal) {                    // markup missing — don't strand the upload
      this._enqueue(entries);
      return;
    }

    this.pendingFolderBatch = entries;

    // Top-level folder name(s) — the first segment of each relative path.
    const topNames = [...new Set(entries.map(e => e.relPath.split('/')[0]).filter(Boolean))];
    const totalBytes = entries.reduce((sum, e) => sum + (e.file?.size || 0), 0);
    const count = entries.length;
    const dest = (document.getElementById('upload-target-path')?.value || '').trim() || 'Backup';
    const destDisplay = '/' + [dest, topNames.length === 1 ? topNames[0] : '']
      .filter(Boolean).join('/').replace(/\\/g, '/');

    const nameEl = document.getElementById('ufc-folder-name');
    if (nameEl) nameEl.textContent = topNames.length === 1
      ? topNames[0]
      : `${topNames.length} folders`;

    const statsEl = document.getElementById('ufc-stats');
    if (statsEl) statsEl.textContent =
      `${count} file${count === 1 ? '' : 's'} · ${this._formatSize(totalBytes)}`;

    const destEl = document.getElementById('ufc-dest');
    if (destEl) destEl.textContent = destDisplay;

    const toggle = document.getElementById('ufc-toggle');
    if (toggle) toggle.textContent = `Show files (${count})`;

    const label = document.getElementById('ufc-upload-label');
    if (label) label.textContent = `Upload ${count} file${count === 1 ? '' : 's'}`;

    const list = document.getElementById('ufc-file-list');
    if (list) {
      const CAP = 300;
      const rows = entries.slice(0, CAP).map(e => `
        <div style="display:flex;justify-content:space-between;gap:12px;padding:5px 9px;border-bottom:1px solid var(--border-glass);font-size:0.72rem;">
          <span style="font-family:var(--font-mono);color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${this._escHtml(e.relPath)}</span>
          <span style="color:var(--text-dim);flex-shrink:0;">${this._formatSize(e.file?.size || 0)}</span>
        </div>`).join('');
      const more = count > CAP
        ? `<div style="padding:6px 9px;font-size:0.72rem;color:var(--text-dim);">…and ${count - CAP} more file${count - CAP === 1 ? '' : 's'}</div>`
        : '';
      list.innerHTML = rows + more;
    }

    app.openModal('modal-folder-confirm');
  }

  /** Review modal confirmed — queue the staged folder and start uploading. */
  _confirmFolderBatch() {
    const batch = this.pendingFolderBatch;
    this.pendingFolderBatch = null;
    app.closeModal('modal-folder-confirm');
    if (!batch || !batch.length) return;

    this._enqueue(batch);
    if (!this.isUploading) this.startUpload();
  }

  /** Add pre-resolved {file, relPath} entries. */
  _enqueue(entries) {
    entries.forEach(({ file, relPath }) => {
      this.filesQueue.push({
        file,
        // Folder-relative path ('' for a loose file). Files pulled out of a
        // dropped folder have no webkitRelativePath, so this is the only
        // reliable record of their structure.
        relPath: relPath || '',
        fromFolder: !!relPath,
        status: 'pending',  // pending | uploading | done | error | cancelled
        progress: 0,
        speedBps: 0,
        etaSecs: 0,
        uploadedBytes: 0,
        xhr: null,
        startedAt: null,
      });
    });
    this.renderQueue();
    this.updateStartButton();
  }

  // ─── Render ────────────────────────────────────────────────────────────────

  renderQueue() {
    if (!this.queueContainer) return;

    if (!this.filesQueue.length) {
      this.queueContainer.innerHTML =
        '<p style="text-align: center; color: var(--text-dim); padding: 24px;">No files in upload queue.</p>';
      return;
    }

    this.queueContainer.innerHTML = this.filesQueue.map((item, idx) => {
      const f = item.file;
      // Adaptive units: a 3 GB file read "3072.00 MB" when this was hard-wired to MB.
      const sizeText = this._formatSize(f.size);
      const uploadedText = this._formatSize(item.uploadedBytes);
      // Folder items show their relative path ("Album/2024/pic.jpg") so the
      // structure is visible; loose files just show the name.
      const displayName = item.relPath || f.name;

      // Status badge
      let badge = '';
      if (item.status === 'pending') {
        badge = `<span class="nav-badge">Pending</span>`;
      } else if (item.status === 'uploading') {
        badge = `<span class="nav-badge" style="background:rgba(0,242,254,0.2);color:var(--accent-cyan);">Uploading</span>`;
      } else if (item.status === 'done') {
        badge = `<span class="nav-badge" style="background:rgba(16,185,129,0.2);color:var(--accent-emerald);">✓ Done</span>`;
      } else if (item.status === 'error') {
        badge = `<span class="nav-badge" style="background:rgba(244,63,94,0.2);color:var(--accent-rose);">✗ Error</span>`;
      } else if (item.status === 'cancelled') {
        badge = `<span class="nav-badge" style="background:rgba(245,158,11,0.2);color:var(--accent-amber);">Cancelled</span>`;
      }

      // Speed & ETA string (only while uploading)
      const speedStr  = this._formatSpeed(item.speedBps);
      const etaStr    = item.etaSecs > 0 ? this._formatETA(item.etaSecs) : '--';
      const pct       = item.progress;

      // Progress bar colour
      let barColor = 'var(--grad-primary)';
      if (item.status === 'done')      barColor = 'var(--grad-emerald)';
      if (item.status === 'error')     barColor = 'var(--grad-rose)';
      if (item.status === 'cancelled') barColor = 'var(--grad-amber)';

      // Action buttons
      let actionBtns = '';
      if (item.status === 'pending') {
        actionBtns = `
          <button class="btn btn-secondary btn-icon" style="width:28px;height:28px;"
            onclick="uploaderWidget.removeFromQueue(${idx})" title="Remove">
            <i data-lucide="x" style="width:14px;height:14px;"></i>
          </button>`;
      } else if (item.status === 'uploading') {
        actionBtns = `
          <button class="btn btn-danger btn-icon" style="width:28px;height:28px;"
            onclick="uploaderWidget.cancelItem(${idx})" title="Cancel upload">
            <i data-lucide="x-circle" style="width:14px;height:14px;"></i>
          </button>`;
      } else if (item.status === 'error' || item.status === 'cancelled') {
        actionBtns = `
          <button class="btn btn-secondary btn-icon" style="width:28px;height:28px;"
            onclick="uploaderWidget.retryItem(${idx})" title="Retry">
            <i data-lucide="rotate-cw" style="width:14px;height:14px;"></i>
          </button>
          <button class="btn btn-secondary btn-icon" style="width:28px;height:28px;"
            onclick="uploaderWidget.removeFromQueue(${idx})" title="Remove">
            <i data-lucide="trash-2" style="width:14px;height:14px;"></i>
          </button>`;
      } else if (item.status === 'done') {
        actionBtns = `
          <button class="btn btn-secondary btn-icon" style="width:28px;height:28px;"
            onclick="uploaderWidget.removeFromQueue(${idx})" title="Remove">
            <i data-lucide="trash-2" style="width:14px;height:14px;"></i>
          </button>`;
      }

      return `
        <div class="upload-item-card" style="flex-direction: column; align-items: stretch; gap: 10px;" id="upload-item-${idx}">
          <!-- Row 1: file info + badge + action -->
          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
            <div style="display:flex;align-items:center;gap:12px;overflow:hidden;flex:1;">
              <i data-lucide="file" style="color:var(--accent-cyan);flex-shrink:0;"></i>
              <div style="overflow:hidden;">
                <strong style="font-size:0.9rem;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;">${this._escHtml(displayName)}</strong>
                <div style="font-size:0.75rem;color:var(--text-dim);">${sizeText} · ${this._escHtml(f.type || 'Unknown type')}</div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
              ${badge}
              ${actionBtns}
            </div>
          </div>

          <!-- Row 2: progress bar (always visible once added) -->
          <div style="background:var(--bg-tertiary);border-radius:4px;height:8px;overflow:hidden;">
            <div style="height:100%;width:${pct}%;background:${barColor};border-radius:4px;transition:width 0.3s ease;"></div>
          </div>

          <!-- Row 3: stats row -->
          <div style="display:flex;justify-content:space-between;font-size:0.78rem;color:var(--text-muted);">
            <span>
              ${item.status === 'uploading' || item.status === 'done'
                ? `${uploadedText} / ${sizeText}`
                : `0 B / ${sizeText}`}
            </span>
            <span style="display:flex;gap:16px;">
              ${item.status === 'uploading' ? `
                <span style="color:var(--accent-cyan);font-weight:600;">${speedStr}</span>
                <span>ETA: <strong style="color:#fff;">${etaStr}</strong></span>
              ` : ''}
              <span style="font-weight:700;color:${item.status === 'done' ? 'var(--accent-emerald)' : '#fff'};">${pct}%</span>
            </span>
          </div>
        </div>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  /** Update only a single item's progress bars/stats without a full re-render */
  _updateItemProgress(idx) {
    const item = this.filesQueue[idx];
    if (!item) return;

    const card = document.getElementById(`upload-item-${idx}`);
    if (!card) {
      // Fallback — full re-render
      this.renderQueue();
      return;
    }

    const pct = item.progress;

    // Progress bar
    const bar = card.querySelector('[data-role="progress-bar"]') || card.querySelectorAll('div > div')[1]?.firstElementChild;
    if (bar) bar.style.width = `${pct}%`;

    // Stats row — just rebuild the entire card when uploading so the numbers are always fresh
    this.renderQueue();
  }

  updateStartButton() {
    if (!this.startBtn) return;
    const hasPending = this.filesQueue.some(i => i.status === 'pending');
    if (this.isUploading) {
      this.startBtn.innerHTML = '<i data-lucide="square"></i> Stop All Uploads';
      this.startBtn.style.display = 'inline-flex';
      this.startBtn.style.background = 'var(--grad-rose)';
    } else if (hasPending) {
      this.startBtn.innerHTML = '<i data-lucide="play"></i> Start Upload';
      this.startBtn.style.display = 'inline-flex';
      this.startBtn.style.background = '';
    } else {
      this.startBtn.style.display = hasPending || this.filesQueue.length > 0 ? 'inline-flex' : 'none';
      this.startBtn.innerHTML = '<i data-lucide="play"></i> Start Upload';
      this.startBtn.style.background = '';
    }
    if (window.lucide) lucide.createIcons();
  }

  // ─── Upload logic ───────────────────────────────────────────────────────────

  async startUpload() {
    if (this.isUploading) return;

    const targetFolder = document.getElementById('upload-target-path')?.value.trim() || '';
    const sortByType = document.getElementById('upload-sort-type')?.checked !== false;
    const pendingItems = this.filesQueue.filter(i => i.status === 'pending');
    if (!pendingItems.length) return;

    this.isUploading = true;
    this.updateStartButton();

    for (const item of pendingItems) {
      if (item.status === 'cancelled') continue;
      await this._uploadSingleItem(item, targetFolder, sortByType);
    }

    this.isUploading = false;
    this.updateStartButton();
    fileManager.refresh();
    this.renderQueue();
  }

  _uploadSingleItem(item, targetFolder, sortByType = true) {
    return new Promise((resolve) => {
      item.status = 'uploading';
      item.startedAt = Date.now();
      item.uploadedBytes = 0;
      item.speedBps = 0;
      item.etaSecs = 0;
      item.progress = 0;
      this.renderQueue();

      // A file that came from a folder is uploaded intact: its own structure is
      // preserved and it is never split into type folders. Those land in
      // "Backup" unless an explicit destination was chosen. Loose files keep
      // the type-sorting toggle. Each item is its own request, so these
      // decisions are made per file.
      const isFolderItem = !!item.fromFolder;
      const dest = isFolderItem ? (targetFolder || 'Backup') : targetFolder;
      const sort = isFolderItem ? false : sortByType;

      const formData = new FormData();
      formData.append('target_folder', dest);
      formData.append('sort_by_type', sort ? 'true' : 'false');
      // Send the folder-relative path as the multipart filename so the backend
      // can rebuild the structure; loose files just send their name.
      formData.append('files', item.file, item.relPath || item.file.name);

      const xhr = new XMLHttpRequest();
      item.xhr = xhr;
      xhr.open('POST', `${api.baseUrl}/api/upload`);

      let lastLoaded = 0;
      let lastTime = Date.now();

      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;

        const now = Date.now();
        const elapsed = (now - lastTime) / 1000;  // seconds since last update
        const deltaByes = e.loaded - lastLoaded;

        if (elapsed > 0.3) {  // update every 300 ms minimum
          item.speedBps     = deltaByes / elapsed;
          const remaining   = e.total - e.loaded;
          item.etaSecs      = item.speedBps > 0 ? remaining / item.speedBps : 0;
          lastLoaded = e.loaded;
          lastTime   = now;
        }

        item.uploadedBytes = e.loaded;
        item.progress      = Math.round((e.loaded / e.total) * 100);
        this.renderQueue();
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          item.status = 'done';
          item.progress = 100;
          item.speedBps = 0;
          item.etaSecs  = 0;
        } else {
          item.status = 'error';
        }
        item.xhr = null;
        this.renderQueue();
        resolve();
      };

      xhr.onerror = () => {
        item.status = 'error';
        item.xhr = null;
        this.renderQueue();
        resolve();
      };

      xhr.onabort = () => {
        item.status = 'cancelled';
        item.speedBps = 0;
        item.etaSecs  = 0;
        item.xhr = null;
        this.renderQueue();
        resolve();
      };

      xhr.send(formData);
    });
  }

  stopAll() {
    this.filesQueue.forEach(item => {
      if (item.status === 'uploading' && item.xhr) {
        item.xhr.abort();
      }
    });
    this.isUploading = false;
    this.updateStartButton();
  }

  cancelItem(idx) {
    const item = this.filesQueue[idx];
    if (!item) return;
    if (item.xhr) {
      item.xhr.abort();  // triggers xhr.onabort → sets status
    } else {
      item.status = 'cancelled';
      this.renderQueue();
    }
  }

  retryItem(idx) {
    const item = this.filesQueue[idx];
    if (!item) return;
    item.status = 'pending';
    item.progress = 0;
    item.uploadedBytes = 0;
    item.speedBps = 0;
    item.etaSecs = 0;
    this.renderQueue();
    this.updateStartButton();
  }

  removeFromQueue(idx) {
    const item = this.filesQueue[idx];
    if (item?.xhr) item.xhr.abort();
    this.filesQueue.splice(idx, 1);
    this.renderQueue();
    this.updateStartButton();
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  _formatSpeed(bps) {
    if (!bps || bps <= 0) return '0 B/s';
    if (bps >= 1024 * 1024 * 1024) return `${(bps / (1024 ** 3)).toFixed(2)} GB/s`;
    if (bps >= 1024 * 1024)        return `${(bps / (1024 ** 2)).toFixed(2)} MB/s`;
    if (bps >= 1024)               return `${(bps / 1024).toFixed(1)} KB/s`;
    return `${Math.round(bps)} B/s`;
  }

  _formatETA(secs) {
    if (!secs || secs <= 0 || !isFinite(secs)) return '--';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  _formatSize(bytes) {
    return formatSize(bytes);
  }

  /** File names come from the OS — escape before injecting into innerHTML. */
  _escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ─── Mobile QR Pairing ─────────────────────────────────────────────────────

  generateMobileQR() {
    const qrContainer = document.getElementById('qrcode-container');
    const urlText = document.getElementById('mobile-qr-url');
    if (!qrContainer) return;

    qrContainer.innerHTML = '';

    // Prefer the real LAN IP address so smartphones on local Wi-Fi can connect
    let url = window.DISKPULSE_LAN_URL ? `${window.DISKPULSE_LAN_URL}/#uploader` : window.location.href;
    const topbarIp = document.getElementById('topbar-lan-ip')?.textContent?.trim();
    if (topbarIp && topbarIp !== '--' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')) {
      url = `http://${topbarIp}/#uploader`;
    }

    if (urlText) urlText.textContent = url;

    if (window.QRCode) {
      new QRCode(qrContainer, {
        text: url,
        width: 170,
        height: 170,
        colorDark: '#0f172a',
        colorLight: '#ffffff',
        correctLevel: QRCode.CorrectLevel.H
      });
    }
  }
}

const uploaderWidget = new MultiDeviceUploader();
