/**
 * PDF Preview — PDF.js rendered to a canvas.
 *
 * PDFs used to share the text-editor branch with .txt/.md, so opening one
 * dumped its raw bytes ("%PDF-1.3 / 3 0 obj / stream …") into an editable
 * textarea with a Save button — one keystroke away from corrupting the file.
 * They now come here instead.
 *
 * PDF.js is loaded LAZILY on first preview (it's ~1 MB) from the first source
 * that works, in this order:
 *
 *   1. frontend/vendor/pdfjs/  — a locally vendored copy. Nothing is fetched
 *      from the internet, so this is the only fully offline / air-gapped path.
 *      See frontend/vendor/pdfjs/README.md for the two files to drop in.
 *   2. jsDelivr, 3. cdnjs — same CDNs the app already uses for Chart.js,
 *      lucide and qrcodejs.
 *
 * If every source fails (offline with nothing vendored), the viewer degrades to
 * a button that opens the file in the browser's own PDF viewer, which needs no
 * JavaScript at all — /api/files/raw already serves application/pdf inline.
 */

// Each entry carries its own worker/cmap/font URLs: the npm layout (jsDelivr,
// files under build/) and the cdnjs layout (flat) are not the same shape.
const PDFJS_SOURCES = [
  {
    label: 'vendored',
    lib: 'vendor/pdfjs/pdf.min.js',
    worker: 'vendor/pdfjs/pdf.worker.min.js',
    cmaps: 'vendor/pdfjs/cmaps/',
    fonts: 'vendor/pdfjs/standard_fonts/',
    local: true,
  },
  {
    label: 'jsDelivr',
    lib: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js',
    worker: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js',
    cmaps: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/cmaps/',
    fonts: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/standard_fonts/',
  },
  {
    label: 'cdnjs',
    lib: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js',
    worker: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js',
    cmaps: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/',
    fonts: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/standard_fonts/',
  },
];

const PDFV_MIN_SCALE = 0.25;
const PDFV_MAX_SCALE = 5;
// How long to wait on one source's <script> before moving to the next.
const PDFV_LOAD_TIMEOUT_MS = 15000;

class PdfViewer {
  constructor() {
    this.lib = null;        // window.pdfjsLib, once some source has loaded
    this.source = null;     // the PDFJS_SOURCES entry that won
    this._loading = null;   // in-flight loader promise (de-dupes fast clicks)

    this.doc = null;        // PDFDocumentProxy
    this.loadTask = null;   // PDFDocumentLoadingTask
    this.path = '';         // storage-relative path, for the Download button
    this.rawUrl = '';
    this.pageNum = 1;
    this.scale = 0;         // 0 = "fit to width on next render"
    this.renderTask = null;
    this.renderSeq = 0;     // discards results of superseded renders
    this.bound = false;
  }

  get modal() { return document.getElementById('modal-pdf-viewer'); }
  get canvas() { return document.getElementById('pdfv-canvas'); }

  // ─── Open / close ──────────────────────────────────────────────────────────

  async open(path) {
    if (!this.modal) {                      // markup missing — don't dead-end
      window.open(api.getRawFileUrl(path), '_blank');
      return;
    }
    this._bind();

    // Opening a second PDF while one is loaded: drop the old document first so
    // its worker and page bitmaps go away instead of stacking up.
    await this._teardownDoc();

    this.path = path;
    this.rawUrl = api.getRawFileUrl(path);
    this.pageNum = 1;
    this.scale = 0;

    const name = path.split('/').pop();
    const titleEl = document.getElementById('pdfv-title');
    if (titleEl) titleEl.innerHTML = `<i data-lucide="file-text"></i> ${this._escHtml(name)}`;
    const countEl = document.getElementById('pdfv-page-count');
    if (countEl) countEl.textContent = '–';
    const inputEl = document.getElementById('pdfv-page-input');
    if (inputEl) inputEl.value = '1';

    app.openModal('modal-pdf-viewer');
    this._status('Loading PDF viewer…');

    let lib;
    try {
      lib = await this._ensureLib();
    } catch (err) {
      this._failed(err, 'The PDF viewer library could not be loaded.');
      return;
    }

    this._status('Opening document…');
    try {
      this.loadTask = lib.getDocument({
        url: this.rawUrl,
        cMapUrl: this.source.cmaps,      // CJK / non-Latin encodings
        cMapPacked: true,
        standardFontDataUrl: this.source.fonts,  // PDFs without embedded fonts
      });
      this.doc = await this.loadTask.promise;
    } catch (err) {
      this._failed(err, "This file couldn't be opened as a PDF.");
      return;
    }

    if (countEl) countEl.textContent = String(this.doc.numPages);
    await this._renderPage(1);
  }

