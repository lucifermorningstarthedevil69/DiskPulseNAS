/**
 * Interactive File Manager Module
 */
class FileManagerView {
  constructor() {
    this.currentPath = "";
    this.currentFiles = [];
    this.selectedPaths = new Set();
    this.viewMode = 'grid'; // 'grid' | 'list'
    this.editingFilePath = null;
    this._justDragged = false;
    this._dragState = { active: false, potential: false, startX: 0, startY: 0, box: null };

    // Rename + move/copy destination-picker state
    this.renameTargetPath = null;
    this.renameOldName = null;
    this.destMode = null;      // 'move' | 'copy'
    this.destSources = [];     // relative paths being moved/copied
    this.destPath = "";        // folder currently browsed in the picker
    this.activeTransfer = null; // op_id of the transfer currently being tracked

    this.bindEvents();
  }

  bindEvents() {
    // View Switchers
    document.getElementById('fm-toggle-grid')?.addEventListener('click', () => this.setViewMode('grid'));
    document.getElementById('fm-toggle-list')?.addEventListener('click', () => this.setViewMode('list'));

    // Search filter
    document.getElementById('fm-search-input')?.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      if (!q) {
        this.renderFiles(this.currentFiles);
      } else {
        const filtered = this.currentFiles.filter(f => f.name.toLowerCase().includes(q));
        this.renderFiles(filtered);
      }
    });

    // New Folder Dialog
    document.getElementById('fm-btn-new-folder')?.addEventListener('click', () => {
      document.getElementById('modal-folder-name').value = '';
      app.openModal('modal-new-folder');
    });

    document.getElementById('modal-folder-submit')?.addEventListener('click', async () => {
      const name = document.getElementById('modal-folder-name').value.trim();
      if (!name) return;
      try {
        await api.createDirectory(this.currentPath, name);
        app.closeModal('modal-new-folder');
        this.refresh();
      } catch (err) {
        this.showToast(err.message);
      }
    });

    // New File Dialog
    document.getElementById('fm-btn-new-file')?.addEventListener('click', () => {
      this.editingFilePath = null;
      document.getElementById('editor-title').innerHTML = '<i data-lucide="file-plus"></i> Create New File';
      document.getElementById('editor-filename-group').style.display = 'block';
      document.getElementById('editor-filename').value = '';
      document.getElementById('editor-content').value = '';
      app.openModal('modal-file-editor');
      if (window.lucide) lucide.createIcons();
    });

    // File Editor Save
    document.getElementById('editor-save-btn')?.addEventListener('click', async () => {
      const content = document.getElementById('editor-content').value;
      try {
        if (this.editingFilePath) {
          await api.writeFile(this.editingFilePath, content);
        } else {
          const fname = document.getElementById('editor-filename').value.trim();
          if (!fname) {
            this.showToast('File name cannot be empty');
            return;
          }
          await api.createFile(this.currentPath, fname, content);
        }
        app.closeModal('modal-file-editor');
        this.refresh();
      } catch (err) {
        this.showToast(err.message);
      }
    });

    // Upload Trigger
    document.getElementById('fm-btn-upload')?.addEventListener('click', () => {
      document.getElementById('upload-target-path').value = this.currentPath;
      app.switchView('uploader');
    });

    // Bulk Delete
    document.getElementById('fm-bulk-delete')?.addEventListener('click', async () => {
      const count = this.selectedPaths.size;
      if (!count) return;
      const ok = await this.confirmModal({
        title: 'Delete items',
        message: `Permanently delete ${count} selected item${count > 1 ? 's' : ''}? This cannot be undone.`,
        confirmLabel: 'Delete',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.deleteFiles(Array.from(this.selectedPaths));
        this.selectedPaths.clear();
        this.updateBulkBar();
        this.refresh();
      } catch (err) {
        this.showToast(err.message);
      }
    });

    // Bulk Move / Copy → open destination picker
    document.getElementById('fm-bulk-move')?.addEventListener('click', () => this.openDestPicker('move'));
    document.getElementById('fm-bulk-copy')?.addEventListener('click', () => this.openDestPicker('copy'));

    // Rename modal submit (button + Enter key)
    document.getElementById('modal-rename-submit')?.addEventListener('click', () => this.submitRename());
    document.getElementById('modal-rename-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); this.submitRename(); }
    });

    // Destination picker confirm ("Move here" / "Copy here")
    document.getElementById('dest-picker-confirm')?.addEventListener('click', () => this.confirmDest());

    // Bulk Zip Download
    document.getElementById('fm-bulk-zip')?.addEventListener('click', () => {
      const paths = Array.from(this.selectedPaths);
      if (!paths.length) return;

      // Exactly one file selected → skip zipping entirely and stream the file
      // itself. This is the common "download one movie" case and avoids the
      // read-2GB-then-compress-then-buffer path that used to hang.
      if (paths.length === 1) {
        const only = this.currentFiles.find(f => f.path === paths[0]);
        if (only && !only.is_dir) {
          this.downloadSingleFile(paths[0]);
          return;
        }
      }
      this.downloadZip(paths);
    });

    // Select all checkbox
    document.getElementById('fm-select-all-checkbox')?.addEventListener('change', (e) => {
      if (e.target.checked) {
        this.currentFiles.forEach(f => this.selectedPaths.add(f.path));
      } else {
        this.selectedPaths.clear();
      }
      this.updateBulkBar();
      this.renderFiles(this.currentFiles);
    });

    // Select All / Select None buttons
    document.getElementById('fm-select-all-btn')?.addEventListener('click', () => this.selectAll());
    document.getElementById('fm-select-none-btn')?.addEventListener('click', () => this.selectNone());

    // Keyboard shortcut: Ctrl/Cmd+A to select all
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        const active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
        e.preventDefault();
        this.selectAll();
      }
    });

    // Grid drag selection
    const gridEl = document.getElementById('fm-files-grid');
    if (gridEl) {
      gridEl.addEventListener('mousedown', (e) => this._onGridMouseDown(e));
    }
    document.addEventListener('mousemove', (e) => this._onGridMouseMove(e));
    document.addEventListener('mouseup', (e) => this._onGridMouseUp(e));
  }

  setViewMode(mode) {
    this.viewMode = mode;
    const gridEl = document.getElementById('fm-files-grid');
    const listEl = document.getElementById('fm-files-list-wrapper');
    if (mode === 'grid') {
      gridEl.style.display = 'grid';
      listEl.style.display = 'none';
    } else {
      gridEl.style.display = 'none';
      listEl.style.display = 'block';
    }
    this.renderFiles(this.currentFiles);
  }

  async navigateTo(path) {
    this.currentPath = path;
    this.selectedPaths.clear();
    this.updateBulkBar();
    await this.refresh();
  }

  async refresh() {
    try {
      const res = await api.listFiles(this.currentPath);
      this.currentFiles = res.files || [];
      this.renderBreadcrumbs(res.breadcrumbs || []);
      this.renderFiles(this.currentFiles);
    } catch (err) {
      console.error(err);
    }
  }

  renderBreadcrumbs(breadcrumbs) {
    const container = document.getElementById('fm-breadcrumbs');
    if (!container) return;

    container.innerHTML = breadcrumbs.map((crumb, idx) => {
      const isLast = idx === breadcrumbs.length - 1;
      if (isLast) {
        return `<span class="crumb-item current">${crumb.name === 'root' ? '<i data-lucide="hard-drive" style="width:14px; display:inline;"></i> root' : crumb.name}</span>`;
      }
      return `
        <span class="crumb-item" onclick="fileManager.navigateTo('${crumb.path}')">
          ${crumb.name === 'root' ? '<i data-lucide="hard-drive" style="width:14px; display:inline;"></i> root' : crumb.name}
        </span>
        <span class="crumb-separator">/</span>
      `;
    }).join('');
    if (window.lucide) lucide.createIcons();
  }

  renderFiles(files) {
    const gridContainer = document.getElementById('fm-files-grid');
    const listBody = document.getElementById('fm-files-list-tbody');

    if (!files.length) {
      gridContainer.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-dim); padding: 48px;"><i data-lucide="folder-open" style="width:48px;height:48px;margin-bottom:12px;opacity:0.4;"></i><p>This directory is empty</p></div>';
      listBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 32px;">This directory is empty</td></tr>';
      if (window.lucide) lucide.createIcons();
      return;
    }

    // Render Grid View
    gridContainer.innerHTML = files.map(f => {
      const isSelected = this.selectedPaths.has(f.path);
      const icon = this.getCategoryIcon(f.category);
      return `
        <div class="file-item-grid ${isSelected ? 'selected' : ''}" data-path="${f.path}"
          onclick="fileManager.onItemClick(event, '${f.path}', ${f.is_dir}, '${f.category}')"
          oncontextmenu="return fileManager.openItemMenu(event, '${f.path}', ${f.is_dir}, '${f.category}')">
          <label class="file-item-checkbox-wrap" title="Select" onclick="event.stopPropagation();">
            <input type="checkbox" class="file-item-checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); fileManager.toggleSelect('${f.path}');">
          </label>
          <button class="fm-item-menu-btn" title="Actions"
            onclick="event.stopPropagation(); fileManager.openItemMenu(event, '${f.path}', ${f.is_dir}, '${f.category}')">
            <i data-lucide="more-vertical"></i>
          </button>
          <div class="file-icon-wrap" style="color: ${this.getCategoryColor(f.category)};">
            <i data-lucide="${icon}"></i>
          </div>
          <div class="file-name" title="${f.name}">${f.name}</div>
          <div class="file-meta">${f.size_human}</div>
        </div>
      `;
    }).join('');

    // Render List View
    listBody.innerHTML = files.map(f => {
      const isSelected = this.selectedPaths.has(f.path);
      const icon = this.getCategoryIcon(f.category);
      // Secondary line shown under the name on phones (where the size /
      // modified columns are hidden) so that info isn't lost.
      const metaBits = [];
      if (!f.is_dir && f.size_human) metaBits.push(f.size_human);
      if (f.modified_human) metaBits.push(f.modified_human);
      const rowMeta = metaBits.join(' · ');
      return `
        <tr class="${isSelected ? 'selected' : ''}" onclick="fileManager.onItemClick(event, '${f.path}', ${f.is_dir}, '${f.category}')"
          oncontextmenu="return fileManager.openItemMenu(event, '${f.path}', ${f.is_dir}, '${f.category}')">
          <td class="fm-col-select"><input type="checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); fileManager.toggleSelect('${f.path}');"></td>
          <td class="fm-col-name">
            <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
              <i data-lucide="${icon}" style="width: 16px; height: 16px; flex-shrink: 0; color: ${this.getCategoryColor(f.category)};"></i>
              <div style="min-width: 0;">
                <strong style="color: var(--text-main);">${f.name}</strong>
                <div class="fm-row-meta">${rowMeta}</div>
              </div>
            </div>
          </td>
          <td class="fm-col-size">${f.size_human}</td>
          <td class="fm-col-type"><span class="nav-badge">${f.category}</span></td>
          <td class="fm-col-modified">${f.modified_human}</td>
          <td class="fm-col-perms"><code>${f.permissions}</code></td>
          <td class="fm-col-actions" style="text-align: right;">
            <button class="btn btn-secondary btn-icon" style="width: 28px; height: 28px;" onclick="event.stopPropagation(); fileManager.openItem('${f.path}', ${f.is_dir}, '${f.category}')" title="Open / Preview">
              <i data-lucide="eye" style="width: 14px; height: 14px;"></i>
            </button>
            <button class="btn btn-secondary btn-icon" style="width: 28px; height: 28px;" onclick="event.stopPropagation(); fileManager.downloadItem('${f.path}', ${f.is_dir})" title="Download">
              <i data-lucide="download" style="width: 14px; height: 14px;"></i>
            </button>
            <button class="btn btn-secondary btn-icon" style="width: 28px; height: 28px;" onclick="event.stopPropagation(); fileManager.promptRename('${f.path}', '${f.name}')" title="Rename">
              <i data-lucide="edit-2" style="width: 14px; height: 14px;"></i>
            </button>
            <button class="btn btn-danger btn-icon" style="width: 28px; height: 28px;" onclick="event.stopPropagation(); fileManager.deleteSingle('${f.path}', '${f.name}')" title="Delete">
              <i data-lucide="trash" style="width: 14px; height: 14px;"></i>
            </button>
          </td>
        </tr>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  onItemClick(event, path, isDir, category) {
    if (this._justDragged) {
      this._justDragged = false;
      event.stopPropagation();
      event.preventDefault();
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      this.toggleSelect(path);
      return;
    }
    // Double click behavior or single click
    if (isDir) {
      this.navigateTo(path);
    } else {
      this.openItem(path, isDir, category);
    }
  }

  // ---- Per-item actions menu (grid ⋮ button / right-click) ----
  // Grid cards used to offer no actions at all — copy/move/rename/delete were
  // only reachable from list-view buttons or the bulk bar. One reusable popup
  // serves both the ⋮ button and right-click, in both view modes.

  /** Build the reusable popup once; item clicks dispatch via runItemAction(). */
  ensureItemMenu() {
    let menu = document.getElementById('fm-item-menu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'fm-item-menu';
    menu.className = 'fm-ctx-menu';
    menu.innerHTML = `
      <button data-action="open"><i data-lucide="eye"></i><span>Open</span></button>
      <button data-action="download"><i data-lucide="download"></i><span>Download</span></button>
      <button data-action="rename"><i data-lucide="edit-2"></i><span>Rename</span></button>
      <button data-action="move"><i data-lucide="folder-input"></i><span>Move to…</span></button>
      <button data-action="copy"><i data-lucide="copy"></i><span>Copy to…</span></button>
      <div class="fm-ctx-sep"></div>
      <button data-action="delete" class="fm-ctx-danger"><i data-lucide="trash"></i><span>Delete</span></button>
    `;
    document.body.appendChild(menu);

    menu.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const target = this._ctxTarget;
      this.closeItemMenu();
      if (target) this.runItemAction(btn.dataset.action, target);
    });
    // Any click outside closes it. The opener calls stopPropagation(), so the
    // very click that opened the menu doesn't immediately close it again.
    document.addEventListener('click', (e) => {
      if (menu.classList.contains('fm-ctx-on') && !menu.contains(e.target)) this.closeItemMenu();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.closeItemMenu();
    });
    window.addEventListener('resize', () => this.closeItemMenu());
    window.addEventListener('scroll', () => this.closeItemMenu(), true);
    return menu;
  }

  /** Open the actions menu for one item, anchored to the pointer. */
  openItemMenu(event, path, isDir, category) {
    event.preventDefault();
    event.stopPropagation();
    const menu = this.ensureItemMenu();
    this._ctxTarget = { path, isDir, category };

    // First row follows the item type: folders open, media plays, the rest
    // previews; download label reflects the folder → zip path.
    const openBtn = menu.querySelector('[data-action="open"]');
    const dlLabel = menu.querySelector('[data-action="download"] span');
    if (openBtn) {
      const [icon, label] = isDir ? ['folder-open', 'Open']
        : (category === 'video' || category === 'audio') ? ['play', 'Play']
        : ['eye', 'Preview'];
      openBtn.innerHTML = `<i data-lucide="${icon}"></i><span>${label}</span>`;
    }
    if (dlLabel) dlLabel.textContent = isDir ? 'Download as ZIP' : 'Download';
    if (window.lucide) lucide.createIcons();

    menu.classList.add('fm-ctx-on');
    // Position once visible so offsetWidth/Height are real, then clamp so the
    // menu never spills off the viewport edge.
    const x = Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8);
    const y = Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8);
    menu.style.left = `${Math.max(8, x)}px`;
    menu.style.top = `${Math.max(8, y)}px`;
    return false; // suppress the native right-click menu
  }

  closeItemMenu() {
    const menu = document.getElementById('fm-item-menu');
    if (menu) menu.classList.remove('fm-ctx-on');
    this._ctxTarget = null;
  }

  runItemAction(action, { path, isDir, category }) {
    switch (action) {
      case 'open':
        this.openItem(path, isDir, category);
        break;
      case 'download':
        this.downloadItem(path, isDir);
        break;
      case 'rename':
        this.promptRename(path, path.split('/').pop());
        break;
      case 'move':
        this._selectForAction(path);
        this.openDestPicker('move');
        break;
      case 'copy':
        this._selectForAction(path);
        this.openDestPicker('copy');
        break;
      case 'delete': {
        // An item inside an existing multi-selection deletes the whole
        // selection (standard file-manager behavior); otherwise just itself.
        const paths = (this.selectedPaths.has(path) && this.selectedPaths.size > 1)
          ? Array.from(this.selectedPaths) : [path];
        this.deleteTargets(paths);
        break;
      }
    }
  }

  /** Point the selection at `path` — keeping a multi-selection that already
   *  contains it — so move/copy act on exactly what the user expects. */
  _selectForAction(path) {
    if (!(this.selectedPaths.has(path) && this.selectedPaths.size > 1)) {
      this.selectedPaths.clear();
      this.selectedPaths.add(path);
    }
    this.updateBulkBar();
    this.renderFiles(this.currentFiles);
  }

  toggleSelect(path) {
    if (this.selectedPaths.has(path)) {
      this.selectedPaths.delete(path);
    } else {
      this.selectedPaths.add(path);
    }
    this.updateBulkBar();
    this.renderFiles(this.currentFiles);
  }

  selectAll() {
    this.currentFiles.forEach(f => this.selectedPaths.add(f.path));
    this.updateBulkBar();
    this.renderFiles(this.currentFiles);
  }

  selectNone() {
    this.selectedPaths.clear();
    this.updateBulkBar();
    this.renderFiles(this.currentFiles);
  }

  _onGridMouseDown(e) {
    if (e.button !== 0) return;
    if (e.target.closest('.file-item-checkbox, .fm-item-menu-btn, button')) return;
    if (this.viewMode !== 'grid') return;
    this._dragState.potential = true;
    this._dragState.active = false;
    this._dragState.startX = e.clientX;
    this._dragState.startY = e.clientY;
  }

  _onGridMouseMove(e) {
    if (!this._dragState.potential && !this._dragState.active) return;
    const dx = e.clientX - this._dragState.startX;
    const dy = e.clientY - this._dragState.startY;
    if (!this._dragState.active && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
      this._dragState.active = true;
      this._dragState.potential = false;
      if (!e.ctrlKey && !e.metaKey) {
        this.selectedPaths.clear();
        this.updateBulkBar();
      }
      if (!this._dragState.box) {
        this._dragState.box = document.createElement('div');
        this._dragState.box.className = 'fm-selection-box';
        document.body.appendChild(this._dragState.box);
      }
      this._dragState.box.style.left = this._dragState.startX + 'px';
      this._dragState.box.style.top = this._dragState.startY + 'px';
      this._dragState.box.style.width = '0px';
      this._dragState.box.style.height = '0px';
      this._dragState.box.style.display = 'block';
    }
    if (!this._dragState.active) return;
    const x = Math.min(e.clientX, this._dragState.startX);
    const y = Math.min(e.clientY, this._dragState.startY);
    const w = Math.abs(e.clientX - this._dragState.startX);
    const h = Math.abs(e.clientY - this._dragState.startY);
    this._dragState.box.style.left = x + 'px';
    this._dragState.box.style.top = y + 'px';
    this._dragState.box.style.width = w + 'px';
    this._dragState.box.style.height = h + 'px';
    const boxRect = this._dragState.box.getBoundingClientRect();
    const items = document.querySelectorAll('.file-item-grid');
    items.forEach(item => {
      const itemRect = item.getBoundingClientRect();
      const intersects = !(boxRect.right < itemRect.left || boxRect.left > itemRect.right || boxRect.bottom < itemRect.top || boxRect.top > itemRect.bottom);
      const path = item.dataset.path;
      if (intersects) {
        item.classList.add('selected');
        this.selectedPaths.add(path);
      } else if (!e.ctrlKey && !e.metaKey) {
        item.classList.remove('selected');
        this.selectedPaths.delete(path);
      }
    });
    this.updateBulkBar();
  }

  _onGridMouseUp(e) {
    if (this._dragState.potential) {
      this._dragState.potential = false;
      return;
    }
    if (!this._dragState.active) return;
    this._dragState.active = false;
    this._dragState.potential = false;
    if (this._dragState.box) {
      this._dragState.box.style.display = 'none';
    }
    this._justDragged = true;
    setTimeout(() => { this._justDragged = false; }, 0);
    this.renderFiles(this.currentFiles);
  }

  updateBulkBar() {
    const bar = document.getElementById('fm-bulk-bar');
    const countEl = document.getElementById('fm-bulk-count');
    const count = this.selectedPaths.size;
    if (count > 0) {
      bar.style.display = 'flex';
      countEl.textContent = `${count} item${count > 1 ? 's' : ''} selected`;
    } else {
      bar.style.display = 'none';
    }
  }

  async openItem(path, isDir, category) {
    if (isDir) {
      this.navigateTo(path);
      return;
    }

    const rawUrl = api.getRawFileUrl(path);

    if (category === 'video') {
      app.switchView('media');
      mediaPlayer.playVideo(rawUrl, path.split('/').pop(), path);
    } else if (category === 'audio') {
      app.switchView('media');
      mediaPlayer.playAudio(rawUrl, path.split('/').pop());
    } else if (category === 'image') {
      document.getElementById('lightbox-title').textContent = path.split('/').pop();
      document.getElementById('lightbox-img').src = rawUrl;
      app.openModal('modal-lightbox');
    } else if (category === 'pdf') {
      // NEVER the text editor: a PDF is binary, so it rendered as raw
      // "%PDF-1.3 / stream …" bytes in an editable box whose Save button would
      // have corrupted the file.
      pdfViewer.open(path);
    } else if (category === 'text') {
      try {
        const data = await api.readFile(path);
        this.editingFilePath = path;
        document.getElementById('editor-title').innerHTML = `<i data-lucide="file-edit"></i> ${path.split('/').pop()}`;
        document.getElementById('editor-filename-group').style.display = 'none';
        document.getElementById('editor-content').value = data.content;
        app.openModal('modal-file-editor');
        if (window.lucide) lucide.createIcons();
      } catch (err) {
        window.open(rawUrl, '_blank');
      }
    } else {
      // Direct file download for archives / binaries
      this.downloadSingleFile(path);
    }
  }

  // ---- Downloads (stream to disk, never buffer in a Blob) ----

  /** Row-level download: files stream directly, folders go through zip. */
  downloadItem(path, isDir) {
    if (isDir) this.downloadZip([path]);
    else this.downloadSingleFile(path);
  }

  /**
   * Download a single file by navigating a hidden anchor to the streaming
   * endpoint. The browser writes straight to disk (with its own progress UI)
   * and supports resume/Range — no multi-GB Blob held in memory.
   */
  downloadSingleFile(path) {
    const name = (path.split('/').pop()) || 'file';
    if (this.isDesktop()) {
      this.desktopDownload(`${api.baseUrl}/api/files/download?path=${encodeURIComponent(path)}`, name);
    } else {
      const a = document.createElement('a');
      a.href = `${api.baseUrl}/api/files/download?path=${encodeURIComponent(path)}`;
      a.download = name;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    this.showToast(`Downloading "${name}"…`);
  }

  /**
   * Download several items (or a folder) as a zip with live progress.
   * Starts an async zip build on the backend, polls the op_id, then
   * triggers the actual file download once the archive is ready.
   */
  async downloadZip(paths) {
    const modal = document.getElementById('modal-zip-progress');
    const bar = document.getElementById('zip-progress-bar');
    const status = document.getElementById('zip-progress-status');
    const detail = document.getElementById('zip-progress-detail');
    if (!modal || !bar || !status || !detail) return;

    try {
      const res = await api.startZip(paths);
      const opId = res.op_id;
      modal.classList.add('active');
      bar.style.width = '0%';
      status.textContent = 'Creating archive…';
      detail.textContent = '';

      let pollCount = 0;
      const poll = async () => {
        const st = await api.getZipStatus(opId);
        if (!st) {
          modal.classList.remove('active');
          this.showToast('Zip operation expired or not found.');
          return;
        }
        const pct = st.total_bytes > 0 ? Math.min(100, (st.transferred_bytes / st.total_bytes) * 100) : 0;
        bar.style.width = pct.toFixed(0) + '%';
        status.textContent = st.current_file || 'Creating archive…';
        detail.textContent = `${st.done_files}/${st.total_files} files · ${formatSize(st.transferred_bytes)}`;

        if (st.status === 'done' && st.zip_path) {
          bar.style.width = '100%';
          status.textContent = 'Archive ready! Starting download…';
          detail.textContent = `${st.done_files} files · ${formatSize(st.transferred_bytes)}`;
          await new Promise(r => setTimeout(r, 400));
          modal.classList.remove('active');
          this.downloadZipFile(opId);
        } else if (st.status === 'error') {
          modal.classList.remove('active');
          this.showToast('Failed to create zip: ' + (st.errors && st.errors.join(', ') || 'unknown error'));
        } else {
          pollCount++;
          setTimeout(poll, Math.min(500, 200 + pollCount * 20));
        }
      };
      poll();
    } catch (err) {
      modal.classList.remove('active');
      this.showToast(err.message);
    }
  }

  downloadZipFile(opId) {
    const url = api.downloadZip(opId);
    if (this.isDesktop()) {
      this.desktopDownload(url, 'diskpulse_archive.zip');
    } else {
      const a = document.createElement('a');
      a.href = url;
      a.download = 'diskpulse_archive.zip';
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    this.showToast('Downloading archive…');
  }

  isDesktop() {
    return !!(window.pywebview && window.pywebview.api);
  }

  desktopDownload(url, filename) {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_download) {
        window.pywebview.api.save_download(url, filename);
        return;
      }
    } catch (e) {
      console.warn('Desktop download bridge failed, falling back to browser:', e);
    }
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /** Minimal transient toast; the file manager had no notification helper. */
  showToast(message) {
    let host = document.getElementById('fm-toast-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'fm-toast-host';
      host.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(host);
    }
    const toast = document.createElement('div');
    toast.textContent = message;
    toast.style.cssText = 'background:var(--bg-elevated,#1e293b);color:var(--text-main,#e2e8f0);border:1px solid var(--accent-cyan,#22d3ee);border-radius:8px;padding:10px 16px;font-size:0.85rem;box-shadow:0 6px 20px rgba(0,0,0,0.35);opacity:0;transform:translateY(8px);transition:opacity .2s,transform .2s;';
    host.appendChild(toast);
    requestAnimationFrame(() => { toast.style.opacity = '1'; toast.style.transform = 'translateY(0)'; });
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(8px)';
      setTimeout(() => toast.remove(), 250);
    }, 3200);
  }

  /** Open the styled rename modal, pre-filled with the current name. */
  promptRename(path, oldName) {
    this.renameTargetPath = path;
    this.renameOldName = oldName;
    const input = document.getElementById('modal-rename-input');
    if (input) input.value = oldName;
    app.openModal('modal-rename');
    // Focus and select just the base name (before the extension) for quick edits.
    setTimeout(() => {
      if (!input) return;
      input.focus();
      const dot = oldName.lastIndexOf('.');
      if (dot > 0) input.setSelectionRange(0, dot);
      else input.select();
    }, 60);
    if (window.lucide) lucide.createIcons();
  }

  async submitRename() {
    const input = document.getElementById('modal-rename-input');
    const newName = (input?.value || '').trim();
    if (!newName || newName === this.renameOldName) {
      app.closeModal('modal-rename');
      return;
    }
    try {
      await api.renameFile(this.renameTargetPath, newName);
      app.closeModal('modal-rename');
      this.showToast(`Renamed to "${newName}".`);
      this.refresh();
    } catch (err) {
      this.showToast(err.message);
    }
  }

  async deleteSingle(path, name) {
    await this.deleteTargets([path]);
  }

  /** Confirm + delete one or many items (list-view button and the ⋮ / right-
   *  click menu both route here). */
  async deleteTargets(paths) {
    if (!paths.length) return;
    const many = paths.length > 1;
    const label = many ? `${paths.length} selected items` : `"${paths[0].split('/').pop()}"`;
    const ok = await this.confirmModal({
      title: many ? 'Delete items' : 'Delete item',
      message: `Permanently delete ${label}? This cannot be undone.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteFiles(paths);
      paths.forEach(p => this.selectedPaths.delete(p));
      this.updateBulkBar();
      this.showToast(many ? `Deleted ${paths.length} items.` : `Deleted "${paths[0].split('/').pop()}".`);
      this.refresh();
    } catch (err) {
      this.showToast(err.message);
    }
  }

  // ---- Styled confirm modal (Promise-based replacement for window.confirm) ----

  /**
   * Show the generic confirm modal and resolve true/false. Any dismissal
   * (Cancel, the X, backdrop click, Escape) resolves false so callers never
   * hang. Falls back to window.confirm if the modal markup is missing.
   */
  confirmModal({ title = 'Confirm', message = 'Are you sure?', confirmLabel = 'Confirm', danger = true } = {}) {
    return new Promise((resolve) => {
      const modal = document.getElementById('modal-confirm');
      const okBtn = document.getElementById('confirm-ok-btn');
      const cancelBtn = document.getElementById('confirm-cancel-btn');
      if (!modal || !okBtn || !cancelBtn) { resolve(window.confirm(message)); return; }

      const titleEl = document.getElementById('confirm-title');
      const msgEl = document.getElementById('confirm-message');
      const okLabel = document.getElementById('confirm-ok-label');
      if (titleEl) titleEl.innerHTML = `<i data-lucide="alert-triangle"></i> ${title}`;
      if (msgEl) msgEl.textContent = message;
      if (okLabel) okLabel.textContent = confirmLabel;
      okBtn.className = danger ? 'btn btn-danger' : 'btn btn-primary';

      const xBtn = modal.querySelector('.modal-header .modal-close');
      let done = false;
      const cleanup = (result) => {
        if (done) return;
        done = true;
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        if (xBtn) xBtn.removeEventListener('click', onCancel);
        modal.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKey);
        modal.classList.remove('active');
        resolve(result);
      };
      const onOk = () => cleanup(true);
      const onCancel = () => cleanup(false);
      const onBackdrop = (e) => { if (e.target === modal) cleanup(false); };
      const onKey = (e) => { if (e.key === 'Escape') cleanup(false); };

      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      if (xBtn) xBtn.addEventListener('click', onCancel);
      modal.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKey);

      modal.classList.add('active');
      if (window.lucide) lucide.createIcons();
    });
  }

  // ---- Move / Copy destination picker ----

  openDestPicker(mode) {
    const sources = Array.from(this.selectedPaths);
    if (!sources.length) return;
    this.destMode = mode;
    this.destSources = sources;
    this.destPath = this.currentPath; // start browsing from the current folder
    const isMove = mode === 'move';
    const n = sources.length;
    const titleEl = document.getElementById('dest-picker-title');
    const descEl = document.getElementById('dest-picker-desc');
    const labelEl = document.getElementById('dest-picker-confirm-label');
    if (titleEl) titleEl.innerHTML = `<i data-lucide="${isMove ? 'folder-input' : 'copy'}"></i> ${isMove ? 'Move' : 'Copy'} ${n} item${n > 1 ? 's' : ''}`;
    if (descEl) descEl.textContent = `Choose a destination folder, then click "${isMove ? 'Move' : 'Copy'} here".`;
    if (labelEl) labelEl.textContent = `${isMove ? 'Move' : 'Copy'} here`;
    app.openModal('modal-dest-picker');
    this.renderDestPicker();
  }

  navigateDestPicker(path) {
    this.destPath = path;
    this.renderDestPicker();
  }

  async renderDestPicker() {
    const listEl = document.getElementById('dest-picker-list');
    const crumbEl = document.getElementById('dest-picker-breadcrumbs');
    const warnEl = document.getElementById('dest-picker-warn');
    const confirmBtn = document.getElementById('dest-picker-confirm');
    if (!listEl) return;

    let res;
    try {
      res = await api.listFiles(this.destPath);
    } catch (err) {
      listEl.innerHTML = `<div style="padding:16px;color:var(--accent-rose,#f43f5e);font-size:0.85rem;">${err.message}</div>`;
      return;
    }

    // Breadcrumbs (clickable, last is current)
    const crumbs = res.breadcrumbs || [];
    if (crumbEl) {
      crumbEl.innerHTML = crumbs.map((c, i) => {
        const label = c.name === 'root' ? 'root' : c.name;
        const isLast = i === crumbs.length - 1;
        if (isLast) return `<span style="color:var(--accent-cyan,#22d3ee);font-weight:600;">${label}</span>`;
        return `<span style="color:#38bdf8;cursor:pointer;" onclick="fileManager.navigateDestPicker('${c.path}')">${label}</span><span style="color:var(--text-dim);">/</span>`;
      }).join('');
    }

    // Folders only; when moving, hide the folders being moved.
    const folders = (res.files || []).filter(f =>
      f.is_dir && !(this.destMode === 'move' && this.destSources.includes(f.path)));

    if (!folders.length) {
      listEl.innerHTML = `<div style="padding:20px 16px;color:var(--text-dim);font-size:0.82rem;text-align:center;">No subfolders here.<br>Drop into this folder with the button below.</div>`;
    } else {
      listEl.innerHTML = folders.map(f => `
        <div class="dest-row" onclick="fileManager.navigateDestPicker('${f.path}')"
          style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;border-bottom:1px solid var(--border-glass);"
          onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background=''">
          <i data-lucide="folder" style="width:16px;height:16px;color:#38bdf8;flex-shrink:0;"></i>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${f.name}</span>
          <i data-lucide="chevron-right" style="width:14px;height:14px;color:var(--text-dim);flex-shrink:0;"></i>
        </div>
      `).join('');
    }

    // Guard: can't move a folder into itself or its own subtree (mirrors backend).
    const intoSelf = this.destMode === 'move' &&
      this.destSources.some(s => this.destPath === s || this.destPath.startsWith(s + '/'));
    if (warnEl) warnEl.textContent = intoSelf ? "Can't move a folder into itself." : '';
    if (confirmBtn) {
      confirmBtn.disabled = intoSelf;
      confirmBtn.style.opacity = intoSelf ? '0.5' : '';
      confirmBtn.style.pointerEvents = intoSelf ? 'none' : '';
    }

    if (window.lucide) lucide.createIcons();
  }

  async confirmDest() {
    const sources = this.destSources || [];
    if (!sources.length) return;
    if (this.activeTransfer) {
      this.showToast('A transfer is already in progress — wait for it to finish.');
      return;
    }
    const isMove = this.destMode === 'move';
    const confirmBtn = document.getElementById('dest-picker-confirm');
    if (confirmBtn) confirmBtn.disabled = true;
    try {
      // The backend now starts the transfer in the background and returns an
      // op_id immediately; trackTransfer() polls it for live progress.
      const res = isMove
        ? await api.moveFiles(sources, this.destPath)
        : await api.copyFiles(sources, this.destPath);
      app.closeModal('modal-dest-picker');
      this.selectedPaths.clear();
      this.updateBulkBar();
      this.refresh();
      this.trackTransfer(res.op_id, isMove);
    } catch (err) {
      this.showToast(err.message);
    } finally {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.style.opacity = '';
        confirmBtn.style.pointerEvents = '';
      }
    }
  }

  // ---- Live transfer progress (move / copy) ----

  /** Human-readable byte size — see formatSize() in api.js. */
  formatBytes(bytes) {
    return formatSize(bytes);
  }

  formatEta(seconds) {
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  /**
   * Open the progress modal and poll the backend operation until it finishes.
   * The transfer keeps running server-side even if the modal is hidden; the
   * final result always lands as a toast.
   */
  trackTransfer(opId, isMove) {
    this.activeTransfer = opId;
    const verb = isMove ? 'Moving' : 'Copying';
    const titleEl = document.getElementById('transfer-title');
    if (titleEl) titleEl.innerHTML = `<i data-lucide="${isMove ? 'folder-input' : 'copy'}"></i> ${verb}…`;
    this.updateTransferUI({
      status: 'running', total_bytes: 0, transferred_bytes: 0,
      total_files: 0, done_files: 0, total_items: 0, done_items: 0,
      current_item: '', current_file: ''
    });
    app.openModal('modal-transfer-progress');

    let lastBytes = 0;
    let lastTime = performance.now();
    let speed = 0;

    const poll = async () => {
      if (this.activeTransfer !== opId) return;
      let state;
      try {
        state = await api.getFileOperation(opId);
      } catch (err) {
        this.activeTransfer = null;
        app.closeModal('modal-transfer-progress');
        this.showToast(`Transfer status lost: ${err.message}`);
        return;
      }
      if (this.activeTransfer !== opId) return;

      // Smoothed speed from byte deltas between polls (for ETA display).
      const now = performance.now();
      const dt = (now - lastTime) / 1000;
      if (dt > 0.05) {
        const inst = Math.max(0, (state.transferred_bytes - lastBytes) / dt);
        speed = speed > 0 ? speed * 0.7 + inst * 0.3 : inst;
        lastBytes = state.transferred_bytes;
        lastTime = now;
      }

      this.updateTransferUI(state, speed);

      if (state.status === 'running') {
        setTimeout(poll, 400);
        return;
      }

      // Finished (done or error)
      this.activeTransfer = null;
      app.closeModal('modal-transfer-progress');
      const past = isMove ? 'Moved' : 'Copied';
      const okCount = (state.completed || []).length;
      const errCount = (state.errors || []).length;
      if (okCount && errCount) {
        this.showToast(`${past} ${okCount}, ${errCount} failed.`);
      } else if (errCount) {
        this.showToast(`${isMove ? 'Move' : 'Copy'} failed: ${state.errors[0]}`);
      } else {
        this.showToast(`${past} ${okCount} item${okCount === 1 ? '' : 's'}.`);
      }
      this.refresh();
    };
    poll();
  }

  updateTransferUI(state, speed = 0) {
    const bar = document.getElementById('transfer-bar');
    const percentEl = document.getElementById('transfer-percent');
    const speedEl = document.getElementById('transfer-speed');
    const currentEl = document.getElementById('transfer-current');
    const detailEl = document.getElementById('transfer-detail');
    if (!bar) return;

    let pct = 0;
    if (state.total_bytes > 0) pct = (state.transferred_bytes / state.total_bytes) * 100;
    else if (state.total_files > 0) pct = (state.done_files / state.total_files) * 100;
    else if (state.total_items > 0) pct = (state.done_items / state.total_items) * 100;
    if (state.status !== 'running') pct = 100;
    pct = Math.max(0, Math.min(100, pct));

    bar.style.width = `${pct}%`;

    const scanning = state.status === 'running' && !state.total_bytes && !state.total_files && !state.total_items;
    if (percentEl) percentEl.textContent = scanning ? 'Scanning…' : `${Math.floor(pct)}%`;

    if (speedEl) {
      if (state.status === 'running' && speed > 0) {
        let txt = `${this.formatBytes(speed)}/s`;
        if (state.total_bytes > state.transferred_bytes) {
          txt += ` · ${this.formatEta((state.total_bytes - state.transferred_bytes) / speed)} left`;
        }
        speedEl.textContent = txt;
      } else {
        speedEl.textContent = '';
      }
    }

    if (currentEl) {
      const item = (state.current_item || '').split('/').pop();
      currentEl.textContent = state.current_file ? `${item} → ${state.current_file}` : (item || '');
      currentEl.title = currentEl.textContent;
    }

    if (detailEl) {
      const parts = [];
      if (state.total_bytes > 0) parts.push(`${this.formatBytes(state.transferred_bytes)} of ${this.formatBytes(state.total_bytes)}`);
      if (state.total_files > 0) parts.push(`${state.done_files}/${state.total_files} files`);
      if (state.total_items > 1) parts.push(`${state.done_items}/${state.total_items} items`);
      detailEl.textContent = parts.join(' · ');
    }
  }

  getCategoryIcon(cat) {
    switch (cat) {
      case 'folder': return 'folder';
      case 'video': return 'film';
      case 'audio': return 'music';
      case 'image': return 'image';
      case 'pdf': return 'file-text';
      case 'text': return 'file-code';
      case 'archive': return 'archive';
      case 'executable': return 'cpu';
      default: return 'file';
    }
  }

  getCategoryColor(cat) {
    switch (cat) {
      case 'folder': return '#38bdf8';
      case 'video': return '#f43f5e';
      case 'audio': return '#8b5cf6';
      case 'image': return '#10b981';
      case 'pdf': return '#f59e0b';
      case 'text': return '#00f2fe';
      case 'archive': return '#eab308';
      default: return '#94a3b8';
    }
  }
}

const fileManager = new FileManagerView();
