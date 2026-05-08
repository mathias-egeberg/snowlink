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

echo ">>> Creating autostart entry..."
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/snowlink.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SnowLink
Exec=/bin/bash -c "cd $(pwd) && source venv/bin/activate && python main.py"
EOF

echo ""
echo "✅ Setup complete!"
echo "   Start manually : source venv/bin/activate && python main.py"
echo "   Autostart added: $AUTOSTART_DIR/snowlink.desktop"
