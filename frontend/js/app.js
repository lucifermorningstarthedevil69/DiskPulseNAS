/**
 * DiskPulse Application Shell & Controller
 */
class DiskPulseApp {
  constructor() {
    this.currentView = 'dashboard';
    this.previousView = null;
    this.bindGlobalEvents();
    this.init();
  }

  init() {
    // Start live telemetry WebSocket
    api.initTelemetryWebSocket();

    // Initial lucide icons rendering
    if (window.lucide) lucide.createIcons();

    // Initial load for active view
    this.switchView('dashboard');
  }

  bindGlobalEvents() {
    // Nav menu item clicks
    document.querySelectorAll('.nav-item[data-view]').forEach(item => {
      item.addEventListener('click', (e) => {
        const view = item.dataset.view;
        this.switchView(view);
        
        // Close mobile drawer if open
        document.getElementById('sidebar')?.classList.remove('mobile-open');
      });
    });

    // Mobile Menu Toggle
    document.getElementById('mobile-toggle')?.addEventListener('click', () => {
      document.getElementById('sidebar')?.classList.toggle('mobile-open');
    });

    // Tapping the backdrop closes the drawer
    document.getElementById('sidebar-overlay')?.addEventListener('click', () => {
      document.getElementById('sidebar')?.classList.remove('mobile-open');
    });

    // Quick Action Bar Buttons
    document.getElementById('quick-btn-terminal')?.addEventListener('click', () => {
      this.switchView('terminal');
    });

    // Modal Close Buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const modal = e.target.closest('.modal-backdrop');
        if (modal) modal.classList.remove('active');
      });
    });

    // Close modal on backdrop click
    document.querySelectorAll('.modal-backdrop').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('active');
      });
    });

    // Global keyboard shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal-backdrop.active').forEach(m => m.classList.remove('active'));
      }
    });
  }

  switchView(viewName) {
    // Leaving the media player? Fully stop playback so the server-side ffmpeg
    // transcode is torn down and the source file is released. A paused <video>
    // on its own keeps the /api/media/stream connection (and the file lock)
    // open, which blocks move/rename/delete and stalls server shutdown.
    if (this.currentView === 'media' && viewName !== 'media' &&
        typeof mediaPlayer !== 'undefined' && typeof mediaPlayer.stopPlayback === 'function') {
      mediaPlayer.stopPlayback();
    }

    if (viewName !== this.currentView) {
      this.previousView = this.currentView;
    }
    this.currentView = viewName;

    // Update Nav Sidebar
    document.querySelectorAll('.nav-item[data-view]').forEach(item => {
      if (item.dataset.view === viewName) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });

    // Update View Panels
    document.querySelectorAll('.view-panel').forEach(panel => {
      if (panel.id === `view-${viewName}`) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    });

    // Update Topbar View Title
    const titleEl = document.getElementById('current-view-title');
    if (titleEl) {
      titleEl.innerHTML = this.getViewTitleWithIcon(viewName);
    }

    // Trigger view-specific refreshes
    if (viewName === 'files') {
      fileManager.refresh();
    } else if (viewName === 'downloads') {
      downloadManager.fetchTasks();
    } else if (viewName === 'media') {
      mediaPlayer.loadMediaLibrary();
    } else if (viewName === 'history') {
      historyController.refresh();
    }

    if (window.lucide) lucide.createIcons();
  }

  getViewTitleWithIcon(viewName) {
    switch (viewName) {
      case 'dashboard':
        return '<i data-lucide="activity"></i> System & Drive Telemetry';
      case 'files':
        return '<i data-lucide="folder"></i> Interactive Storage Explorer';
      case 'downloads':
        return '<i data-lucide="download-cloud"></i> High-Speed Download Manager';
      case 'terminal':
        return '<i data-lucide="terminal"></i> Embedded NAS Terminal Shell';
      case 'media':
        return '<i data-lucide="play-circle"></i> In-Browser Web Media Player';
      case 'uploader':
        return '<i data-lucide="upload-cloud"></i> Multi-Device Storage Uploader';
      case 'deploy':
        return '<i data-lucide="server"></i> 1-Click NAS Standalone Deployer';
      case 'history':
        return '<i data-lucide="clock"></i> Transfer History';
      default:
        return '<i data-lucide="hard-drive"></i> DiskPulse NAS Hub';
    }
  }

  openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('active');
      if (window.lucide) lucide.createIcons();
    }
  }

  closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.remove('active');
    }
  }
}

// Global instance
const app = new DiskPulseApp();

/**
 * Show the generic confirm modal and resolve true/false.
 * Falls back to window.confirm if the markup is missing.
 */
function confirmModal({ title = 'Confirm', message = 'Are you sure?', confirmLabel = 'Confirm', danger = true } = {}) {
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
