#!/usr/bin/env bash
# ============================================================================
# DiskPulse NAS — Linux launcher
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"
REQUIREMENTS="requirements.txt"
ENTRY_POINT="run.py"
PIDFILE="$SCRIPT_DIR/.diskpulse.pid"
LOGFILE="$SCRIPT_DIR/.diskpulse.log"
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="8000"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }
banner() {
  echo -e "${CYAN}${BOLD}"
  echo "   ____  _     _    ____        _           "
  echo "  |  _ \\(_)___| | _|  _ \\ _   _| |___  ___ "
  echo "  | | | | / __| |/ / |_) | | | | / __|/ _ \\"
  echo "  | |_| | \\__ \\   <|  __/| |_| | \\__ \\  __/"
  echo "  |____/|_|___/_|\\_\\_|    \\__,_|_|___/\\___|"
  echo -e "${NC}"
  echo -e "${GREEN}${BOLD}=== DiskPulse NAS Storage Hub & Diagnostics Server ===${NC}"
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [COMMAND]

Commands:
  start       Start the server (default if no command given)
  stop        Stop a running server
  restart     Restart the server
  status      Show running status
  install     Install system dependencies (ffmpeg, smartmontools)
  dev         Start with auto-reload (development mode)

Options:
  -h, --help  Show this help message

Examples:
  sudo ./run.sh start          # start on 0.0.0.0:8000
  ./run.sh dev                 # dev mode with auto-reload
  ./run.sh install             # install ffmpeg + smartmontools
EOF
  exit 1
}

# ---------------------------------------------------------------------------
# Root check for install
# ---------------------------------------------------------------------------
check_root() {
  if [[ $EUID -ne 0 ]]; then
    err "This command needs root privileges. Re-run with sudo."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Detect OS / package manager
# ---------------------------------------------------------------------------
detect_pkg_mgr() {
  if command -v apt-get &>/dev/null; then
    echo "apt"
  elif command -v dnf &>/dev/null; then
    echo "dnf"
  elif command -v yum &>/dev/null; then
    echo "yum"
  elif command -v pacman &>/dev/null; then
    echo "pacman"
  elif command -v apk &>/dev/null; then
    echo "apk"
  else
    echo "unknown"
  fi
}

PKG_MGR=$(detect_pkg_mgr)

# ---------------------------------------------------------------------------
# Install system dependencies
# ---------------------------------------------------------------------------
install_deps() {
  check_root
  banner
  echo -e "${CYAN}Installing system dependencies...${NC}"

  case "$PKG_MGR" in
    apt)
      apt-get update
      apt-get install -y --no-install-recommends \
        ffmpeg \
        smartmontools \
        python3-venv \
        python3-pip \
        curl \
        gcc \
        libffi-dev \
        libssl-dev
      ;;
    dnf)
      dnf install -y \
        ffmpeg \
        smartmontools \
        python3-virtualenv \
        python3-pip \
        gcc \
        libffi-devel \
        openssl-devel
      ;;
    yum)
      yum install -y epel-release
      yum install -y \
        ffmpeg \
        smartmontools \
        python3-virtualenv \
        python3-pip \
        gcc \
        libffi-devel \
        openssl-devel
      ;;
    pacman)
      pacman -Sy --noconfirm \
        ffmpeg \
        smartmontools \
        python-virtualenv \
        base-devel \
        curl
      ;;
    apk)
      apk add --no-cache \
        ffmpeg \
        smartmontools \
        python3-dev \
        gcc \
        musl-dev \
        libffi-dev \
        openssl-dev \
        curl
      ;;
    *)
      err "Unknown package manager. Install these manually:"
      echo "  - ffmpeg"
      echo "  - smartmontools"
      echo "  - python3-venv / python3-virtualenv"
      echo "  - build-essential / gcc / base-devel"
      echo "  - libffi-dev / libffi-devel"
      echo "  - libssl-dev / openssl-devel"
      exit 1
      ;;
  esac

  info "System dependencies installed."
  echo ""
  echo -e "${CYAN}Recommended next steps:${NC}"
  echo "  1. Run: $(basename "$0") venv"
  echo "  2. Run: $(basename "$0") start"
}

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
VENV_DIR="$SCRIPT_DIR/.venv"

