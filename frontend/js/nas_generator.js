/**
 * 1-Click NAS Standalone Package & Configuration Generator
 */
class NASDeployer {
  constructor() {
    this.platformSelect = document.getElementById('deploy-platform-select');
    this.storageInput = document.getElementById('deploy-storage-path');
    this.portInput = document.getElementById('deploy-port');
    this.previewArea = document.getElementById('deploy-config-preview');
    this.downloadBtn = document.getElementById('deploy-btn-download');
    this.copyBtn = document.getElementById('deploy-btn-copy');

    this.bindEvents();
    this.updatePreview();
  }

  bindEvents() {
    [this.platformSelect, this.storageInput, this.portInput].forEach(el => {
      el?.addEventListener('change', () => this.updatePreview());
      el?.addEventListener('input', () => this.updatePreview());
    });

    this.copyBtn?.addEventListener('click', () => {
      if (this.previewArea) {
        navigator.clipboard.writeText(this.previewArea.value);
        this.copyBtn.innerHTML = '<i data-lucide="check"></i> Copied!';
        if (window.lucide) lucide.createIcons();
        setTimeout(() => {
          this.copyBtn.innerHTML = '<i data-lucide="copy"></i> Copy to Clipboard';
          if (window.lucide) lucide.createIcons();
        }, 2000);
      }
    });

    this.downloadBtn?.addEventListener('click', () => {
      const platform = this.platformSelect.value;
      const storage = encodeURIComponent(this.storageInput.value);
      const port = this.portInput.value;
      window.location.href = `${api.baseUrl}/api/nas/export?platform_type=${platform}&storage_path=${storage}&port=${port}`;
    });
  }

  updatePreview() {
    if (!this.previewArea) return;

    const platform = this.platformSelect?.value || 'docker';
    const storage = this.storageInput?.value || '/mnt/storage';
    const port = this.portInput?.value || '8000';

    let content = '';
    if (platform === 'docker') {
      content = `# DiskPulse Docker Compose Deployment
version: '3.8'

services:
  diskpulse-nas:
    image: python:3.13-slim
    container_name: diskpulse-nas
    restart: unless-stopped
    ports:
      - "${port}:8000"
    environment:
      - DISKPULSE_HOST=0.0.0.0
      - DISKPULSE_PORT=8000
      - DISKPULSE_STORAGE_ROOT=/storage
      - PYTHONUNBUFFERED=1
    volumes:
      - ${storage}:/storage
      - ./app:/app
    working_dir: /app
    command: >
      bash -c "pip install --no-cache-dir fastapi uvicorn websockets aiofiles aiohttp humanize psutil &&
               python run.py"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/telemetry')"]
      interval: 30s
      timeout: 5s
      retries: 3`;
    } else if (platform === 'systemd') {
      content = `[Unit]
Description=DiskPulse NAS Storage Hub & Monitor
After=network.target storage.mount

[Service]
Type=simple
User=root
WorkingDirectory=/opt/diskpulse
Environment=DISKPULSE_HOST=0.0.0.0
Environment=DISKPULSE_PORT=${port}
Environment=DISKPULSE_STORAGE_ROOT=${storage}
ExecStart=/usr/bin/python3 /opt/diskpulse/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target`;
    } else if (platform === 'synology') {
      content = `#!/bin/sh
# DiskPulse Synology DSM 7.x Startup Script
export DISKPULSE_HOST=0.0.0.0
export DISKPULSE_PORT=${port}
export DISKPULSE_STORAGE_ROOT="${storage}"

APP_DIR="/volume1/@appdata/diskpulse"
case "$1" in
  start)
    cd $APP_DIR && nohup python3 run.py > /var/log/diskpulse.log 2>&1 &
    ;;
  stop)
    pkill -f "run.py"
    ;;
  status)
    pgrep -f "run.py" > /dev/null && echo "Running" || echo "Stopped"
    ;;
esac
exit 0`;
    } else if (platform === 'truenas') {
      content = `# TrueNAS SCALE Custom App Manifest
apiVersion: v1
kind: Pod
metadata:
  name: diskpulse-nas
spec:
  containers:
    - name: diskpulse
      image: python:3.13-slim
      command: ["/bin/sh", "-c"]
      args:
        - "pip install fastapi uvicorn websockets aiofiles aiohttp humanize psutil && python /app/run.py"
      env:
        - name: DISKPULSE_PORT
          value: "${port}"
        - name: DISKPULSE_STORAGE_ROOT
          value: "${storage}"
      ports:
        - containerPort: ${port}
          hostPort: ${port}
      volumeMounts:
        - mountPath: "${storage}"
          name: storage-pool
  volumes:
    - name: storage-pool
      hostPath:
        path: "${storage}"`;
    } else {
      content = `#!/usr/bin/env bash
# DiskPulse 1-Click Automated Linux/NAS Installer
set -e
echo "Installing DiskPulse NAS Storage Monitor & Suite..."
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
INSTALL_DIR="/opt/diskpulse"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r . "$INSTALL_DIR/"
cd "$INSTALL_DIR"
python3 -m venv venv
./venv/bin/pip install fastapi uvicorn websockets aiofiles aiohttp humanize psutil
sudo systemctl enable --now diskpulse
echo "DiskPulse NAS running at http://localhost:${port}"`;
    }

    this.previewArea.value = content;
  }
}

const nasDeployer = new NASDeployer();

/**
 * Reset setup and redirect to the setup wizard.
 * Called by the "Reset & Reconfigure Storage" button in the NAS Deployer panel.
 */
async function deployResetSetup() {
  const ok = await confirmModal({
    title: 'Reset DiskPulse?',
    message: 'This will reset DiskPulse to the first-run setup wizard.\n\nYour existing storage files will NOT be deleted — only the configuration will be reset.\n\nContinue?',
    confirmLabel: 'Reset',
    danger: true,
  });
  if (!ok) return;

  try {
    const res = await fetch('/api/setup/reset', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      alert('Setup reset complete. The page will now reload to show the setup wizard.');
      window.location.href = '/';
    } else {
      alert('Reset failed: ' + JSON.stringify(data));
    }
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}
