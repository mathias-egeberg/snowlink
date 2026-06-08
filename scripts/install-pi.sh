#!/usr/bin/env bash
# SnowLink – Raspberry Pi installation script
#
# Installs system dependencies, creates the Python virtual environment,
# installs Python packages, and optionally sets up a systemd autostart unit.
#
# Run once after cloning the repository:
#   bash scripts/install-pi.sh
#
# Re-run at any time to update dependencies.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== SnowLink Pi Install ==="
echo "Project: $PROJECT_DIR"
echo ""

# ── System packages ───────────────────────────────────────────────────────
echo "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    chromium-browser \
    python3-venv \
    python3-pip \
    curl \
    libssl-dev

# ── Python virtual environment ────────────────────────────────────────────
if [ ! -f "venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing backend dependencies..."
pip install --upgrade pip --quiet
pip install -r backend/requirements.txt --quiet

# Optional Pi-specific dependencies (serial, etc.)
if [ -f "requirements-pi.txt" ]; then
    echo "Installing Pi-specific dependencies..."
    pip install -r requirements-pi.txt --quiet
fi

echo ""
echo "=== Installation complete ==="
echo ""

# ── Systemd autostart (optional) ─────────────────────────────────────────
read -r -p "Set up systemd autostart? [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    SERVICE_FILE="/etc/systemd/system/snowlink.service"
    USER="$(whoami)"
    echo "Writing $SERVICE_FILE ..."
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=SnowLink Kiosk
After=network.target graphical-session.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=/bin/bash $PROJECT_DIR/scripts/run-pi-kiosk.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable snowlink.service
    echo "Autostart enabled.  Start now: sudo systemctl start snowlink"
else
    echo ""
    echo "To start manually:"
    echo "  bash scripts/run-pi-kiosk.sh"
fi