ensure_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    warn "No virtual environment found at .venv/"
    echo -e "${CYAN}Creating virtual environment...${NC}"
    "$PYTHON" -m venv "$VENV_DIR"
    info "Virtual environment created at $VENV_DIR"
  fi

  # Activate
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  info "Using virtual environment: $VENV_DIR"
}

pip_install() {
  ensure_venv
  echo -e "${CYAN}Installing Python dependencies...${NC}"
  pip install --upgrade pip
  pip install -r "$REQUIREMENTS"
  info "Python dependencies installed."
}

# ---------------------------------------------------------------------------
# Check Python version
# ---------------------------------------------------------------------------
check_python() {
  if ! command -v "$PYTHON" &>/dev/null; then
    err "Python 3 not found. Install Python 3.10+ first."
    exit 1
  fi

  VER=$("$PYTHON" -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
  MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info[0])")
  MINOR=$("$PYTHON" -c "import sys; print(sys.version_info[1])")

  if [[ "$MAJOR" -lt 3 ]] || [[ "$MAJOR" -eq 3 && "$MINOR" -lt 10 ]]; then
    err "Python 3.10+ required, found $VER"
    exit 1
  fi
  info "Python $VER detected."
}

# ---------------------------------------------------------------------------
# Check system tools
# ---------------------------------------------------------------------------
check_tools() {
  local missing=()

  if ! command -v ffmpeg &>/dev/null; then
    missing+=("ffmpeg")
  fi

  if ! command -v smartctl &>/dev/null; then
    missing+=("smartmontools")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    warn "Missing tools: ${missing[*]}"
    echo -e "${CYAN}Install them with:${NC}"
    echo "  $0 install"
    echo ""
    echo -e "${YELLOW}Continuing anyway — some features will be limited.${NC}"
  else
    info "System tools OK (ffmpeg, smartmontools)."
  fi
}

# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------
is_running() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return 0
    else
      rm -f "$PIDFILE"
    fi
  fi
  return 1
}

start_server() {
  local mode="${1:-prod}"
  check_python
  ensure_venv
  check_tools

  if [[ "$(is_running)" ]]; then
    warn "Server is already running (PID $(is_running))."
    echo "  Use '$0 restart' to restart it."
    exit 1
  fi

  banner

  # Environment
  export PYTHONUNBUFFERED=1
  export DISKPULSE_HOST="${DISKPULSE_HOST:-$DEFAULT_HOST}"
  export DISKPULSE_PORT="${DISKPULSE_PORT:-$DEFAULT_PORT}"

  echo -e "${CYAN}Starting DiskPulse NAS...${NC}"
  echo -e "  Host:  ${GREEN}http://$DISKPULSE_HOST:$DISKPULSE_PORT${NC}"
  echo -e "  Log:   $LOGFILE"
  echo -e "  PID:   $PIDFILE"
  echo ""

  # Build command
  local cmd
  if [[ "$mode" == "dev" ]]; then
    cmd=("$PYTHON" "-m" "uvicorn" "backend.main:app" \
      "--host" "$DISKPULSE_HOST" \
      "--port" "$DISKPULSE_PORT" \
      "--reload" \
      "--log-level" "info")
  else
    cmd=("$PYTHON" "$ENTRY_POINT")
  fi

  # Run in background, capture PID
  nohup "${cmd[@]}" > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"

  sleep 2

  if [[ "$(is_running)" ]]; then
    info "Server started (PID $(is_running))."
    echo ""
    echo -e "${BOLD}Access URLs:${NC}"
    echo -e "  Local:   ${GREEN}http://localhost:$DISKPULSE_PORT${NC}"
    if [[ "$DISKPULSE_HOST" == "0.0.0.0" ]]; then
      echo -e "  Network: ${GREEN}http://<your-ip>:$DISKPULSE_PORT${NC}"
    fi
    echo ""
    echo -e "  Run '$(basename "$0") stop' to stop."
    echo -e "  Run '$(basename "$0") logs' to view logs."
  else
    err "Server failed to start. Check $LOGFILE for details."
    rm -f "$PIDFILE"
    exit 1
  fi
}

