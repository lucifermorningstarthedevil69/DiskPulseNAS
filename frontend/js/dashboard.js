/**
 * Dashboard & Telemetry Visualizer
 */

/**
 * Format a byte count for chart tooltips.
 * Delegates to the canonical formatSize() in api.js so the doughnut, the pool
 * card and the sidebar all describe the same bytes the same way.
 */
function formatStorageSize(bytes) {
  return formatSize(bytes);
}

/**
 * Short badge label for a drive with no readable temperature.
 *
 * The backend now says *why* (smart_reason) instead of leaving us to guess, and
 * the distinction matters: a spun-down disk and a USB stick with no S.M.A.R.T.
 * at all are both "expected", while a refused pass-through is actionable. A flat
 * "Temp N/A" on all three sent people hunting for a fault that wasn't there.
 */
const DP_SMART_REASON_BADGES = {
  asleep: 'Asleep',
  unsupported: 'No S.M.A.R.T.',
  usb_bridge: 'No S.M.A.R.T.',
  no_permission: 'Needs admin',
  timeout: 'No response',
  no_tool: 'Needs smartmontools',
  unreadable: 'Temp N/A'
};

function smartReasonBadge(reason) {
  return DP_SMART_REASON_BADGES[reason] || 'Temp N/A';
}

/** Shared dark-theme tooltip styling, so every chart on the page reads the same. */
const DP_TOOLTIP = {
  backgroundColor: '#0f172a',
  titleColor: '#f8fafc',
  bodyColor: '#cbd5e1',
  borderColor: 'rgba(255,255,255,0.1)',
  borderWidth: 1
};

/**
 * Normalise a latency sample list to plain milliseconds.
 *
 * The live feed emits {i, ms} objects (so the sampler can number them as they
 * arrive) while the finished result stores bare floats. Both describe the same
 * round-trips, and the chart shouldn't care which one it was handed.
 */
