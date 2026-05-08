#!/usr/bin/env bash
# ============================================================
# SnowLink – Raspberry Pi setup script
# Run once: chmod +x setup.sh && ./setup.sh
# ============================================================
set -e

echo ">>> Updating system packages..."
sudo apt-get update -y
sudo apt-get install -y \
    python3-pip python3-venv \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
    libportmidi-dev libswscale-dev libavformat-dev libavcodec-dev \
    zlib1g-dev libgstreamer1.0-dev gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good libmtdev-dev xclip xsel

echo ">>> Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo ">>> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements-pi.txt

# ── Shared .desktop content ──────────────────────────────────────────────────
SNOWLINK_DIR="$(pwd)"
DESKTOP_CONTENT="[Desktop Entry]
Version=1.0
Type=Application
Name=SnowLink
Comment=Snow and ice management system
Exec=/bin/bash -c 'cd $SNOWLINK_DIR && source venv/bin/activate && SNOWLINK_FULLSCREEN=1 python main.py'
Terminal=false
StartupNotify=false
Categories=Utility;"

echo ">>> Creating autostart entry..."
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
echo "$DESKTOP_CONTENT" > "$AUTOSTART_DIR/snowlink.desktop"

echo ">>> Creating desktop icon..."
DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"
echo "$DESKTOP_CONTENT" > "$DESKTOP_DIR/snowlink.desktop"
chmod +x "$DESKTOP_DIR/snowlink.desktop"

# Trust the icon on GNOME (silently ignores failure on non-GNOME)
gio set "$DESKTOP_DIR/snowlink.desktop" metadata::trusted true 2>/dev/null || true

echo ""
echo "✅ Setup complete!"
echo "   Start manually : source venv/bin/activate && SNOWLINK_FULLSCREEN=1 python main.py"
echo "   Autostart added: $AUTOSTART_DIR/snowlink.desktop"
echo "   Desktop icon  : $DESKTOP_DIR/snowlink.desktop"