stop_server() {
  if [[ ! "$(is_running)" ]]; then
    warn "Server is not running."
    rm -f "$PIDFILE"
    return 0
  fi

  local pid
  pid=$(is_running)
  echo -e "${CYAN}Stopping server (PID $pid)...${NC}"

  # Graceful shutdown
  kill -TERM "$pid" 2>/dev/null || true

  local i=0
  while [[ $i -lt 30 ]]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      info "Server stopped."
      rm -f "$PIDFILE"
      return 0
    fi
    sleep 1
    ((i++))
  done

  # Force kill
  warn "Server did not stop gracefully. Force killing..."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PIDFILE"
  info "Server killed."
}

show_status() {
  if [[ "$(is_running)" ]]; then
    local pid
    pid=$(is_running)
    echo -e "${GREEN}●${NC} DiskPulse NAS is ${BOLD}running${NC} (PID $pid)"
    echo ""
    echo "  Config:"
    echo "    Host:  ${DISKPULSE_HOST:-$DEFAULT_HOST}"
    echo "    Port:  ${DISKPULSE_PORT:-$DEFAULT_PORT}"
    echo "    Log:   $LOGFILE"
    echo ""
    echo "  Recent log lines:"
    tail -n 5 "$LOGFILE" 2>/dev/null | sed 's/^/    /' || echo "    (log empty or missing)"
  else
    echo -e "${RED}●${NC} DiskPulse NAS is ${BOLD}stopped${NC}"
  fi
}

show_logs() {
  if [[ -f "$LOGFILE" ]]; then
    tail -n 50 "$LOGFILE"
  else
    err "No log file found at $LOGFILE"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Systemd service generator
# ---------------------------------------------------------------------------
generate_systemd() {
  check_root
  local user="${1:-$(whoami)}"
  local workdir="$SCRIPT_DIR"
  local python_path

  if [[ -d "$VENV_DIR" ]]; then
    python_path="$VENV_DIR/bin/python"
  else
    python_path=$(command -v "$PYTHON")
  fi

  if [[ -z "$python_path" ]]; then
    err "Could not find python executable for systemd service."
    exit 1
  fi

  local service_file="/etc/systemd/system/diskpulse-nas.service"

  cat > "$service_file" <<EOF
[Unit]
Description=DiskPulse NAS Storage Hub
Documentation=https://github.com/your-repo/diskpulse-nas
After=network.target

[Service]
Type=simple
User=$user
WorkingDirectory=$workdir
ExecStart=$python_path $workdir/run.py
Restart=on-failure
RestartSec=10
Environment="PYTHONUNBUFFERED=1"
Environment="DISKPULSE_HOST=$DEFAULT_HOST"
Environment="DISKPULSE_PORT=$DEFAULT_PORT"

# Security hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable diskpulse-nas.service

  info "Systemd service created at $service_file"
  echo ""
  echo "  Start now:   sudo systemctl start diskpulse-nas"
  echo "  Enable boot: sudo systemctl enable diskpulse-nas"
  echo "  View logs:   sudo journalctl -u diskpulse-nas -f"
  echo "  Stop:        sudo systemctl stop diskpulse-nas"
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------
main() {
  # If no args, default to start
  if [[ $# -eq 0 ]]; then
    start_server prod
    exit 0
  fi

  case "${1:-}" in
    start)
      start_server "${2:-prod}"
      ;;
    stop)
      stop_server
      ;;
    restart)
      stop_server
      sleep 1
      start_server "${2:-prod}"
      ;;
    status)
      show_status
      ;;
    logs)
      show_logs
      ;;
    install)
      install_deps
      ;;
    venv)
      check_python
      pip_install
      ;;
    systemd)
      generate_systemd "${2:-}"
      ;;
    dev)
      start_server dev
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      err "Unknown command: $1"
      usage
      ;;
  esac
}

main "$@"