function pingMillis(samples) {
  if (!Array.isArray(samples)) return [];
  return samples
    .map(s => (typeof s === 'number' ? s : Number(s && s.ms)))
    .filter(v => Number.isFinite(v));
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/**
 * Jitter: the mean gap between *consecutive* round-trips, in arrival order.
 *
 * Same definition the backend uses (RFC 3550's approach), so a value computed
 * here mid-run is comparable with the one that lands when the run finishes.
 * Sorting the samples first would collapse it toward zero.
 */
function jitterOf(values) {
  if (values.length < 2) return null;
  let total = 0;
  for (let i = 1; i < values.length; i++) total += Math.abs(values[i] - values[i - 1]);
  return Number((total / (values.length - 1)).toFixed(2));
}

/** How often to re-read a running test. The backend samples every 100 ms, so a
 *  slower poll delivers the trace in unreadable jumps; the payload is a few KB. */
const DP_SPEED_POLL_MS = 500;

class DashboardVisualizer {
  constructor() {
    this.chartDiskIO = null;
    this.chartCategories = null;
    this.chartSpeedLive = null;
    this.chartSpeedPing = null;
    this.chartSpeedHistory = null;
    // Poll timer for a running speed test, and the guard that keeps exactly one.
    this._speedPoll = null;
    this._speedPolling = false;
    
    // 30 seconds rolling buffer
    this.ioHistoryMax = 30;
    this.ioLabels = Array(this.ioHistoryMax).fill('');
    this.ioReadData = Array(this.ioHistoryMax).fill(0);
    this.ioWriteData = Array(this.ioHistoryMax).fill(0);
    
    this.initCharts();
    this.bindEvents();
    this.initSpeedTest();
  }

  initCharts() {
    // 1. Rolling Disk I/O Line Chart
    const ctxIO = document.getElementById('chart-disk-io')?.getContext('2d');
    if (ctxIO) {
      this.chartDiskIO = new Chart(ctxIO, {
        type: 'line',
        data: {
          labels: this.ioLabels,
          datasets: [
            {
              label: 'Read MB/s',
              data: this.ioReadData,
              borderColor: '#00f2fe',
              backgroundColor: 'rgba(0, 242, 254, 0.1)',
              borderWidth: 2,
              pointRadius: 0,
              tension: 0.35,
              fill: true
            },
            {
              label: 'Write MB/s',
              data: this.ioWriteData,
              borderColor: '#10b981',
              backgroundColor: 'rgba(16, 185, 129, 0.1)',
              borderWidth: 2,
              pointRadius: 0,
              tension: 0.35,
              fill: true
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 0 },
          plugins: {
            legend: {
              labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } }
            },
            tooltip: {
              mode: 'index',
              intersect: false,
              backgroundColor: '#0f172a',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: 'rgba(255,255,255,0.1)',
              borderWidth: 1
            }
          },
          scales: {
            x: {
              grid: { color: 'rgba(255, 255, 255, 0.04)' },
              ticks: { display: false }
            },
            y: {
              beginAtZero: true,
              grid: { color: 'rgba(255, 255, 255, 0.06)' },
              ticks: { color: '#64748b', font: { size: 10 } }
            }
          }
        }
      });
    }

    // 2. Storage Category Doughnut Chart
    const ctxCat = document.getElementById('chart-storage-categories')?.getContext('2d');
    if (ctxCat) {
      this.chartCategories = new Chart(ctxCat, {
        type: 'doughnut',
        data: {
          labels: ['Media', 'ISOs', 'Documents', 'Software', 'Backups', 'Other'],
          datasets: [{
            data: [35, 25, 15, 12, 8, 5],
            backgroundColor: [
              '#00f2fe',
              '#8b5cf6',
              '#10b981',
              '#f59e0b',
              '#f43f5e',
              '#64748b'
            ],
            borderWidth: 0,
            hoverOffset: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '72%',
          plugins: {
            legend: {
              position: 'right',
              labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, boxWidth: 10, padding: 8 }
            },
            tooltip: {
              backgroundColor: '#0f172a',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: 'rgba(255,255,255,0.1)',
              borderWidth: 1,
              callbacks: {
                label: (ctx) => {
                  const bytes = ctx.parsed || 0;
                  const data = (ctx.dataset && ctx.dataset.data) || [];
                  const total = data.reduce((sum, v) => sum + (Number(v) || 0), 0);
                  const pct = total > 0 ? ((bytes / total) * 100).toFixed(1) : '0.0';
                  return ` ${ctx.label}: ${formatStorageSize(bytes)} (${pct}%)`;
                }
              }
            }
          }
        }
      });
    }
  }

  bindEvents() {
    api.on('telemetry:status', ({ connected }) => {
      const statusEl = document.getElementById('live-telemetry-status');
      if (statusEl) {
        statusEl.textContent = connected ? 'STREAMING LIVE' : 'DISCONNECTED';
        statusEl.parentElement.style.borderColor = connected ? 'rgba(16, 185, 129, 0.3)' : 'rgba(244, 63, 94, 0.3)';
      }
    });

    api.on('telemetry:data', (data) => this.renderTelemetry(data));

    // Topbar LAN IP Badge click to copy
    const lanBadge = document.getElementById('topbar-lan-badge');
    if (lanBadge) {
      lanBadge.addEventListener('click', async () => {
        const url = window.DISKPULSE_LAN_URL || (document.getElementById('topbar-lan-ip')?.textContent ? `http://${document.getElementById('topbar-lan-ip').textContent}` : window.location.origin);
        try {
          await navigator.clipboard.writeText(url);
          const icon = document.getElementById('topbar-lan-copy-icon');
          if (icon) {
            icon.setAttribute('data-lucide', 'check');
            if (window.lucide) lucide.createIcons();
            setTimeout(() => {
              icon.setAttribute('data-lucide', 'copy');
              if (window.lucide) lucide.createIcons();
            }, 2000);
          }
        } catch (e) {
          prompt('Copy LAN URL:', url);
        }
      });
    }
  }

  renderTelemetry(data) {
    if (!data) return;

    // 1. Header & Host Info + LAN IP
    const hostEl = document.getElementById('sidebar-hostname');
    const uptimeEl = document.getElementById('sidebar-uptime');
    if (hostEl && data.system) hostEl.textContent = data.system.hostname;
    if (uptimeEl && data.system) {
      const secs = data.system.uptime_seconds;
      uptimeEl.textContent = `Up: ${secs != null ? formatUptime(secs) : (data.system.uptime_human || '--')}`;
    }

    if (data.system && data.system.local_ip) {
      window.DISKPULSE_LAN_URL = data.system.local_url || `http://${data.system.local_ip}:${data.system.local_port || 8000}`;
      const lanBadge = document.getElementById('topbar-lan-badge');
      const lanText = document.getElementById('topbar-lan-ip');
      if (lanBadge && lanText) {
        lanText.textContent = `${data.system.local_ip}:${data.system.local_port || 8000}`;
        lanBadge.style.display = 'inline-flex';
      }
    }

    // 2. Storage Pool Overview
    const pool = data.storage_pool;
    if (pool) {
      document.getElementById('dash-pool-used').textContent = pool.used_human;
      document.getElementById('dash-pool-free').textContent = pool.free_human;
      document.getElementById('dash-pool-total').textContent = pool.total_human;
      document.getElementById('dash-pool-percent').textContent = `${pool.percent}%`;
      document.getElementById('dash-pool-bar').style.width = `${pool.percent}%`;

      document.getElementById('sidebar-pool-text').textContent = `${pool.percent}%`;
      document.getElementById('sidebar-pool-bar').style.width = `${pool.percent}%`;

      // Used / total under the bar. Formatted client-side from the raw byte
      // counts so the units adapt (MB → GB → TB) instead of being fixed.
      // Free space isn't shown inline any more, so both tooltips carry it.
      const usedEl = document.getElementById('sidebar-pool-used');
      const totalEl = document.getElementById('sidebar-pool-total');
      if (usedEl) {
        usedEl.textContent = pool.used_bytes != null ? formatSize(pool.used_bytes) : (pool.used_human || '--');
        usedEl.title = pool.used_bytes != null ? `${pool.used_bytes.toLocaleString()} bytes used` : '';
      }
      if (totalEl) {
        totalEl.textContent = pool.total_bytes != null ? formatSize(pool.total_bytes) : (pool.total_human || '--');
        totalEl.title = pool.total_bytes != null
          ? `${formatSize(pool.free_bytes)} free of ${pool.total_bytes.toLocaleString()} bytes · ${pool.root_path || ''}`.trim()
          : '';
      }

      // Update Categories Doughnut
      if (this.chartCategories && pool.categories) {
        this.chartCategories.data.labels = pool.categories.map(c => c.name);
        this.chartCategories.data.datasets[0].data = pool.categories.map(c => c.size_bytes);
        this.chartCategories.update();
      }
    }

    // 3. Disk I/O & IOPS
    const diskIo = data.disk_io;
    if (diskIo) {
      const readMb = (diskIo.read_bytes_sec / (1024 * 1024));
      const writeMb = (diskIo.write_bytes_sec / (1024 * 1024));
      
      document.getElementById('dash-disk-iops').textContent = `${diskIo.total_iops} IOPS`;
      document.getElementById('dash-disk-read').textContent = diskIo.read_human_sec;
      document.getElementById('dash-disk-write').textContent = diskIo.write_human_sec;
      document.getElementById('dash-disk-rw').textContent = `${(readMb + writeMb).toFixed(1)} MB/s`;
      const rwBar = document.getElementById('dash-disk-rw-bar');
      if (rwBar) {
        // Cap visual scale at 100 MB/s for the mini gauge
        const rwPct = Math.min(100, ((readMb + writeMb) / 100) * 100);
        rwBar.style.width = `${Math.max(rwPct > 0 ? 3 : 0, rwPct)}%`;
      }

      // Push into Rolling Chart
      if (this.chartDiskIO) {
        this.ioReadData.shift();
        this.ioReadData.push(readMb);
        this.ioWriteData.shift();
        this.ioWriteData.push(writeMb);
        this.chartDiskIO.update('none');
      }
    }

    // 4. CPU Metrics & Per-Core Visualizer
    const cpu = data.cpu;
    if (cpu) {
      document.getElementById('dash-cpu-percent').textContent = `${cpu.percent_total.toFixed(1)}%`;
      document.getElementById('dash-cpu-cores').textContent = `${cpu.cores_logical} Cores`;
      document.getElementById('dash-cpu-freq').textContent = `${cpu.freq_current_mhz} MHz`;

      const tempEl = document.getElementById('dash-cpu-temp');
      if (tempEl) {
        const temp = cpu.temp_c;
        if (temp !== null && temp !== undefined) {
          tempEl.textContent = `${temp.toFixed(1)}°C`;
          let color = 'var(--accent-emerald)';
          if (temp >= 90) color = 'var(--accent-rose)';
          else if (temp >= 75) color = 'var(--accent-amber)';
          tempEl.style.color = color;
          tempEl.style.display = 'inline';
        } else {
          tempEl.textContent = 'N/A';
          tempEl.style.color = 'var(--text-dim)';
          tempEl.style.display = 'inline';
        }
      }

      const coresContainer = document.getElementById('dash-cpu-cores-list');
      if (coresContainer && cpu.per_core) {
        coresContainer.innerHTML = cpu.per_core.map((pct, idx) => `
          <div class="core-chip">
            <div class="core-name">C${idx}</div>
            <div class="core-pct">${pct.toFixed(0)}%</div>
            <div class="core-bar">
              <div class="core-bar-fill" style="width: ${pct}%;"></div>
            </div>
          </div>
        `).join('');
      }
    }

    // 5. Memory RAM Metrics
    const mem = data.memory;
    if (mem) {
      document.getElementById('dash-ram-used').textContent = mem.used_human;
      document.getElementById('dash-ram-avail').textContent = mem.available_human;
      document.getElementById('dash-ram-total').textContent = mem.total_human;
      document.getElementById('dash-ram-percent').textContent = `${mem.percent.toFixed(1)}%`;
      document.getElementById('dash-ram-bar').style.width = `${mem.percent}%`;
    }

    // 6. S.M.A.R.T. Drive Health & Temperature Cards (real hardware data)
    const drivesGrid = document.getElementById('dash-drives-grid');
    if (drivesGrid && data.smart_drives) {
      if (data.smart_drives.length === 0) {
        drivesGrid.innerHTML = `<div class="drive-card" style="grid-column: 1 / -1; text-align: center; color: var(--text-dim);">No drives detected.</div>`;
      } else {
      drivesGrid.innerHTML = data.smart_drives.map(drive => {
        const tempKnown = drive.temperature_c !== null && drive.temperature_c !== undefined;
        const healthKnown = drive.health_percent !== null && drive.health_percent !== undefined;
        const pohKnown = drive.power_on_hours !== null && drive.power_on_hours !== undefined;

        let badgeClass = 'badge-normal';
        if (drive.temp_status === 'Warning') badgeClass = 'badge-warning';
        else if (drive.temp_status === 'Critical') badgeClass = 'badge-critical';

        const tempTxt = tempKnown ? `${drive.temperature_c}°C` : 'N/A';
        const badgeTxt = tempKnown
          ? `${tempTxt} ${drive.temp_status}`
          : (drive.temp_status === 'Unknown'
              ? smartReasonBadge(drive.smart_reason)
              : drive.temp_status);
        const badgeStyle = (drive.temp_status === 'Unknown')
          ? 'style="background: rgba(100,116,139,0.15); color: var(--text-dim);"'
          : '';

        let statusColor = 'var(--accent-emerald)';
        if (drive.status === 'Warning') statusColor = 'var(--accent-amber)';
        else if (drive.status === 'Failing') statusColor = 'var(--accent-rose)';
        else if (drive.status === 'Unknown' || drive.status === 'Asleep') statusColor = 'var(--text-dim)';

        const healthTxt = healthKnown ? `${drive.health_percent}%` : '—';
        const healthWidth = healthKnown ? drive.health_percent : 0;
        // Health bar/% follow the S.M.A.R.T. status, so a Warning/Failing
        // drive never shows a green bar next to an amber/red label.
        let healthColor = 'var(--accent-emerald)';
        let healthBarBg = 'var(--grad-emerald)';
        if (drive.status === 'Warning') {
          healthColor = 'var(--accent-amber)';
          healthBarBg = 'var(--grad-amber)';
        } else if (drive.status === 'Failing') {
          healthColor = 'var(--accent-rose)';
          healthBarBg = 'var(--grad-rose)';
        } else if (!healthKnown) {
          healthColor = 'var(--text-dim)';
        }

        const reallocTxt = (drive.reallocated_sectors && drive.reallocated_sectors > 0)
          ? ` · <span style="color: var(--accent-amber);">${drive.reallocated_sectors} reallocated sectors</span>`
          : '';

        let pohTxt = '—';
        if (pohKnown) {
          const hrs = drive.power_on_hours;
          pohTxt = hrs < 24 ? `${hrs}h` : `${(hrs / 24).toFixed(0)}d`;
        }

        // Subtitle: media type · capacity · interface (only real, known bits)
        const subBits = [];
        if (drive.media_type && drive.media_type !== 'Unknown') subBits.push(drive.media_type);
        if (drive.capacity_human && drive.capacity_human !== '—') subBits.push(drive.capacity_human);
        if (drive.interface && drive.interface !== '—') subBits.push(drive.interface);
        const subLine = subBits.join(' · ');

        return `
          <div class="drive-card"${drive.note ? ` title="${drive.note}"` : ''}>
            <div class="drive-header">
              <div>
                <div class="drive-name">${drive.name}</div>
                <div style="font-size: 0.75rem; color: var(--text-dim);">${subLine ? subLine + ' — ' : ''}S.M.A.R.T.: <strong style="color: ${statusColor};">${drive.status}</strong>${reallocTxt}</div>
              </div>
              <span class="drive-badge ${badgeClass}"${badgeStyle ? ' ' + badgeStyle : ''}>${badgeTxt}</span>
            </div>

            <div class="progress-mini" style="height: 4px;">
              <div class="progress-mini-bar" style="width: ${healthWidth}%; background: ${healthBarBg};"></div>
            </div>

            <div class="drive-metrics">
              <div>
                <div class="drive-metric-val" style="color: ${healthColor};">${healthTxt}</div>
                <div class="drive-metric-lbl">Health</div>
              </div>
              <div>
                <div class="drive-metric-val" style="color: var(--accent-cyan);">${tempTxt}</div>
                <div class="drive-metric-lbl">Temp</div>
              </div>
              <div>
                <div class="drive-metric-val" style="color: var(--accent-violet);">${pohTxt}</div>
                <div class="drive-metric-lbl">Power-On</div>
              </div>
            </div>
          </div>
        `;
      }).join('');
      }
    }

    // 7. Active Partition Mounts Table
    const tbody = document.getElementById('dash-partitions-tbody');
    if (tbody && data.partitions) {
      tbody.innerHTML = data.partitions.map(p => `
        <tr>
          <td><strong style="color: #fff;"><i data-lucide="hard-drive" style="width: 14px; height: 14px; display: inline-block; vertical-align: middle;"></i> ${p.mountpoint}</strong></td>
          <td><code>${p.device}</code></td>
          <td><span class="nav-badge">${p.fstype}</span></td>
          <td>${p.used_human} / ${p.total_human}</td>
          <td style="min-width: 120px;">
            <div class="progress-mini" style="height: 6px;">
              <div class="progress-mini-bar" style="width: ${p.percent}%; ${p.percent > 90 ? 'background: var(--grad-rose);' : ''}"></div>
            </div>
            <div style="font-size: 0.7rem; color: var(--text-dim); margin-top: 2px;">${p.percent}% used</div>
          </td>
          <td style="color: var(--accent-emerald); font-weight: 600;">${p.free_human}</td>
        </tr>
      `).join('');
      if (window.lucide) lucide.createIcons();
    }
  }

  // Speed Test Methods
  initSpeedTest() {
    const btnRun = document.getElementById('btn-run-speedtest');
    btnRun?.addEventListener('click', () => this.handleRunSpeedTest());

    this.initSpeedTestCharts();

    // Initial load of latest speed test result
    this.loadSpeedTestStatus();
  }

  /**
   * Build the three speed-test charts once, empty.
   *
   * They're created up front and then fed with .update('none') so a running test
   * animates smoothly instead of tearing down and rebuilding a canvas twice a
   * second — which is what makes the throughput line look like a live trace.
   */
  initSpeedTestCharts() {
    // 1. Throughput over time. Download and upload are separate datasets on one
    // linear time axis, with upload shifted past the end of the download, so the
    // chart reads as a single continuous transfer: ramp, plateau, switch, ramp.
    const ctxLive = document.getElementById('chart-speedtest-live')?.getContext('2d');
    if (ctxLive) {
      this.chartSpeedLive = new Chart(ctxLive, {
        type: 'line',
        data: {
          datasets: [
            {
              label: 'Download Mbps',
              data: [],
              borderColor: '#00f2fe',
              backgroundColor: 'rgba(0, 242, 254, 0.12)',
              borderWidth: 2,
              pointRadius: 0,
              tension: 0.3,
              fill: true
            },
            {
              label: 'Upload Mbps',
              data: [],
              borderColor: '#10b981',
              backgroundColor: 'rgba(16, 185, 129, 0.12)',
              borderWidth: 2,
              pointRadius: 0,
              tension: 0.3,
              fill: true
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 0 },
          interaction: { mode: 'nearest', intersect: false },
          plugins: {
            legend: { labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, boxWidth: 12 } },
            tooltip: {
              ...DP_TOOLTIP,
              callbacks: {
                title: (items) => `${(items[0]?.parsed.x ?? 0).toFixed(1)}s`,
                label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)} Mbps`
              }
            }
          },
          scales: {
            x: {
              type: 'linear',
              min: 0,
              grid: { color: 'rgba(255, 255, 255, 0.04)' },
              ticks: {
                color: '#64748b',
                font: { size: 10 },
                maxTicksLimit: 8,
                callback: (v) => `${v}s`
              }
            },
            y: {
              beginAtZero: true,
              grid: { color: 'rgba(255, 255, 255, 0.06)' },
              ticks: { color: '#64748b', font: { size: 10 }, callback: (v) => `${v}` },
              title: { display: true, text: 'Mbps', color: '#64748b', font: { size: 10 } }
            }
          }
        }
      });
    }

    // 2. Latency detail. Bars are individual round-trips, the dashed line is the
    // median — the gap between the two *is* the jitter, which is the number that
    // predicts a stuttering call far better than a good average ping does.
    const ctxPing = document.getElementById('chart-speedtest-ping')?.getContext('2d');
    if (ctxPing) {
      this.chartSpeedPing = new Chart(ctxPing, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [
            {
              label: 'Round-trip ms',
              data: [],
              backgroundColor: 'rgba(139, 92, 246, 0.55)',
              borderColor: '#8b5cf6',
              borderWidth: 1,
              borderRadius: 2,
              // Chart.js draws datasets in ascending `order`, so the lower number
              // goes down first and the median line lands on top of the bars —
              // which is the whole point of drawing it.
              order: 1
            },
            {
              label: 'Median',
              type: 'line',
              data: [],
              borderColor: '#f59e0b',
              borderWidth: 1.5,
              borderDash: [4, 4],
              pointRadius: 0,
              fill: false,
              order: 2
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 0 },
          plugins: {
            legend: { labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, boxWidth: 12 } },
            tooltip: {
              ...DP_TOOLTIP,
              callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${Number(ctx.parsed.y).toFixed(2)} ms` }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { display: false } },
            y: {
              beginAtZero: true,
              grid: { color: 'rgba(255, 255, 255, 0.06)' },
              ticks: { color: '#64748b', font: { size: 10 } },
              title: { display: true, text: 'ms', color: '#64748b', font: { size: 10 } }
            }
          }
        }
      });
    }

    // 3. Run history, oldest to newest. Throughput as bars and ping as a line on
    // its own axis, because the interesting question over time is whether a drop
    // in speed came with a rise in latency (congestion) or not (a capacity change).
    const ctxHist = document.getElementById('chart-speedtest-history')?.getContext('2d');
    if (ctxHist) {
      this.chartSpeedHistory = new Chart(ctxHist, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [
            {
              label: 'Down Mbps',
              data: [],
              backgroundColor: 'rgba(0, 242, 254, 0.6)',
              borderRadius: 3,
              yAxisID: 'y'
            },
            {
              label: 'Up Mbps',
              data: [],
              backgroundColor: 'rgba(16, 185, 129, 0.6)',
              borderRadius: 3,
              yAxisID: 'y'
            },
            {
              label: 'Ping ms',
              type: 'line',
              data: [],
              borderColor: '#8b5cf6',
              backgroundColor: '#8b5cf6',
              borderWidth: 2,
              pointRadius: 2,
              tension: 0.3,
              fill: false,
              yAxisID: 'y1'
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 0 },
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, boxWidth: 12 } },
            tooltip: { ...DP_TOOLTIP }
          },
          scales: {
            x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 0, autoSkipPadding: 8 } },
            y: {
              beginAtZero: true,
              position: 'left',
              grid: { color: 'rgba(255, 255, 255, 0.06)' },
              ticks: { color: '#64748b', font: { size: 10 } },
              title: { display: true, text: 'Mbps', color: '#64748b', font: { size: 10 } }
            },
            y1: {
              beginAtZero: true,
              position: 'right',
              grid: { display: false },
              ticks: { color: '#8b5cf6', font: { size: 10 } },
              title: { display: true, text: 'ms', color: '#8b5cf6', font: { size: 10 } }
            }
          }
        }
      });
    }
  }

  async loadSpeedTestStatus() {
    try {
      const res = await api.getSpeedTestLatest();
      this.renderSpeedTest(res);
      // A test started before this page loaded (or on another device) would
      // otherwise sit frozen mid-run until it happened to be reloaded again.
      if (res && res.is_running) this.pollSpeedTest();
    } catch (e) {
      console.warn('Could not load speedtest status:', e);
    }
  }

  /**
   * Follow a running test to completion.
   *
   * Each request is scheduled only after the previous one has been rendered, so
   * two slow responses can't land out of order and rewind the chart to an earlier
   * sample count — which a fixed setInterval allows as soon as one poll takes
   * longer than the interval. Guarded against a second chain, since both the
   * button and a mid-run page load can start one.
   */
  pollSpeedTest() {
    // A separate flag rather than testing the timer id: setTimeout is only
    // documented to return "a value", and a falsy id would silently let a second
    // chain start alongside the first.
    if (this._speedPolling) return;
    this._speedPolling = true;

    const btn = document.getElementById('btn-run-speedtest');
    const btnText = document.getElementById('btn-run-speedtest-text');
    if (btn) btn.disabled = true;
    if (btnText) btnText.textContent = 'Testing Speed...';

    const stop = () => {
      if (this._speedPoll) clearTimeout(this._speedPoll);
      this._speedPoll = null;
      this._speedPolling = false;
      if (btn) btn.disabled = false;
      if (btnText) btnText.textContent = 'Run Speed Test';
    };

    let errors = 0;
    const tick = async () => {
      if (!this._speedPolling) return;
      try {
        const res = await api.getSpeedTestLatest();
        errors = 0;
        this.renderSpeedTest(res);
        if (!res || !res.is_running) return stop();
      } catch (_) {
        // One dropped poll is usually a blip — a reloading proxy, a busy server —
        // not the end of the test. Abandoning the run on the first failure left
        // the card frozen mid-transfer with the button stuck on "Testing Speed...".
        if (++errors >= 4) return stop();
      }
      this._speedPoll = setTimeout(tick, DP_SPEED_POLL_MS);
    };

    this._speedPoll = setTimeout(tick, DP_SPEED_POLL_MS);
  }

  async handleRunSpeedTest() {
    const btn = document.getElementById('btn-run-speedtest');
    const btnText = document.getElementById('btn-run-speedtest-text');
    const badge = document.getElementById('speedtest-status-badge');

    if (btn) btn.disabled = true;
    if (btnText) btnText.textContent = 'Testing Speed...';
    if (badge) {
      badge.textContent = 'TESTING...';
      badge.style.background = 'rgba(245,158,11,0.15)';
      badge.style.color = 'var(--accent-amber)';
    }

    try {
      await api.runSpeedTest();
      this.pollSpeedTest();
    } catch (err) {
      alert(`Speed test failed: ${err.message}`);
      if (btn) btn.disabled = false;
      if (btnText) btnText.textContent = 'Run Speed Test';
    }
  }

  renderSpeedTest(data) {
    if (!data) return;

    const latest = data.latest || data;
    const isRunning = data.is_running;
    const badge = document.getElementById('speedtest-status-badge');

    if (badge) {
      if (isRunning) {
        // Say which stage we're in rather than a flat "TESTING..." — an 8 s
        // download and a 5 s upload otherwise look like one long unexplained wait.
        const phase = (data.live || {}).phase;
        const stage = { latency: 'PING', download: 'DOWNLOAD', upload: 'UPLOAD', connecting: 'CONNECTING' }[phase];
        badge.textContent = stage ? `TESTING · ${stage}` : 'TESTING...';
        badge.style.background = 'rgba(245,158,11,0.15)';
        badge.style.color = 'var(--accent-amber)';
      } else if (latest.status === 'completed') {
        badge.textContent = 'ONLINE & TESTED';
        badge.style.background = 'rgba(16,185,129,0.15)';
        badge.style.color = 'var(--accent-emerald)';
      } else if (latest.status === 'error') {
        badge.textContent = 'TEST ERROR';
        badge.style.background = 'rgba(244,63,94,0.15)';
        badge.style.color = 'var(--accent-rose)';
      } else {
        badge.textContent = 'READY';
        badge.style.background = 'rgba(56,189,248,0.15)';
        badge.style.color = 'var(--accent-blue)';
      }
    }

    const dlVal = document.getElementById('st-download-val');
    const ulVal = document.getElementById('st-upload-val');
    const pingVal = document.getElementById('st-ping-val');
    const ispVal = document.getElementById('st-isp-val');
    const serverVal = document.getElementById('st-server-val');
    const lastTestedVal = document.getElementById('st-last-tested');
    const clientIpVal = document.getElementById('st-client-ip');

    if (dlVal && latest.download_mbps !== undefined) {
      dlVal.innerHTML = `${latest.download_mbps} <span style="font-size: 0.85rem; font-weight: 500;">Mbps</span>`;
    }
    if (ulVal && latest.upload_mbps !== undefined) {
      ulVal.innerHTML = `${latest.upload_mbps} <span style="font-size: 0.85rem; font-weight: 500;">Mbps</span>`;
    }
    if (pingVal && latest.ping_ms !== undefined) {
      pingVal.innerHTML = `${latest.ping_ms} <span style="font-size: 0.85rem; font-weight: 500;">ms</span>`;
    }
    if (ispVal && latest.isp) {
      ispVal.textContent = latest.isp;
    }
    if (serverVal && latest.server) {
      const s = latest.server;
      const sName = s.name || s.sponsor || 'Default';
      const sCountry = s.country ? ` (${s.country})` : '';
      serverVal.textContent = `Server: ${sName}${sCountry}`;
    }
    if (clientIpVal && latest.client_ip) {
      clientIpVal.textContent = latest.client_ip;
    }
    if (lastTestedVal && latest.timestamp) {
      const d = new Date(latest.timestamp * 1000);
      lastTestedVal.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    this.renderSpeedProgress(data.live || {}, isRunning);
    // While a run is in flight `latest` still holds the *previous* run's samples,
    // so it must not be offered as a fallback: the chart would draw run #1's upload
    // curve alongside run #2's partial download, and slide it rightward as the new
    // download grew. Same for a failed run, whose `latest` should be blank but must
    // not be trusted to be. The status is kept so the panels can still say why the
    // chart is empty; only the measurements are withheld.
    const stale = isRunning || latest.status === 'error';
    const prev = stale ? { status: latest.status } : latest;
    this.renderSpeedLive(data.live || {}, prev);
    this.renderSpeedPing(data.live || {}, prev);
    this.renderSpeedHistory(data.history || []);
  }

  /** Phase label + progress bar. Hidden entirely when no test is in flight. */
  renderSpeedProgress(live, isRunning) {
    const row = document.getElementById('st-progress-row');
    if (!row) return;

    // Keep the bar visible for the "error" phase too: a test that died deserves to
    // say which stage it died in, rather than the row just vanishing.
    const show = isRunning || live.phase === 'error';
    row.style.display = show ? 'block' : 'none';
    if (!show) return;

    const label = document.getElementById('st-phase-label');
    const bar = document.getElementById('st-progress-bar');
    const mbps = document.getElementById('st-live-mbps');
    const elapsed = document.getElementById('st-elapsed');

    if (label) label.textContent = live.phase_label || 'Working';
    if (bar) {
      bar.style.width = `${Math.max(0, Math.min(100, Number(live.progress) || 0))}%`;
      bar.style.background = live.phase === 'error' ? 'var(--grad-rose)' : '';
    }
    // A stall is real information, so 0.0 has to be shown as 0.0. Testing the value
    // for truthiness would print "--" for it and hide the most interesting reading.
    if (mbps) {
      const rate = live.mbps == null ? NaN : Number(live.mbps);
      mbps.textContent = Number.isFinite(rate) ? rate.toFixed(1) : '--';
    }

    if (elapsed) elapsed.textContent = `${Number(live.elapsed || 0).toFixed(1)}s`;
  }

  /**
   * Plot the transfer itself.
   *
   * The backend times each phase independently, so both sample lists start at
   * t=0. Shifting upload past the end of download turns two overlapping traces
   * into one readable timeline.
   */
  renderSpeedLive(live, latest) {
    const chart = this.chartSpeedLive;
    if (!chart) return;

    // A finished run keeps its samples in `live`; `latest` is the fallback for a
    // result that arrived from somewhere else (or a reload mid-session).
    const down = (live.download_samples || []).length
      ? live.download_samples
      : (latest.download_samples || []);
    const up = (live.upload_samples || []).length
      ? live.upload_samples
      : (latest.upload_samples || []);

    const downPts = down.map(s => ({ x: Number(s.t) || 0, y: Number(s.mbps) || 0 }));
    const downEnd = downPts.length ? downPts[downPts.length - 1].x : 0;
    // Nudge the upload trace one gap past the download so the two lines never
    // share an x value and the phase boundary stays visible.
    const offset = downEnd > 0 ? downEnd + 0.2 : 0;
    const upPts = up.map(s => ({ x: offset + (Number(s.t) || 0), y: Number(s.mbps) || 0 }));

    chart.data.datasets[0].data = downPts;
    chart.data.datasets[1].data = upPts;
    chart.update('none');

    const hint = document.getElementById('st-live-hint');
    if (hint) {
      const total = downPts.length + upPts.length;
      // Each phase is timed from its own zero, so the transfer's real duration is
      // the two added together — not the shifted x of the last upload point.
      const upSeconds = up.length ? Number(up[up.length - 1].t) || 0 : 0;
      hint.textContent = total
        ? `${total} samples · ${(downEnd + upSeconds).toFixed(1)}s of transfer`
        : 'Run a test to plot the transfer';
    }
  }

  /** Round-trip bars against the median, plus the jitter/loss summary line. */
  renderSpeedPing(live, latest) {
    const chart = this.chartSpeedPing;
    const pings = pingMillis((live.ping_samples || []).length ? live.ping_samples : latest.ping_samples);

    if (chart) {
      const mid = median(pings);
      chart.data.labels = pings.map((_, i) => `#${i + 1}`);
      chart.data.datasets[0].data = pings;
      chart.data.datasets[1].data = mid === null ? [] : pings.map(() => mid);
      chart.update('none');
    }

    const summary = document.getElementById('st-jitter-summary');
    if (!summary) return;
    if (!pings.length) {
      summary.textContent = latest.status === 'error' ? 'No response from edge' : '--';
      return;
    }
    // Prefer the backend's figure, but derive one from the bars while a run is still
    // probing — the alternative is a blank jitter readout during the ping phase.
    const jitter = latest.jitter_ms ?? jitterOf(pings);
    const lo = latest.ping_min_ms ?? Math.min(...pings);
    const hi = latest.ping_max_ms ?? Math.max(...pings);
    const loss = latest.packet_loss_pct;
    const bits = [];
    if (jitter != null) bits.push(`Jitter ${Number(jitter).toFixed(2)} ms`);
    bits.push(`${Number(lo).toFixed(0)}–${Number(hi).toFixed(0)} ms`);
    if (loss) bits.push(`${loss}% loss`);
    summary.textContent = bits.join(' · ');
  }

  /** Past runs, oldest to newest — the trend the single latest number can't show. */
  renderSpeedHistory(history) {
    const chart = this.chartSpeedHistory;
    // The backend keeps 30 runs; 12 is as many bars as fit legibly in this card.
    const runs = history.slice(-12);

    if (chart) {
      chart.data.labels = runs.map(r => {
        if (!r.timestamp) return '--';
        const d = new Date(r.timestamp * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      });
      chart.data.datasets[0].data = runs.map(r => Number(r.download_mbps) || 0);
      chart.data.datasets[1].data = runs.map(r => Number(r.upload_mbps) || 0);
      chart.data.datasets[2].data = runs.map(r => (r.ping_ms == null ? null : Number(r.ping_ms)));
      chart.update('none');
    }

    const summary = document.getElementById('st-history-summary');
    if (!summary) return;
    if (!history.length) {
      summary.textContent = 'No previous runs';
      return;
    }
    const downs = history.map(r => Number(r.download_mbps)).filter(Number.isFinite);
    const avg = downs.length ? downs.reduce((a, b) => a + b, 0) / downs.length : null;
    summary.textContent = `${history.length} run${history.length === 1 ? '' : 's'}`
      + (avg !== null ? ` · avg ${avg.toFixed(1)} Mbps down` : '');
  }
}

const dashboardVisualizer = new DashboardVisualizer();
