/**
 * Reusable Storage Folder Picker
 * ------------------------------------------------------------------
 * Browses the storage tree (folders only) and hands the chosen relative
 * path back through an onPick callback. Shared by the multi-device uploader
 * and the Add Download modal so both choose a destination the same way.
 *
 * This is deliberately INDEPENDENT of the move/copy picker in file_manager.js
 * (#modal-dest-picker) — that one carries move-into-itself guards and transfer
 * state we don't want to entangle with a simple "pick a folder" flow.
 *
 * Usage:
 *   folderPicker.open({
 *     title: 'Choose upload folder',
 *     startPath: '',                 // relative to storage root ('' = root)
 *     confirmLabel: 'Upload here',
 *     onPick: (relPath) => { ... }   // '' means the storage root
 *   });
 */
class FolderPicker {
  constructor() {
    this.path = '';
    this.onPick = null;
    this._bound = false;
  }

  _bind() {
    if (this._bound) return;
    document.getElementById('fp-confirm')
      ?.addEventListener('click', () => this._confirm());
    document.getElementById('fp-newfolder-btn')
      ?.addEventListener('click', () => this._createFolder());
    document.getElementById('fp-newfolder-input')
      ?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); this._createFolder(); }
      });
    this._bound = true;
  }

  open({ title = 'Choose folder', startPath = '', confirmLabel = 'Select this folder', onPick = null } = {}) {
    this._bind();
    this.path = (startPath || '').replace(/^\/+/, '');
    this.onPick = onPick;
    const titleEl = document.getElementById('fp-title');
    const labelEl = document.getElementById('fp-confirm-label');
    if (titleEl) titleEl.innerHTML = `<i data-lucide="folder-search"></i> ${title}`;
    if (labelEl) labelEl.textContent = confirmLabel;
    const input = document.getElementById('fp-newfolder-input');
    if (input) input.value = '';
    app.openModal('modal-folder-picker');
    this.render();
  }

  navigate(path) {
    this.path = (path || '').replace(/^\/+/, '');
    this.render();
  }

  async render() {
    const listEl = document.getElementById('fp-list');
    const crumbEl = document.getElementById('fp-breadcrumbs');
    const curEl = document.getElementById('fp-current');
    if (!listEl) return;

    let res;
    try {
      res = await api.listFiles(this.path);
    } catch (err) {
      listEl.innerHTML = `<div style="padding:16px;color:var(--accent-rose,#f43f5e);font-size:0.85rem;">${err.message}</div>`;
      return;
    }

    // Chosen-folder indicator
    if (curEl) {
      curEl.textContent = this.path ? '/' + this.path : '/ (storage root)';
    }

    // Breadcrumbs (clickable, last is current)
    const crumbs = res.breadcrumbs || [];
    if (crumbEl) {
      crumbEl.innerHTML = crumbs.map((c, i) => {
        const label = c.name === 'root' ? 'root' : c.name;
        const isLast = i === crumbs.length - 1;
        if (isLast) return `<span style="color:var(--accent-cyan,#22d3ee);font-weight:600;">${label}</span>`;
        return `<span style="color:#38bdf8;cursor:pointer;" onclick="folderPicker.navigate('${this._esc(c.path)}')">${label}</span><span style="color:var(--text-dim);">/</span>`;
      }).join('');
    }

    // Folders only
    const folders = (res.files || []).filter(f => f.is_dir);
    if (!folders.length) {
      listEl.innerHTML = `<div style="padding:20px 16px;color:var(--text-dim);font-size:0.82rem;text-align:center;">No subfolders here.<br>Pick this folder below, or create one.</div>`;
    } else {
      listEl.innerHTML = folders.map(f => `
        <div class="dest-row" onclick="folderPicker.navigate('${this._esc(f.path)}')"
          style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;border-bottom:1px solid var(--border-glass);"
          onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background=''">
          <i data-lucide="folder" style="width:16px;height:16px;color:#38bdf8;flex-shrink:0;"></i>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${this._escHtml(f.name)}</span>
          <i data-lucide="chevron-right" style="width:14px;height:14px;color:var(--text-dim);flex-shrink:0;"></i>
        </div>
      `).join('');
    }

    if (window.lucide) lucide.createIcons();
  }

  async _createFolder() {
    const input = document.getElementById('fp-newfolder-input');
    const name = (input?.value || '').trim();
    if (!name) return;
    try {
      await api.createDirectory(this.path, name);
      if (input) input.value = '';
      // Step into the freshly created folder for convenience.
      this.navigate(this.path ? `${this.path}/${name}` : name);
    } catch (err) {
      alert(err.message || 'Could not create folder');
    }
  }

  _confirm() {
    if (typeof this.onPick === 'function') this.onPick(this.path);
    app.closeModal('modal-folder-picker');
  }

  // Escape a path for use inside a single-quoted inline handler.
  _esc(s) {
    return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  // Escape text for safe HTML rendering.
  _escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}

const folderPicker = new FolderPicker();
