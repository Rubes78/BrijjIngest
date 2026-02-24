#!/usr/bin/env bash
# =============================================================================
# BrijjIngest Installer
# Supports: Debian 11/12, Ubuntu 20.04/22.04/24.04
#
# Usage:
#   sudo ./install.sh
#
# Environment overrides:
#   INSTALL_DIR   — where to install (default: /opt/brijjingest)
#   BRIJJ_PORT    — web UI port      (default: 5000)
#   BRIJJ_HOST    — bind address     (default: 0.0.0.0)
# =============================================================================
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/brijjingest}"
SERVICE_NAME="brijjdata"
BRIJJ_PORT="${BRIJJ_PORT:-5000}"
BRIJJ_HOST="${BRIJJ_HOST:-0.0.0.0}"
REPO="https://github.com/Rubes78/BrijjIngest.git"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root:  sudo ./install.sh"

# ── Detect OS ─────────────────────────────────────────────────────────────────
header "Detecting OS"
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID}"          # debian | ubuntu
    OS_VER="${VERSION_ID}" # 11 | 12 | 20.04 | 22.04 | 24.04
else
    die "Cannot detect OS — /etc/os-release not found."
fi

case "${OS_ID}:${OS_VER}" in
    debian:11)  MSSQL_CFG="debian/11" ;;
    debian:12)  MSSQL_CFG="debian/12" ;;
    ubuntu:20.04) MSSQL_CFG="ubuntu/20.04" ;;
    ubuntu:22.04) MSSQL_CFG="ubuntu/22.04" ;;
    ubuntu:24.04) MSSQL_CFG="ubuntu/24.04" ;;
    *) die "Unsupported OS: ${OS_ID} ${OS_VER}. Supported: Debian 11/12, Ubuntu 20.04/22.04/24.04" ;;
esac
ok "Detected: ${PRETTY_NAME}"

# ── System packages ───────────────────────────────────────────────────────────
header "Installing system packages"
apt-get update -q
apt-get install -y -q \
    python3 python3-venv python3-pip \
    unixodbc-dev \
    curl git ca-certificates gnupg
ok "System packages ready"

# ── Microsoft ODBC Driver 18 ──────────────────────────────────────────────────
header "Checking ODBC Driver 18 for SQL Server"
if odbcinst -q -d 2>/dev/null | grep -q "ODBC Driver 18"; then
    ok "ODBC Driver 18 already installed — skipping"
else
    info "Installing Microsoft ODBC Driver 18..."
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg

    curl -fsSL "https://packages.microsoft.com/config/${MSSQL_CFG}/prod.list" \
        -o /etc/apt/sources.list.d/mssql-release.list

    apt-get update -q
    ACCEPT_EULA=Y apt-get install -y -q msodbcsql18
    ok "ODBC Driver 18 installed"
fi

# ── Clone or update repo ──────────────────────────────────────────────────────
header "Setting up application in ${INSTALL_DIR}"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "Repo already exists — pulling latest..."
    git -C "${INSTALL_DIR}" pull --ff-only
    ok "Repository updated"
elif [[ "$(realpath "$(dirname "$0")")" == "$(realpath "${INSTALL_DIR}")" ]]; then
    ok "Running from install directory — no clone needed"
else
    info "Cloning from ${REPO}..."
    git clone "${REPO}" "${INSTALL_DIR}"
    ok "Repository cloned to ${INSTALL_DIR}"
fi

# ── Python venv ───────────────────────────────────────────────────────────────
header "Setting up Python environment"
VENV="${INSTALL_DIR}/venv"
if [[ ! -d "${VENV}" ]]; then
    python3 -m venv "${VENV}"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

info "Installing Python dependencies..."
"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
ok "Python dependencies installed"

# ── Config file ───────────────────────────────────────────────────────────────
header "Configuration"
CONFIG="${INSTALL_DIR}/config.ini"
if [[ -f "${CONFIG}" ]]; then
    ok "config.ini already exists — leaving untouched"
else
    cp "${INSTALL_DIR}/config.ini.example" "${CONFIG}"
    warn "Created config.ini from example — edit it before starting the service:"
    warn "  ${CONFIG}"
fi

# ── systemd service ───────────────────────────────────────────────────────────
header "Installing systemd service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=BrijjData Configuration UI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV}/bin/python ${INSTALL_DIR}/web_config.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
Environment=BRIJJ_HOST=${BRIJJ_HOST}
Environment=BRIJJ_PORT=${BRIJJ_PORT}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
ok "Service installed and enabled"

# ── Start / restart ───────────────────────────────────────────────────────────
header "Starting service"
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl restart "${SERVICE_NAME}"
    ok "Service restarted"
else
    if [[ -f "${CONFIG}" ]] && grep -q "YOUR_SQL_SERVER_HOST" "${CONFIG}"; then
        warn "Service NOT started — config.ini still has placeholder values."
        warn "Edit ${CONFIG} then run:  sudo systemctl start ${SERVICE_NAME}"
    else
        systemctl start "${SERVICE_NAME}"
        ok "Service started"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
IFACE_IP=$(hostname -I | awk '{print $1}')
echo
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        BrijjIngest — Installation Complete   ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo
echo -e "  Install dir : ${INSTALL_DIR}"
echo -e "  Config file : ${CONFIG}"
echo -e "  Service     : ${SERVICE_NAME}  (systemctl start|stop|status ${SERVICE_NAME})"
echo -e "  Web UI      : ${GREEN}http://${IFACE_IP}:${BRIJJ_PORT}${NC}"
echo
if grep -q "YOUR_SQL_SERVER_HOST" "${CONFIG}" 2>/dev/null; then
    echo -e "  ${YELLOW}Next step: edit config.ini with your database and API credentials,${NC}"
    echo -e "  ${YELLOW}then run:  sudo systemctl start ${SERVICE_NAME}${NC}"
fi
echo