  async close() {
    this.renderSeq++;                      // invalidate anything in flight
    this._cancelRender();
    await this._teardownDoc();

    // Zeroing the canvas frees the backing bitmap — a 300-dpi A4 page is ~35 MB.
    const c = this.canvas;
    if (c) {
      c.width = 0;
      c.height = 0;
      c.style.width = '';
      c.style.height = '';
    }
    app.closeModal('modal-pdf-viewer');
  }

  /** Fallback path: let the browser's built-in viewer handle it. */
  openExternally() {
    if (this.rawUrl) window.open(this.rawUrl, '_blank');
  }

  // ─── Library loading ───────────────────────────────────────────────────────

  async _ensureLib() {
    if (this.lib) return this.lib;
    if (this._loading) return this._loading;   // a second click must not re-inject

    this._loading = (async () => {
      const tried = [];
      for (const src of PDFJS_SOURCES) {
        try {
          // A missing vendored file would otherwise be fetched and parsed as
          // JS (or as the server's 404 body); probe first so the console stays
          // clean and we fall through quickly.
          if (src.local && !(await this._localExists(src.lib))) {
            tried.push(`${src.label}: not present`);
            continue;
          }
          await this._loadScript(src.lib);
          if (!window.pdfjsLib) throw new Error('loaded but pdfjsLib is undefined');

          this.lib = window.pdfjsLib;
          this.lib.GlobalWorkerOptions.workerSrc = src.worker;
          this.source = src;
          console.info(`[pdf] PDF.js loaded from ${src.label}`);
          return this.lib;
        } catch (err) {
          tried.push(`${src.label}: ${err?.message || err}`);
          console.warn(`[pdf] source unavailable — ${src.label}`, err);
        }
      }
      throw new Error(tried.join(' · '));
    })();

    try {
      return await this._loading;
    } finally {
      this._loading = null;    // this.lib is set on success, so retries are cheap
    }
  }

  /** HEAD-probe a same-origin asset without letting a 404 hit the console. */
  async _localExists(url) {
    try {
      const res = await fetch(url, { method: 'HEAD', cache: 'no-store' });
      if (!res.ok) return false;
      // DiskPulse serves JS with a javascript content type; anything else means
      // a stray route matched instead of a real file.
      const type = (res.headers.get('content-type') || '').toLowerCase();
      return type.includes('javascript') || type === '';
    } catch (_) {
      return false;
    }
  }

