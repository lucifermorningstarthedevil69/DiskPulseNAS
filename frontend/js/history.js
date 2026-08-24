/**
 * Transfer History Controller
 *
 * Shows completed downloads and uploads with:
 *   - Type filter (all / download / upload)
 *   - Retention setting (auto-prune after X days)
 *   - Clear all history button
 */
class HistoryController {
  constructor() {
    this.entries = [];
    this.filter = 'all';
    this.retentionDays = 7;

    this.bindEvents();
  }

  bindEvents() {
    const filterEl = document.getElementById('history-filter');
    filterEl?.addEventListener('change', (e) => {
      this.filter = e.target.value || 'all';
      this.render();
    });

    const clearBtn = document.getElementById('history-clear-btn');
    clearBtn?.addEventListener('click', () => this.clearAll());

    const retentionEl = document.getElementById('history-retention');
    retentionEl?.addEventListener('change', (e) => {
      this.updateRetention(parseInt(e.target.value, 10) || 7);
    });
  }

  async refresh() {
    try {
      const [historyRes, settingsRes] = await Promise.all([
        api.getHistory(),
        api.getHistorySettings(),
      ]);
      this.entries = historyRes.history || [];
      this.retentionDays = settingsRes.retention_days || 7;

      const retentionEl = document.getElementById('history-retention');
      if (retentionEl) retentionEl.value = String(this.retentionDays);

      this.render();
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  render() {
    const tbody = document.getElementById('history-tbody');
    if (!tbody) return;

    const filtered = this.filter === 'all'
      ? this.entries
      : this.entries.filter(e => e.type === this.filter);

    if (!filtered.length) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align: center; color: var(--text-dim); padding: 40px;">
            <i data-lucide="clock" style="width: 32px; height: 32px; margin-bottom: 8px; opacity: 0.4;"></i>
            <p>No transfer history yet. Completed downloads and uploads will appear here.</p>
          </td>
        </tr>`;
      if (window.lucide) lucide.createIcons();
      return;
    }

    tbody.innerHTML = filtered.map(e => {
      const when = this.formatTime(e.timestamp);
      const size = this.formatBytes(e.size_bytes);
      const dur = e.duration_secs ? this.formatDuration(e.duration_secs) : '--';
      const typeLabel = e.type === 'download' ? 'Download' : 'Upload';
      const typeColor = e.type === 'download' ? 'var(--accent-cyan)' : 'var(--accent-violet)';
      const statusColors = {
        completed: 'var(--accent-emerald)',
        error: 'var(--accent-rose)',
        cancelled: 'var(--accent-amber)',
        paused: 'var(--accent-amber)',
      };
      const statusColor = statusColors[e.status] || 'var(--text-muted)';
      const dest = e.destination || e.source || '--';

      return `
        <tr>
          <td style="white-space: nowrap; color: var(--text-muted); font-size: 0.8rem;">${when}</td>
          <td><span style="color: ${typeColor}; font-weight: 600; font-size: 0.8rem;">${typeLabel}</span></td>
          <td style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px;" title="${this._escHtml(e.filename || '')}">${this._escHtml(e.filename || '--')}</td>
          <td style="white-space: nowrap; color: var(--text-muted); font-size: 0.8rem;">${size}</td>
          <td style="white-space: nowrap;"><span style="color: ${statusColor}; font-weight: 600; font-size: 0.8rem; text-transform: uppercase;">${e.status || '--'}</span></td>
          <td style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; color: var(--text-muted); font-size: 0.8rem;" title="${this._escHtml(dest)}">${this._escHtml(dest)}</td>
          <td style="white-space: nowrap; text-align: right; color: var(--text-muted); font-size: 0.8rem;">${dur}</td>
        </tr>`;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  async updateRetention(days) {
    this.retentionDays = days;
    try {
      await api.setHistorySettings(days);
      await this.refresh();
    } catch (err) {
      console.error('Failed to update retention:', err);
    }
  }

  async clearAll() {
    if (!confirm('Clear all transfer history? This cannot be undone.')) return;
    try {
      await api.clearHistory();
      this.entries = [];
      this.render();
    } catch (err) {
      console.error('Failed to clear history:', err);
    }
  }

  // Helpers ---------------------------------------------------------------

  formatTime(ts) {
    if (!ts) return '--';
    const d = new Date(ts * 1000);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const pad = (n) => String(n).padStart(2, '0');
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (isToday) return `Today ${time}`;
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`;
  }

  formatBytes(bytes) {
    return formatSize(bytes);
  }

  formatDuration(secs) {
    if (!secs || secs <= 0) return '--';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  _escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

const historyController = new HistoryController();
