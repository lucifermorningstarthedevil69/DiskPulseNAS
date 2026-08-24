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