  _loadScript(url) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-pdfjs="${url}"]`);
      if (existing) {
        // Already injected by an earlier attempt — don't add a second copy.
        if (window.pdfjsLib) return resolve();
        // It finished loading but never defined pdfjsLib (an ESM 4.x build, or
        // an HTML error page served with a JS type). Waiting on `load` here
        // would hang forever — no further event is coming. Reject so the loader
        // moves on to the next source instead of leaving the modal spinning.
        if (existing.dataset.pdfjsState) {
          return reject(new Error(`already loaded without pdfjsLib (${existing.dataset.pdfjsState})`));
        }
        // Still in flight from a concurrent attempt — ride along with it.
        existing.addEventListener('load', () => resolve(), { once: true });
        existing.addEventListener('error', () => reject(new Error('load error')), { once: true });
        return;
      }

      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      s.dataset.pdfjs = url;

      // A CDN that accepts the connection and then stalls would otherwise pin
      // the modal on "Loading PDF viewer…" indefinitely (captive portals and
      // filtered LANs do exactly this). Give up and try the next source.
      const timer = setTimeout(() => {
        s.dataset.pdfjsState = 'timeout';
        s.remove();
        reject(new Error(`timed out after ${PDFV_LOAD_TIMEOUT_MS / 1000}s`));
      }, PDFV_LOAD_TIMEOUT_MS);

      s.addEventListener('load', () => {
        clearTimeout(timer);
        s.dataset.pdfjsState = 'loaded';
        resolve();
      }, { once: true });
      s.addEventListener('error', () => {
        clearTimeout(timer);
        s.dataset.pdfjsState = 'error';
        s.remove();          // removed, so a retry re-injects rather than hanging
        reject(new Error('network or 404'));
      }, { once: true });

      document.head.appendChild(s);
    });
  }

  // ─── Rendering ─────────────────────────────────────────────────────────────

  async _renderPage(n) {
    if (!this.doc) return;

    const total = this.doc.numPages;
    this.pageNum = Math.min(Math.max(1, Math.trunc(n) || 1), total);

    const input = document.getElementById('pdfv-page-input');
    if (input) input.value = String(this.pageNum);
    this._updateNav();

    // Two render tasks on one canvas throw; always cancel the outgoing one.
    const seq = ++this.renderSeq;
    this._cancelRender();

    let page;
    try {
      page = await this.doc.getPage(this.pageNum);
    } catch (err) {
      if (seq === this.renderSeq) this._failed(err, `Page ${this.pageNum} could not be read.`);
      return;
    }
    if (seq !== this.renderSeq) { this._cleanupPage(page); return; }   // superseded

    if (!this.scale) this.scale = this._fitScale(page);

    // Cap the device-pixel ratio: on a 3x phone screen an uncapped A4 canvas is
    // ~100 MB of bitmap, which is how mobile tabs get killed mid-render.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const viewport = page.getViewport({ scale: this.scale * dpr });
    const canvas = this.canvas;
    if (!canvas) return;

    canvas.width = Math.max(1, Math.floor(viewport.width));
    canvas.height = Math.max(1, Math.floor(viewport.height));
    // CSS size stays in layout pixels, so the extra DPR detail sharpens the
    // page instead of doubling its size.
    canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
    canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;

    this._status('');   // reveals the canvas

    try {
      this.renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
      await this.renderTask.promise;
    } catch (err) {
      // Cancelling is normal when paging quickly — only real failures surface.
      const name = err?.name || '';
      if (name !== 'RenderingCancelledException' && seq === this.renderSeq) {
        this._failed(err, `Page ${this.pageNum} could not be rendered.`);
        return;
      }
    } finally {
      if (seq === this.renderSeq) this.renderTask = null;
      this._cleanupPage(page);
    }

    this._updateZoomLabel();
    const scroller = document.getElementById('pdfv-scroll');
    if (scroller) scroller.scrollTop = 0;
  }

  _zoom(factor) {
    if (!this.doc) return;
    const base = this.scale || 1;
    this.scale = Math.min(PDFV_MAX_SCALE, Math.max(PDFV_MIN_SCALE, base * factor));
    this._renderPage(this.pageNum);
  }

  _fitScale(page) {
    const box = document.getElementById('pdfv-scroll');
    // 34px covers the container's own padding on both sides.
    const avail = Math.max(240, (box?.clientWidth || 820) - 34);
    const unit = page.getViewport({ scale: 1 });
    return Math.min(2, Math.max(PDFV_MIN_SCALE, avail / unit.width));
  }

  _cancelRender() {
    if (!this.renderTask) return;
    try { this.renderTask.cancel(); } catch (_) { /* already settled */ }
    this.renderTask = null;
  }

  _cleanupPage(page) {
    try { page?.cleanup?.(); } catch (_) { /* nothing to release */ }
  }

  async _teardownDoc() {
    const doc = this.doc;
    const task = this.loadTask;
    this.doc = null;
    this.loadTask = null;
    try {
      if (doc) await doc.destroy();
      else if (task) await task.destroy();
    } catch (_) { /* already gone */ }
  }

  // ─── Chrome ────────────────────────────────────────────────────────────────

  _updateNav() {
    const total = this.doc?.numPages || 0;
    const prev = document.getElementById('pdfv-prev');
    const next = document.getElementById('pdfv-next');
    if (prev) prev.disabled = this.pageNum <= 1;
    if (next) next.disabled = this.pageNum >= total;
  }

  _updateZoomLabel() {
    const el = document.getElementById('pdfv-zoom-label');
    if (el) el.textContent = `${Math.round((this.scale || 1) * 100)}%`;
  }

  /** msg may contain markup — every caller passes literals or escaped text. */
  _status(msg, isError = false) {
    const el = document.getElementById('pdfv-status');
    const canvas = this.canvas;
    if (!el) return;
    if (!msg) {
      el.style.display = 'none';
      if (canvas) canvas.style.display = '';
      return;
    }
    el.innerHTML = msg;
    el.style.display = 'block';
    el.style.color = isError ? 'var(--accent-rose)' : 'var(--text-muted)';
    if (canvas) canvas.style.display = 'none';
  }

  _failed(err, headline) {
    console.error('[pdf] preview failed:', err);
    const detail = this._escHtml(err?.message || String(err || 'Unknown error'));
    this._status(
      `${this._escHtml(headline)}<br>` +
      `<span style="font-size: 0.75rem; color: var(--text-dim);">${detail}</span><br><br>` +
      `<button class="btn btn-secondary btn-sm" onclick="pdfViewer.openExternally()">` +
      `Open in the browser's PDF viewer</button>`,
      true
    );
  }

  _escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _bind() {
    if (this.bound) return;
    this.bound = true;

    const on = (id, fn) => document.getElementById(id)?.addEventListener('click', fn);
    on('pdfv-prev', () => this._renderPage(this.pageNum - 1));
    on('pdfv-next', () => this._renderPage(this.pageNum + 1));
    on('pdfv-zoom-in', () => this._zoom(1.25));
    on('pdfv-zoom-out', () => this._zoom(1 / 1.25));
    on('pdfv-fit', () => { this.scale = 0; this._renderPage(this.pageNum); });
    on('pdfv-newtab', () => this.openExternally());
    on('pdfv-download', () => { if (this.path) fileManager.downloadSingleFile(this.path); });

    document.getElementById('pdfv-page-input')?.addEventListener('change', (e) => {
      const n = parseInt(e.target.value, 10);
      if (Number.isFinite(n)) this._renderPage(n);
      else e.target.value = String(this.pageNum);
    });

    // app.js closes modals on Escape / backdrop / .modal-close, but it only
    // strips the CSS class. PDF.js holds a worker plus a page bitmap, so the
    // same gestures have to reach close() or the document leaks until the next
    // open. close() is idempotent, so double-handling is harmless.
    document.querySelectorAll('.pdfv-dismiss').forEach(btn => {
      btn.addEventListener('click', () => this.close());
    });
    this.modal?.addEventListener('click', (e) => {
      if (e.target === this.modal) this.close();
    });
    window.addEventListener('keydown', (e) => {
      if (!this.modal?.classList.contains('active')) return;
      if (e.key === 'Escape') { this.close(); return; }
      if (e.target?.id === 'pdfv-page-input') return;   // typing a page number
      if (e.key === 'ArrowRight' || e.key === 'PageDown') {
        e.preventDefault();
        this._renderPage(this.pageNum + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault();
        this._renderPage(this.pageNum - 1);
      }
    });
  }
}

const pdfViewer = new PdfViewer();
